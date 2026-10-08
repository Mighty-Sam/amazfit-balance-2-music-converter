"""轉換後驗證：重新讀取剛產生的 MP3，確認音訊與 Metadata 都正確。

同時用 mutagen 與獨立的原始位元組解析器 (core.id3_inspect) 讀取，兩者結果必須一致。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from mutagen import MutagenError
from mutagen.mp3 import MP3

import config
from core import converter
from core.errors import ConverterError
from core.id3_inspect import inspect_tags
from core.metadata import V23_FRAME_IDS, TrackMetadata, read_tags
from core.text import is_clean_unicode, looks_like_garbled_latin, looks_like_mojibake

log = logging.getLogger(__name__)


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""
    severity: str = "error"  # "error" 失敗即判定驗證失敗；"warn" 僅提醒


@dataclass
class ValidationReport:
    path: Path
    checks: list[Check] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.checks if c.severity == "error")

    @property
    def failures(self) -> list[Check]:
        return [c for c in self.checks if not c.ok and c.severity == "error"]

    @property
    def warnings(self) -> list[Check]:
        return [c for c in self.checks if not c.ok and c.severity == "warn"]

    def add(self, name: str, ok: bool, detail: str = "", severity: str = "error") -> bool:
        self.checks.append(Check(name, bool(ok), detail, severity))
        return bool(ok)

    def format(self) -> str:
        lines = []
        for c in self.checks:
            mark = "✓" if c.ok else ("!" if c.severity == "warn" else "✗")
            lines.append(f"[{mark}] {c.name}" + (f"：{c.detail}" if c.detail else ""))
        return "\n".join(lines)


def validate_mp3(path: Path, expected: TrackMetadata | None = None,
                 profile: config.ConvertProfile = config.BALANCE2_PROFILE,
                 tools: converter.FFmpegTools | None = None) -> ValidationReport:
    path = Path(path)
    report = ValidationReport(path)

    # 1. MP3 可以正常解析
    try:
        audio = MP3(path)
        report.add("MP3 可正常解析", audio.info.length > 0,
                   f"{audio.info.length:.1f} 秒, {audio.info.sample_rate} Hz, "
                   f"{audio.info.channels} 聲道, {audio.info.bitrate // 1000} kbps")
    except (MutagenError, OSError) as e:
        report.add("MP3 可正常解析", False, f"{type(e).__name__}: {e}")
        return report

    # 2. FFprobe 可以解析，且音訊參數符合設定
    try:
        data = converter.probe(path, tools.ffprobe if tools else None)
        stream = converter.audio_stream(data) or {}
        report.add("FFprobe 可解析", stream.get("codec_name") == "mp3",
                   f"codec={stream.get('codec_name')}")
        sr, ch = int(stream.get("sample_rate") or 0), int(stream.get("channels") or 0)
        br = int(stream.get("bit_rate") or 0) // 1000
        report.add("音訊格式符合設定",
                   sr == profile.sample_rate_hz and ch == profile.channels
                   and abs(br - profile.bitrate_kbps) <= 2,
                   f"{sr} Hz / {ch} 聲道 / {br} kbps（預期 {profile.sample_rate_hz} Hz / "
                   f"{profile.channels} 聲道 / {profile.bitrate_kbps} kbps）")
    except ConverterError as e:
        report.add("FFprobe 可解析", False, f"{e.user_message} {e.detail}".strip())

    # 3. 標籤結構：只有一個 ID3v2.3、沒有 ID3v1 / APE
    raw = inspect_tags(path)
    primary = raw.primary
    report.add(f"ID3v2.{profile.id3_version} 存在",
               primary is not None and primary.major == profile.id3_version and primary.location == "start",
               primary.version_label if primary else "沒有 ID3v2 標籤")
    report.add("只有一個 ID3v2 標籤", len(raw.v2_tags) == 1,
               ", ".join(f"{t.version_label}@{t.location}" for t in raw.v2_tags) or "無")
    if not profile.write_id3v1:
        report.add("沒有 ID3v1", raw.v1 is None, "存在 ID3v1" if raw.v1 else "")
    report.add("沒有 APE 標籤", not raw.has_ape)
    if primary and primary.parse_error:
        report.add("ID3 結構正確", False, primary.parse_error)

    # 4. 所有文字欄位都是 UTF-16（含 BOM）且能正確解碼
    frames = primary.text_frames if primary else []
    bad_enc = [f"{f.frame_id}={f.encoding_name}" for f in frames
               if f.encoding != profile.id3_text_encoding or f.text is None or f.error]
    report.add("文字編碼 UTF-16", frames and not bad_enc,
               ", ".join(bad_enc) if bad_enc else f"{len(frames)} 個文字欄位皆為 UTF-16 (BOM)")

    # 5. Title / Artist 可讀取且與寫入值完全相同（mutagen 與原始位元組各讀一次）
    try:
        tags = read_tags(path)
    except ConverterError as e:
        report.add("mutagen 可讀取 ID3", False, e.detail)
        tags = {}
    for key, label in (("title", "Title"), ("artist", "Artist")):
        frame_id = V23_FRAME_IDS[key]
        via_mutagen = tags.get(key, "")
        via_raw = raw.text(frame_id) or ""
        if key == "title":
            ok = bool(via_mutagen)
        else:
            ok = True  # Artist 可以留白
        detail = repr(via_mutagen) if via_mutagen else "（空白）"
        if expected is not None:
            want = getattr(expected, key)
            ok = ok and via_mutagen == want
            if via_mutagen != want:
                detail = f"寫入 {want!r}，讀回 {via_mutagen!r}"
        if via_mutagen != via_raw:
            ok = False
            detail += f"；原始位元組解析結果不同：{via_raw!r}"
        report.add(f"{label} 可正常讀取", ok, detail)

    # 6. Unicode 正常、沒有 mojibake
    values = {f.frame_id: f.text for f in frames if f.text}
    not_clean = [k for k, v in values.items() if not is_clean_unicode(v, profile.unicode_form)]
    report.add("中文 Unicode 正常", not not_clean,
               f"未正規化或含控制字元：{', '.join(not_clean)}" if not_clean else f"{profile.unicode_form}")
    mojibake = [f"{k}={v!r}" for k, v in values.items() if looks_like_mojibake(v)]
    report.add("沒有 UTF-8 → Latin-1 亂碼", not mojibake, ", ".join(mojibake))
    suspicious = [f"{k}={v!r}" for k, v in values.items() if looks_like_garbled_latin(v)]
    if suspicious:
        report.add("沒有疑似亂碼字元", False, ", ".join(suspicious), severity="warn")

    log.info("驗證%s：%s", "通過" if report.ok else "失敗", path.name)
    for c in report.checks:
        if not c.ok:
            log.warning("  驗證項目「%s」未通過：%s", c.name, c.detail)
    return report
