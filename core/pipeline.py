"""完整流程：YouTube → 下載 → FFmpeg → 清理 Metadata → 寫入 ID3v2.3 → 驗證。

process_url() 永遠不會丟出例外，失敗時回傳 ok=False 的 JobResult，
所以批次處理中某一首失敗不會影響其他首。
"""
from __future__ import annotations

import logging
import shutil
import tempfile
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable

import config
from core import converter
from core.downloader import YouTubeDownloader, parse_youtube_url, safe_url_for_log
from core.errors import ConverterError, MetadataError, OutputFileError
from core.filename import build_basename, resolve_output_path
from core.metadata import TrackMetadata, extract_metadata, write_tags
from core.validator import validate_mp3

log = logging.getLogger(__name__)


class Step(Enum):
    FETCH_INFO = "取得影片資訊"
    DOWNLOAD = "下載音訊"
    CONVERT = "轉換 MP3"
    READ_META = "讀取 Metadata"
    FIX_META = "修正中文 Metadata"
    VALIDATE = "驗證 ID3"
    DONE = "完成"


STEPS = list(Step)


@dataclass
class ProgressEvent:
    step: Step
    status: str  # "start" | "progress" | "done" | "fail" | "skip"
    fraction: float = 0.0
    message: str = ""


ProgressHandler = Callable[[ProgressEvent], None]


@dataclass
class JobResult:
    source: str
    ok: bool = False
    output_path: Path | None = None
    metadata: TrackMetadata | None = None
    video_id: str = ""
    error: str = ""
    failed_step: Step | None = None
    validation: object | None = None  # core.validator.ValidationReport
    notes: list[str] = field(default_factory=list)

    def summary(self) -> str:
        if self.ok and self.metadata and self.output_path:
            return f"完成！\n\n歌曲：\n{self.metadata.display_name}\n\n輸出：\n{self.output_path}"
        return f"失敗：{self.error}"


def _noop(_: ProgressEvent) -> None:
    pass


class Pipeline:
    def __init__(self, profile: config.ConvertProfile, output_dir: Path,
                 downloader: YouTubeDownloader | None = None,
                 tools: converter.FFmpegTools | None = None):
        self.profile = profile
        self.output_dir = Path(output_dir).expanduser()
        self.tools = tools
        self.downloader = downloader

    # ------------------------------------------------------------------ public
    def process_url(self, url: str, on_progress: ProgressHandler = _noop) -> JobResult:
        result = JobResult(source=safe_url_for_log(url))
        log.info("=== 開始處理 URL: %s", result.source)
        step = Step.FETCH_INFO
        try:
            self._ensure_tools()
            on_progress(ProgressEvent(step, "start"))
            result.video_id = parse_youtube_url(url).video_id
            downloader = self.downloader or YouTubeDownloader(ffmpeg_location=self.tools.ffmpeg)
            info = downloader.fetch_info(url)
            log.info("video_id=%s title=%r channel=%r", info.get("id"), info.get("title"),
                     info.get("channel") or info.get("uploader"))
            on_progress(ProgressEvent(step, "done", 1.0, info.get("title") or ""))

            with tempfile.TemporaryDirectory(prefix="amazfit-") as tmp:
                step = Step.DOWNLOAD
                on_progress(ProgressEvent(step, "start"))
                audio = downloader.download_audio(
                    info, Path(tmp),
                    lambda f, msg: on_progress(ProgressEvent(Step.DOWNLOAD, "progress", f, msg)))
                log.info("download: OK (%s, %d bytes)", audio.suffix, audio.stat().st_size)
                on_progress(ProgressEvent(step, "done", 1.0))
                self._finish(audio, info, Path(tmp), result, on_progress)
        except ConverterError as e:
            self._fail(result, result.failed_step or step, e.user_message, e.detail, on_progress)
        except Exception as e:  # 最後一道防線：任何非預期錯誤都不能讓程式崩潰
            log.exception("非預期錯誤")
            self._fail(result, result.failed_step or step, "發生非預期錯誤，請查看 Log",
                       f"{type(e).__name__}: {e}", on_progress)
        return result

    def process_file(self, src: Path, info: dict, on_progress: ProgressHandler = _noop) -> JobResult:
        """把本機音訊檔走同一套流程（CLI --from-file 與測試使用）。info 模擬 yt-dlp 的欄位。"""
        src = Path(src)
        result = JobResult(source=str(src.name), video_id=str(info.get("id") or ""))
        log.info("=== 開始處理檔案: %s", src.name)
        step = Step.CONVERT
        try:
            self._ensure_tools()
            on_progress(ProgressEvent(Step.FETCH_INFO, "skip"))
            on_progress(ProgressEvent(Step.DOWNLOAD, "skip"))
            with tempfile.TemporaryDirectory(prefix="amazfit-") as tmp:
                self._finish(src, info, Path(tmp), result, on_progress)
        except ConverterError as e:
            self._fail(result, result.failed_step or step, e.user_message, e.detail, on_progress)
        except Exception as e:
            log.exception("非預期錯誤")
            self._fail(result, result.failed_step or step, "發生非預期錯誤，請查看 Log",
                       f"{type(e).__name__}: {e}", on_progress)
        return result

    # ----------------------------------------------------------------- private
    def _ensure_tools(self) -> None:
        if self.tools is None:
            self.tools = converter.check_ffmpeg()

    def _finish(self, audio: Path, info: dict, tmp: Path, result: JobResult,
                on_progress: ProgressHandler) -> None:
        def run(step: Step):
            result.failed_step = step
            on_progress(ProgressEvent(step, "start"))

        run(Step.CONVERT)
        mp3_tmp = converter.convert_to_mp3(
            audio, tmp / "converted.mp3", self.profile, self.tools,
            lambda f: on_progress(ProgressEvent(Step.CONVERT, "progress", f)))
        log.info("ffmpeg: OK (%d kbps, %d Hz, %d ch)", self.profile.bitrate_kbps,
                 self.profile.sample_rate_hz, self.profile.channels)
        on_progress(ProgressEvent(Step.CONVERT, "done", 1.0))

        run(Step.READ_META)
        meta = extract_metadata(info)
        log.info("metadata 來源=%s 原始 title=%r → title=%r artist=%r",
                 meta.source, info.get("title"), meta.title, meta.artist)
        for fix in meta.fixes:
            log.info("metadata 修正：%s", fix)
            result.notes.append(fix)
        on_progress(ProgressEvent(Step.READ_META, "done", 1.0, meta.display_name))

        run(Step.FIX_META)
        meta = write_tags(mp3_tmp, meta, self.profile)
        result.metadata = meta
        final_path = self._move_to_output(mp3_tmp, meta)
        result.output_path = final_path
        log.info("metadata: OK ID3v2.%d UTF-16 → %s", self.profile.id3_version, final_path)
        on_progress(ProgressEvent(Step.FIX_META, "done", 1.0))

        run(Step.VALIDATE)
        self._validate(final_path, meta, result)
        on_progress(ProgressEvent(Step.VALIDATE, "done", 1.0))

        result.ok = True
        result.failed_step = None
        log.info("output: %s", final_path)
        on_progress(ProgressEvent(Step.DONE, "done", 1.0, str(final_path)))

    def _validate(self, path: Path, meta: TrackMetadata, result: JobResult) -> None:
        """重新讀取剛產生的 MP3；任何一項失敗都不能顯示成功。"""
        if not self.profile.validate:
            return
        report = validate_mp3(path, meta, self.profile, self.tools)
        result.validation = report
        if not report.ok:
            failed = "；".join(f"{c.name}（{c.detail}）" for c in report.failures)
            raise MetadataError("Metadata 驗證失敗，請查看 Log", failed)
        for w in report.warnings:
            result.notes.append(f"警告：{w.name} {w.detail}")

    def _move_to_output(self, mp3_tmp: Path, meta: TrackMetadata) -> Path:
        try:
            self.output_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise OutputFileError(f"無法建立輸出資料夾：{self.output_dir}", str(e)) from e
        basename = build_basename(meta.title, meta.artist)
        final_path = resolve_output_path(self.output_dir, basename, self.profile.overwrite_existing)
        try:
            shutil.move(str(mp3_tmp), str(final_path))
        except OSError as e:
            raise OutputFileError(f"無法寫入輸出檔案：{final_path.name}", str(e)) from e
        return final_path

    @staticmethod
    def _fail(result: JobResult, step: Step, message: str, detail: str,
              on_progress: ProgressHandler) -> None:
        result.ok = False
        result.error = message
        result.failed_step = step
        log.error("失敗於「%s」：%s | %s", step.value, message, detail)
        on_progress(ProgressEvent(step, "fail", 0.0, message))
