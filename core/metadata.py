"""Metadata：從 YouTube 資訊推導歌曲資料，並寫入乾淨的 ID3v2.3 (UTF-16) 標籤。

原則：不信任下載器原本的 Metadata。
    1. 讀取原始資料（yt-dlp info dict）
    2. 修復 mojibake → NFC 正規化 → 移除控制字元
    3. 清除檔案上所有舊的 ID3v1 / ID3v2 / APE 標籤
    4. 建立全新的 ID3v2.3，所有文字欄位用 UTF-16 儲存
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from mutagen import MutagenError
from mutagen.apev2 import APENoHeaderError
from mutagen.apev2 import delete as delete_ape
from mutagen.id3 import ID3, TALB, TCON, TDRC, TIT2, TPE1, TRCK, ID3NoHeaderError
from mutagen.id3 import delete as delete_id3

import config
from core.errors import MetadataError
from core.filename import remove_title_noise
from core.text import clean_text, fix_mojibake, strip_bilingual_latin_suffix

log = logging.getLogger(__name__)

# 欄位名稱 → ID3 frame 類別（TDRC 在存成 v2.3 時 mutagen 會自動轉成 TYER）
FRAME_MAP = {
    "title": TIT2,
    "artist": TPE1,
    "album": TALB,
    "year": TDRC,
    "track": TRCK,
    "genre": TCON,
}
# 讀回 ID3v2.3 時對應的 frame ID
V23_FRAME_IDS = {"title": "TIT2", "artist": "TPE1", "album": "TALB",
                 "year": "TYER", "track": "TRCK", "genre": "TCON"}


@dataclass
class TrackMetadata:
    title: str
    artist: str = ""
    album: str = ""
    year: str = ""
    track: str = ""
    genre: str = ""
    source: str = ""  # 資料來源，方便除錯：youtube_metadata / title_brackets / title_split / ...
    fixes: list[str] = field(default_factory=list)  # 修正紀錄（例如修復了 mojibake）

    def fields(self) -> dict[str, str]:
        return {k: getattr(self, k) for k in FRAME_MAP}

    @property
    def display_name(self) -> str:
        return f"{self.artist} - {self.title}" if self.artist else self.title


def _clean_field(name: str, value, fixes: list[str], form: str = "NFC") -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        value = ", ".join(str(v) for v in value if v)
    raw = str(value)
    if fix_mojibake(raw) != raw:
        fixes.append(f"{name}: 修復亂碼 {raw!r} → {fix_mojibake(raw)!r}")
    return clean_text(raw, form)


# 「周杰倫 Jay Chou【晴天 Sunny Day】」「五月天《倔強》」「告五人「愛人錯過」」
_ARTIST_BRACKET_TITLE_RE = re.compile(
    r"^(?P<artist>[^【《「『\[]+?)\s*[【《「『](?P<title>[^】》」』]+)[】》」』](?P<rest>.*)$"
)
_SEPARATOR_RE = re.compile(r"\s+[-–—－~～|｜]\s+|\s*[－–—]\s*")


def _normalize_name(s: str) -> str:
    return re.sub(r"\s+", "", s).casefold()


def _parse_video_title(title: str, uploader: str, is_music: bool = True) -> tuple[str, str, str]:
    """從影片標題拆出 (artist, title, source)。無法可靠判斷歌手時 artist 留白。"""
    cleaned = remove_title_noise(title)

    # YouTube Music 自動產生的頻道叫「周杰倫 - Topic」
    if uploader.endswith(" - Topic"):
        return uploader[: -len(" - Topic")].strip(), cleaned, "topic_channel"

    # 非音樂類影片（例如「電影與動畫」）的「A - B」通常不是「歌手 - 歌名」，不拆
    if not is_music:
        return "", cleaned, "title_only"

    m = _ARTIST_BRACKET_TITLE_RE.match(cleaned)
    if m and m.group("artist").strip():
        return m.group("artist").strip(" -|｜"), m.group("title").strip(), "title_brackets"

    parts = _SEPARATOR_RE.split(cleaned, maxsplit=1)
    if len(parts) == 2 and parts[0].strip() and parts[1].strip():
        left, right = parts[0].strip(), parts[1].strip()
        # 「晴天 - 周杰倫」這種反過來的格式：右邊剛好是頻道名稱時對調
        if uploader and _normalize_name(right) == _normalize_name(uploader):
            left, right = right, left
        return left, right, "title_split"

    return "", cleaned, "title_only"


def extract_metadata(info: dict) -> TrackMetadata:
    """由 yt-dlp info dict 推導歌曲 Metadata。資料不足時盡量補齊，不會丟出例外。"""
    fixes: list[str] = []
    video_title = _clean_field("video_title", info.get("title"), fixes)
    uploader = _clean_field("uploader", info.get("channel") or info.get("uploader"), fixes)
    track = _clean_field("track", info.get("track"), fixes)
    artist = _clean_field("artist", info.get("artists") or info.get("artist") or info.get("creator"), fixes)
    album = _clean_field("album", info.get("album"), fixes)
    genre = _clean_field("genre", info.get("genres") or info.get("genre"), fixes)

    year = ""
    for key in ("release_year", "release_date"):
        value = str(info.get(key) or "")
        if re.match(r"^\d{4}", value):
            year = value[:4]
            break

    track_no = info.get("track_number")
    track_no = str(track_no) if isinstance(track_no, int) and track_no > 0 else ""

    if track:
        # YouTube Music / 官方 Topic 頻道會提供 track + artist，最可靠
        title, source = track, "youtube_metadata"
    else:
        categories = info.get("categories")
        is_music = not categories or "Music" in categories  # 沒有分類資訊時照常解析
        parsed_artist, title, source = _parse_video_title(video_title, uploader, is_music)
        if not artist:
            artist = parsed_artist
        if config.STRIP_BILINGUAL_LATIN_SUFFIX and source != "title_only":
            title = strip_bilingual_latin_suffix(title)
            artist = strip_bilingual_latin_suffix(artist)

    if not title:
        title = video_title or str(info.get("id") or "Unknown")
        source = source or "fallback"

    return TrackMetadata(title=title, artist=artist, album=album, year=year,
                         track=track_no, genre=genre, source=source, fixes=fixes)


def sanitize_metadata(meta: TrackMetadata, form: str = "NFC") -> TrackMetadata:
    """寫入前的最後一道清理（使用者手動輸入的值也要經過這裡）。"""
    fixes = list(meta.fixes)
    values = {k: _clean_field(k, v, fixes, form) for k, v in meta.fields().items()}
    if values["year"] and not re.fullmatch(r"\d{4}", values["year"]):
        fixes.append(f"year: 格式不正確，已略過 {values['year']!r}")
        values["year"] = ""
    if values["track"] and not re.fullmatch(r"\d{1,3}(/\d{1,3})?", values["track"]):
        fixes.append(f"track: 格式不正確，已略過 {values['track']!r}")
        values["track"] = ""
    return TrackMetadata(**values, source=meta.source, fixes=fixes)


def strip_all_tags(path: Path) -> None:
    """移除所有 ID3v1、ID3v2（包含重複疊加的多個 ID3v2）與 APEv2 標籤。"""
    path = Path(path)
    for _ in range(10):
        with open(path, "rb") as f:
            head = f.read(3)
        if head != b"ID3":
            break
        delete_id3(path, delete_v1=True, delete_v2=True)
    else:
        raise MetadataError("無法清除舊的 ID3 標籤", f"{path}: 超過 10 層 ID3v2")
    delete_id3(path, delete_v1=True, delete_v2=False)  # 沒有 ID3v2 時也確保 ID3v1 被移除
    try:
        delete_ape(path)
    except APENoHeaderError:
        pass


def write_tags(path: Path, meta: TrackMetadata,
               profile: config.ConvertProfile = config.BALANCE2_PROFILE) -> TrackMetadata:
    """清除舊標籤並寫入全新的 ID3 標籤，回傳實際寫入的（已清理的）Metadata。"""
    path = Path(path)
    meta = sanitize_metadata(meta, profile.unicode_form)
    if not meta.title:
        raise MetadataError("歌曲名稱是空的，無法寫入 Metadata")
    try:
        if profile.strip_existing_tags:
            strip_all_tags(path)
        tags = ID3()
        for key, frame_cls in FRAME_MAP.items():
            value = getattr(meta, key)
            if value:
                tags.add(frame_cls(encoding=profile.id3_text_encoding, text=[value]))
        if profile.id3_version == 3:
            # 全新建立的 ID3() 存成 v2.3 時 mutagen 不會自動轉換 v2.4 專用 frame（例如 TDRC），
            # 必須明確呼叫，否則年份會以 v2.3 不存在的 TDRC 寫入，硬體播放器會忽略或解析錯誤
            tags.update_to_v23()
        tags.save(path, v1=2 if profile.write_id3v1 else 0,
                  v2_version=profile.id3_version, v23_sep="/")
    except MetadataError:
        raise
    except (MutagenError, OSError) as e:
        raise MetadataError("寫入 Metadata 失敗", f"{type(e).__name__}: {e}") from e
    log.debug("寫入 ID3v2.%d: %s", profile.id3_version, meta.fields())
    return meta


def read_tags(path: Path) -> dict[str, str]:
    """用 mutagen 讀回標籤（不轉換版本，維持檔案中的原始 frame）。"""
    try:
        tags = ID3(path, translate=False)
    except ID3NoHeaderError:
        return {}
    except (MutagenError, OSError) as e:
        raise MetadataError("無法讀取 Metadata", f"{type(e).__name__}: {e}") from e
    result = {}
    for key, frame_id in V23_FRAME_IDS.items():
        frame = tags.get(frame_id) or (tags.get("TDRC") if key == "year" else None)
        if frame is not None:
            result[key] = "/".join(str(t) for t in frame.text)
    return result
