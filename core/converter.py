"""FFmpeg：偵測、轉換成 MP3、用 ffprobe 解析。"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import config
from core.errors import ConversionError, FFmpegNotFoundError

log = logging.getLogger(__name__)


def ensure_search_path() -> None:
    """從 Finder 啟動的 .app 只有 /usr/bin:/bin，把 Homebrew 路徑補進 PATH。"""
    paths = os.environ.get("PATH", "").split(os.pathsep)
    for d in config.EXTRA_BIN_DIRS:
        if d not in paths and os.path.isdir(d):
            paths.append(d)
    os.environ["PATH"] = os.pathsep.join(p for p in paths if p)


def find_executable(name: str) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    for d in config.EXTRA_BIN_DIRS:
        candidate = Path(d) / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


@dataclass(frozen=True)
class FFmpegTools:
    ffmpeg: str
    ffprobe: str
    version: str = ""


def check_ffmpeg() -> FFmpegTools:
    """確認 ffmpeg 與 ffprobe 都存在，否則丟出帶安裝說明的錯誤。"""
    ffmpeg = find_executable("ffmpeg")
    ffprobe = find_executable("ffprobe")
    missing = [n for n, p in (("ffmpeg", ffmpeg), ("ffprobe", ffprobe)) if not p]
    if missing:
        raise FFmpegNotFoundError(config.FFMPEG_INSTALL_HINT, f"缺少：{', '.join(missing)}")
    version = ""
    try:
        out = subprocess.run([ffmpeg, "-hide_banner", "-version"], capture_output=True,
                             text=True, timeout=10).stdout
        version = out.splitlines()[0] if out else ""
    except (OSError, subprocess.SubprocessError) as e:
        raise FFmpegNotFoundError(config.FFMPEG_INSTALL_HINT, f"ffmpeg 無法執行：{e}") from e
    return FFmpegTools(ffmpeg=ffmpeg, ffprobe=ffprobe, version=version)


def probe(path: Path, ffprobe: str | None = None) -> dict:
    """ffprobe JSON 輸出；解析失敗時丟出 ConversionError。"""
    ffprobe = ffprobe or check_ffmpeg().ffprobe
    cmd = [ffprobe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=config.FFPROBE_TIMEOUT_SEC)
    except subprocess.TimeoutExpired as e:
        raise ConversionError("FFprobe 解析逾時", str(e)) from e
    if proc.returncode != 0:
        raise ConversionError("FFprobe 無法解析這個檔案",
                              proc.stderr.decode("utf-8", errors="replace").strip())
    try:
        return json.loads(proc.stdout.decode("utf-8", errors="replace"))
    except json.JSONDecodeError as e:
        raise ConversionError("FFprobe 輸出格式錯誤", str(e)) from e


def audio_stream(probe_data: dict) -> dict | None:
    return next((s for s in probe_data.get("streams", []) if s.get("codec_type") == "audio"), None)


def build_ffmpeg_command(ffmpeg: str, src: Path, dst: Path, profile: config.ConvertProfile) -> list[str]:
    return [
        ffmpeg, "-hide_banner", "-nostdin", "-y",
        "-i", str(src),
        "-map", "0:a:0", "-vn", "-sn", "-dn",
        # 不複製來源的任何 metadata / 章節 / 封面，標籤之後由 mutagen 重新建立
        "-map_metadata", "-1", "-map_chapters", "-1",
        "-c:a", profile.codec,
        "-b:a", f"{profile.bitrate_kbps}k",
        "-ar", str(profile.sample_rate_hz),
        "-ac", str(profile.channels),
        "-id3v2_version", "0", "-write_id3v1", "0",
        "-progress", "pipe:1", "-nostats",
        "-f", "mp3", str(dst),
    ]


def convert_to_mp3(src: Path, dst: Path, profile: config.ConvertProfile,
                   tools: FFmpegTools | None = None,
                   on_progress: Callable[[float], None] | None = None) -> Path:
    """把任意音訊轉成 MP3（CBR、指定取樣率與聲道），不寫任何標籤。"""
    tools = tools or check_ffmpeg()
    src, dst = Path(src), Path(dst)
    if not src.is_file():
        raise ConversionError("找不到下載的音訊檔", str(src))

    duration = 0.0
    try:
        duration = float(probe(src, tools.ffprobe).get("format", {}).get("duration") or 0)
    except ConversionError as e:
        log.warning("無法取得來源長度（不影響轉換）：%s", e.detail)

    cmd = build_ffmpeg_command(tools.ffmpeg, src, dst, profile)
    log.debug("ffmpeg: %s", " ".join(cmd))
    with tempfile.TemporaryFile() as stderr_file:
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=stderr_file, text=True)
        except OSError as e:
            raise ConversionError("無法啟動 FFmpeg", str(e)) from e
        started = time.monotonic()
        assert proc.stdout is not None
        for line in proc.stdout:
            if time.monotonic() - started > config.FFMPEG_TIMEOUT_SEC:
                proc.kill()
                raise ConversionError("FFmpeg 轉換逾時", f"超過 {config.FFMPEG_TIMEOUT_SEC} 秒")
            key, _, value = line.strip().partition("=")
            if key == "out_time_us" and duration > 0 and on_progress and value.isdigit():
                on_progress(min(1.0, int(value) / 1_000_000 / duration))
        proc.wait()
        stderr_file.seek(0)
        stderr = stderr_file.read().decode("utf-8", errors="replace")

    if proc.returncode != 0:
        tail = "\n".join(stderr.strip().splitlines()[-15:])
        raise ConversionError("FFmpeg 轉換失敗", tail)
    if not dst.is_file() or dst.stat().st_size == 0:
        raise ConversionError("FFmpeg 沒有產生 MP3 檔案", stderr[-2000:])
    if on_progress:
        on_progress(1.0)
    return dst
