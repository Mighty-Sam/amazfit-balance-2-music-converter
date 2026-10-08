"""檔名處理：清除影片標題雜訊、產生安全的檔名。

檔名與 Metadata 分開管理：
    檔名     → 「周杰倫 - 晴天.mp3」（給人在檔案管理員裡看）
    Metadata → Title=晴天, Artist=周杰倫（給手錶解析）
"""
from __future__ import annotations

import re
from pathlib import Path

import config
from core.text import clean_text

# 括號內容若「全部」由這些字詞組成，就視為雜訊整段移除。
# 例如 [Official Video]、(Official Audio)、【MV】、(4K)。
# 但 (Live)、(feat. xxx)、(Acoustic Ver.) 會保留，因為它們是歌曲版本資訊。
_NOISE_WORDS = {
    "official", "music", "video", "audio", "mv", "m/v", "pv", "hd", "hq", "sd", "4k",
    "8k", "uhd", "1080p", "720p", "480p", "2160p", "60fps", "lyric", "lyrics", "visualizer",
    "full", "version", "clip", "teaser", "官方", "官方版", "完整版", "高清", "高畫質", "高音質",
    "歌詞", "動態歌詞", "字幕", "中字", "官方mv", "完整", "版", "official mv", "4k60",
}
_NOISE_ANCHORS = {
    "official", "mv", "m/v", "pv", "hd", "hq", "4k", "8k", "uhd", "1080p", "720p",
    "2160p", "lyric", "lyrics", "visualizer", "官方", "官方mv", "高清", "高畫質",
    "動態歌詞", "歌詞", "完整版", "audio", "video",
}

_BRACKET_RE = re.compile(
    r"\[([^\[\]]*)\]|\(([^()]*)\)|【([^【】]*)】|（([^（）]*)）|〔([^〔〕]*)〕|［([^［］]*)］"
)

# 標題尾端沒有括號的雜訊，例如「晴天 - Official Music Video」「告五人【愛人錯過】Official MV」
_TRAILING_NOISE_RE = re.compile(
    r"[\s\-–—|｜/]*(?<![A-Za-z0-9])"
    r"(?:official\s+(?:music\s+|lyrics?\s+)?(?:video|audio|mv|m/v)"
    r"|(?:music|lyrics?)\s+video"
    r"|官方(?:完整版)?\s*(?:mv|m/v)|官方版|動態歌詞|m/v|mv"
    r"|(?:4k|hd|hq)(?:\s*(?:mv|video))?)"
    r"\s*$",
    re.IGNORECASE,
)

_ILLEGAL_FILENAME_CHARS = {
    "/": "／", "\\": "＼", ":": "：", "*": "＊", "?": "？",
    '"': "＂", "<": "＜", ">": "＞", "|": "｜",
}
_WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                     *(f"LPT{i}" for i in range(1, 10))}


def _is_noise_group(content: str) -> bool:
    tokens = [t for t in re.split(r"[\s|｜/,_\-–—]+", content.strip().lower()) if t]
    # m/v 會被 / 切開，先合併回來
    joined = content.strip().lower()
    if joined in _NOISE_WORDS:
        return True
    if not tokens:
        return True  # 空括號
    tokens = ["m/v" if t in ("m", "v") and "m/v" in joined else t for t in tokens]
    if not all(t in _NOISE_WORDS for t in tokens):
        return False
    return any(t in _NOISE_ANCHORS for t in tokens)


def _replace_bracket(m: re.Match) -> str:
    content = next(g for g in m.groups() if g is not None)
    return " " if _is_noise_group(content) else m.group(0)


def remove_title_noise(title: str) -> str:
    """移除 [Official Video]、(Official Audio)、[MV]、[HD]、[4K] 等雜訊，但不過度清理。"""
    text = title
    for _ in range(3):  # 雜訊可能重複出現：「晴天 [MV] [HD] Official Video」
        new = _BRACKET_RE.sub(_replace_bracket, text)
        if not new.strip():  # 整個標題都是雜訊時保留原樣
            break
        without_tail = _TRAILING_NOISE_RE.sub("", new)
        if without_tail.strip():  # 不要把整個標題清空
            new = without_tail
        new = re.sub(r"\s{2,}", " ", new).strip()
        if new == text:
            break
        text = new
    return text.strip(" -–—|｜")


def _truncate_utf8(text: str, max_bytes: int) -> str:
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    return encoded[:max_bytes].decode("utf-8", errors="ignore").rstrip()


def sanitize_filename(name: str, max_bytes: int = config.MAX_FILENAME_BYTES) -> str:
    """把任意文字變成 macOS / FAT32 / Windows 都安全的檔名（不含副檔名）。

    非法字元換成全形版本（保留語意，例如 AC/DC → AC／DC），而不是直接刪除。
    """
    text = clean_text(name)
    text = "".join(_ILLEGAL_FILENAME_CHARS.get(ch, ch) for ch in text)
    text = text.strip(" .")  # macOS 的隱藏檔、Windows 不允許結尾的點
    text = _truncate_utf8(text, max_bytes).strip(" .")
    if text.upper() in _WINDOWS_RESERVED:
        text = f"_{text}"
    return text or "untitled"


def build_basename(title: str, artist: str = "") -> str:
    """「周杰倫 - 晴天」；沒有歌手時只用歌名。"""
    title = clean_text(title)
    artist = clean_text(artist)
    base = f"{artist} - {title}" if artist and title else (title or artist)
    return sanitize_filename(base)


def resolve_output_path(output_dir: Path, basename: str, overwrite: bool = False,
                        ext: str = config.OUTPUT_EXTENSION) -> Path:
    """檔案已存在時：overwrite=True 直接覆蓋，否則自動改名為「xxx (2).mp3」。"""
    output_dir = Path(output_dir)
    candidate = output_dir / f"{basename}{ext}"
    if overwrite or not candidate.exists():
        return candidate
    n = 2
    while True:
        candidate = output_dir / f"{basename} ({n}){ext}"
        if not candidate.exists():
            return candidate
        n += 1
