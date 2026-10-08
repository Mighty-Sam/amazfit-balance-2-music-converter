"""診斷模式：分析任意 MP3，找出 Amazfit Balance 2 可能顯示亂碼的原因。

和 validator 不同：這裡不知道「應該是什麼值」，只根據檔案本身判斷相容性，
並對每個問題給出原因說明，方便日後手錶又出現亂碼時直接查。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from mutagen import MutagenError
from mutagen.mp3 import MP3

import config
from core import converter
from core.errors import ConverterError
from core.id3_inspect import ENCODING_NAMES, RawTagInfo, inspect_tags
from core.text import is_clean_unicode, looks_like_garbled_latin, looks_like_mojibake

PASS, WARN, FAIL = "PASS", "WARN", "FAIL"


@dataclass
class Issue:
    level: str  # WARN | FAIL
    message: str


@dataclass
class DiagnosisReport:
    path: Path
    codec: str = ""
    sample_rate: int = 0
    bitrate_kbps: int = 0
    channels: int = 0
    duration: float = 0.0
    bitrate_mode: str = ""
    raw: RawTagInfo | None = None
    title: str | None = None
    artist: str | None = None
    album: str | None = None
    encodings: list[str] = field(default_factory=list)
    unicode_ok: bool = True
    issues: list[Issue] = field(default_factory=list)

    @property
    def verdict(self) -> str:
        if any(i.level == FAIL for i in self.issues):
            return FAIL
        if any(i.level == WARN for i in self.issues):
            return WARN
        return PASS

    def _yes_no(self, major: int) -> str:
        return "Yes" if self.raw and major in self.raw.versions() else "No"

    def format(self) -> str:
        channels = {1: "Mono", 2: "Stereo"}.get(self.channels, f"{self.channels} ch")
        v1 = "None"
        if self.raw and self.raw.v1:
            v1 = "Yes（含非 ASCII 字元）" if self.raw.v1.has_non_ascii else "Yes"
        lines = [
            "File:", self.path.name, "",
            "Audio:",
            self.codec.upper() if self.codec else "（無法解析）",
            f"{self.sample_rate / 1000:g} kHz" if self.sample_rate else "-",
            f"{self.bitrate_kbps} kbps{f' ({self.bitrate_mode})' if self.bitrate_mode else ''}"
            if self.bitrate_kbps else "-",
            channels if self.channels else "-",
            f"{int(self.duration // 60)}:{int(self.duration % 60):02d}" if self.duration else "-",
            "",
            "Metadata:", "",
            f"ID3v1: {v1}",
            f"ID3v2.2: {self._yes_no(2)}" if self.raw and 2 in self.raw.versions() else None,
            f"ID3v2.3: {self._yes_no(3)}",
            f"ID3v2.4: {self._yes_no(4)}",
            f"APEv2: Yes" if self.raw and self.raw.has_ape else None,
            "",
            "Title:", self.title if self.title else "（無）", "",
            "Artist:", self.artist if self.artist else "（無）", "",
            "Album:", self.album if self.album else "（無）", "",
            "Encoding:", ", ".join(self.encodings) if self.encodings else "（無文字欄位）", "",
            "Unicode:", "Valid" if self.unicode_ok else "Invalid", "",
            "Balance 2 Compatibility:", self.verdict,
        ]
        if self.issues:
            lines += ["", "問題："]
            lines += [f"  [{i.level}] {i.message}" for i in self.issues]
        return "\n".join(line for line in lines if line is not None)


def analyze_mp3(path: Path, tools: converter.FFmpegTools | None = None) -> DiagnosisReport:
    path = Path(path)
    report = DiagnosisReport(path)
    issues = report.issues

    if not path.is_file():
        issues.append(Issue(FAIL, "檔案不存在"))
        return report

    # ---- 音訊 ----
    try:
        audio = MP3(path)
        report.duration = audio.info.length
        report.bitrate_mode = {0: "", 1: "CBR", 2: "VBR", 3: "ABR"}.get(int(audio.info.bitrate_mode), "")
    except (MutagenError, OSError) as e:
        issues.append(Issue(FAIL, f"不是有效的 MP3 檔案（{type(e).__name__}）"))
    try:
        stream = converter.audio_stream(converter.probe(path, tools.ffprobe if tools else None)) or {}
        report.codec = stream.get("codec_name", "")
        report.sample_rate = int(stream.get("sample_rate") or 0)
        report.channels = int(stream.get("channels") or 0)
        report.bitrate_kbps = int(stream.get("bit_rate") or 0) // 1000
    except ConverterError as e:
        issues.append(Issue(WARN, f"FFprobe 無法分析：{e.user_message}"))
    if report.codec and report.codec != "mp3":
        issues.append(Issue(FAIL, f"音訊編碼是 {report.codec}，不是 MP3（副檔名與內容不符）"))
    if report.sample_rate and report.sample_rate != config.BALANCE2_PROFILE.sample_rate_hz:
        issues.append(Issue(WARN, f"取樣率 {report.sample_rate} Hz，建議 44100 Hz"))
    if report.channels and report.channels != 2:
        issues.append(Issue(WARN, f"{report.channels} 聲道，建議 Stereo"))
    if report.bitrate_kbps > 320:
        issues.append(Issue(WARN, f"位元率 {report.bitrate_kbps} kbps 過高"))

    # ---- 標籤結構 ----
    try:
        raw = inspect_tags(path)
    except OSError as e:
        issues.append(Issue(FAIL, f"無法讀取檔案：{e}"))
        return report
    report.raw = raw
    primary = raw.primary

    if not raw.v2_tags:
        issues.append(Issue(FAIL, "沒有 ID3v2 標籤，手錶只能顯示檔名或從 ID3v1 讀取"))
    if len(raw.v2_tags) > 1:
        issues.append(Issue(FAIL, f"檔案中有 {len(raw.v2_tags)} 個 ID3v2 標籤"
                                  f"（{', '.join(t.version_label for t in raw.v2_tags)}），播放器可能讀到錯的那個"))
    if primary and primary.major == 4:
        issues.append(Issue(WARN, "使用 ID3v2.4，部分硬體播放器支援不完整，建議改用 ID3v2.3"))
    if primary and primary.major == 2:
        issues.append(Issue(FAIL, "使用過時的 ID3v2.2"))
    if primary and primary.parse_error:
        issues.append(Issue(FAIL, f"ID3 結構損毀：{primary.parse_error}"))
    if raw.v1:
        if raw.v1.has_non_ascii:
            issues.append(Issue(FAIL, "存在含非 ASCII 字元的 ID3v1（只能用 Latin-1，中文必定亂碼），"
                                      "手錶若優先讀 ID3v1 就會顯示亂碼"))
        else:
            issues.append(Issue(WARN, "存在 ID3v1 標籤，建議移除以免手錶讀錯"))
    if raw.has_ape:
        issues.append(Issue(WARN, "存在 APEv2 標籤，建議移除"))

    # ---- 文字欄位 ----
    frames = primary.text_frames if primary else []
    by_id = {f.frame_id: f for f in frames}
    title_f = by_id.get("TIT2") or by_id.get("TT2")
    artist_f = by_id.get("TPE1") or by_id.get("TP1")
    album_f = by_id.get("TALB") or by_id.get("TAL")
    report.title = title_f.text if title_f else None
    report.artist = artist_f.text if artist_f else None
    report.album = album_f.text if album_f else None
    report.encodings = sorted({f.encoding_name + ("" if f.encoding != 1 or f.has_bom else "（無 BOM）")
                               for f in frames})

    if primary and not title_f:
        issues.append(Issue(WARN, "沒有 Title (TIT2)，手錶會顯示檔名"))
    for f in frames:
        label = f"{f.frame_id}"
        if f.text is None or f.error:
            issues.append(Issue(FAIL, f"{label} 無法正確解碼：{f.error}"))
            report.unicode_ok = False
            continue
        non_ascii = not f.text.isascii()
        if f.encoding == 3 and non_ascii:
            issues.append(Issue(FAIL, f"{label} 用 UTF-8 儲存非 ASCII 文字；"
                                      f"ID3v2.3 不支援 UTF-8，手錶很可能顯示亂碼"))
        elif f.encoding == 0 and non_ascii:
            issues.append(Issue(FAIL, f"{label} 用 ISO-8859-1 儲存非 ASCII 文字，中文會變成亂碼"))
        elif f.encoding == 2:
            issues.append(Issue(WARN, f"{label} 使用 UTF-16BE（無 BOM），建議 UTF-16"))
        if looks_like_mojibake(f.text):
            issues.append(Issue(FAIL, f"{label} 內容本身就是亂碼：{f.text!r}"))
            report.unicode_ok = False
        elif looks_like_garbled_latin(f.text):
            issues.append(Issue(WARN, f"{label} 疑似亂碼：{f.text!r}"))
            report.unicode_ok = False
        elif not is_clean_unicode(f.text):
            issues.append(Issue(WARN, f"{label} 含控制字元或未經 NFC 正規化"))
            report.unicode_ok = False
    return report


__all__ = ["analyze_mp3", "DiagnosisReport", "ENCODING_NAMES", "PASS", "WARN", "FAIL"]
