"""Unicode 文字處理：正規化、清除異常字元、修復 mojibake。

這裡的函式是 Metadata 與檔名共用的基礎，不依賴任何第三方套件。
"""
from __future__ import annotations

import re
import unicodedata

# 保留的格式字元：emoji 組合需要 ZWJ / ZWNJ，旗幟 emoji 需要 tag characters
_KEEP_FORMAT_CHARS = {"‌", "‍"}
_TAG_CHAR_RANGE = (0xE0020, 0xE007F)

# 會被換成一般空白的字元（全形空白 U+3000 不在此列，保留）
_SPACE_LIKE = {"\t", "\n", "\r", "\v", "\f", " ", " ", " "}

_CJK_RE = re.compile(
    "["
    "ᄀ-ᇿ"  # Hangul Jamo
    "぀-ヿ"  # Hiragana / Katakana
    "㄰-㆏"  # Hangul Compatibility Jamo
    "㐀-䶿"  # CJK Ext A
    "一-鿿"  # CJK Unified
    "가-힯"  # Hangul Syllables
    "豈-﫿"  # CJK Compatibility
    "\U00020000-\U0002ffff"  # CJK Ext B+
    "]"
)


def _is_noncharacter(cp: int) -> bool:
    return 0xFDD0 <= cp <= 0xFDEF or (cp & 0xFFFE) == 0xFFFE


def is_unwanted_char(ch: str) -> bool:
    """控制字元、不可見格式字元、surrogate、私用區、noncharacter、U+FFFD。"""
    cp = ord(ch)
    cat = unicodedata.category(ch)
    if cat == "Cc":
        return True
    if cat == "Cf":
        if ch in _KEEP_FORMAT_CHARS or _TAG_CHAR_RANGE[0] <= cp <= _TAG_CHAR_RANGE[1]:
            return False
        return True  # BOM、零寬空白、方向控制字元、soft hyphen 等
    if cat in ("Cs", "Co"):
        return True
    if _is_noncharacter(cp) or cp == 0xFFFD:
        return True
    return False


def _cp1252_bytes(text: str) -> bytes | None:
    """把文字當作「被錯誤用 Latin-1 / CP1252 解碼的 bytes」還原回去。"""
    out = bytearray()
    for ch in text:
        cp = ord(ch)
        if cp < 0x100:
            out.append(cp)
            continue
        try:
            out += ch.encode("cp1252")
        except UnicodeEncodeError:
            return None
    return bytes(out)


def _is_cp1252_char(ch: str) -> bool:
    if ord(ch) < 0x100:
        return True
    try:
        ch.encode("cp1252")
        return True
    except UnicodeEncodeError:
        return False


def _fix_run(run: str, max_rounds: int) -> str:
    current = run
    for _ in range(max_rounds):
        if current.isascii():
            break
        raw = _cp1252_bytes(current)
        if raw is None:
            break
        try:
            decoded = raw.decode("utf-8")
        except UnicodeDecodeError:
            break
        if decoded == current:
            break
        current = decoded
    return current


def fix_mojibake(text: str, max_rounds: int = 3) -> str:
    """修復 UTF-8 被當成 Latin-1 / CP1252 解碼產生的亂碼，例如「æ™´å¤©」→「晴天」。

    做法：把連續的 Latin-1/CP1252 字元還原成 bytes，若能以 UTF-8 嚴格解碼就採用。
    一般的 Latin 文字（例如 Café）還原成 bytes 後不是合法 UTF-8，因此不會被誤改。
    最多重複 max_rounds 次以處理多重編碼錯誤。只處理亂碼片段，旁邊正常的中文不受影響。
    """
    if not text or text.isascii():
        return text
    parts: list[str] = []
    run: list[str] = []
    for ch in text:
        if _is_cp1252_char(ch):
            run.append(ch)
        else:
            if run:
                parts.append(_fix_run("".join(run), max_rounds))
                run = []
            parts.append(ch)
    if run:
        parts.append(_fix_run("".join(run), max_rounds))
    return "".join(parts)


def looks_like_mojibake(text: str) -> bool:
    return bool(text) and fix_mojibake(text) != text


def clean_text(text: str | None, form: str = "NFC") -> str:
    """寫入 ID3 或檔名前的標準清理流程。

    1. 修復 mojibake（必須在移除控制字元之前，因為 CP1252 亂碼可能含 U+0080–U+009F）
    2. Unicode 正規化（預設 NFC）
    3. 移除控制字元與不可見異常字元
    4. 合併多餘空白
    中文、日文、韓文、emoji、全形符號都會保留。
    """
    if text is None:
        return ""
    if isinstance(text, bytes):
        text = decode_bytes(text)
    text = str(text)
    text = fix_mojibake(text)
    text = unicodedata.normalize(form, text)
    chars = []
    for ch in text:
        if ch in _SPACE_LIKE:
            chars.append(" ")
        elif not is_unwanted_char(ch):
            chars.append(ch)
    text = "".join(chars)
    text = re.sub(r" {2,}", " ", text)
    return text.strip()


def decode_bytes(raw: bytes) -> str:
    """bytes 轉文字：依序嘗試 BOM、UTF-8、UTF-16、CP950(Big5)、GB18030，最後才 Latin-1。"""
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            return raw.decode("utf-16")
        except UnicodeDecodeError:
            pass
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    for enc in ("utf-8", "cp950", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1")


def has_cjk(text: str) -> bool:
    return bool(_CJK_RE.search(text or ""))


def is_clean_unicode(text: str, form: str = "NFC") -> bool:
    """驗證用：已正規化、沒有異常字元、沒有 mojibake。"""
    if text != unicodedata.normalize(form, text):
        return False
    if any(is_unwanted_char(ch) for ch in text):
        return False
    return not looks_like_mojibake(text)


_VERSION_WORDS = re.compile(
    r"\b(feat|ft|featuring|with|remix|mix|live|acoustic|version|ver|edit|demo|"
    r"instrumental|inst|cover|remaster(ed)?|karaoke|piano|unplugged)\b\.?",
    re.IGNORECASE,
)
_BILINGUAL_RE = re.compile(
    r"^(?P<cjk>.*" + _CJK_RE.pattern + r")\s+(?P<latin>[A-Za-z][A-Za-z0-9 .'&!?,-]*)$"
)


def strip_bilingual_latin_suffix(name: str) -> str:
    """「周杰倫 Jay Chou」→「周杰倫」、「晴天 Sunny Day」→「晴天」。

    只在「前半段以中日韓文字結尾、後半段是純英文」時處理；
    若英文部分是 feat./Remix/Live 等版本資訊則保留。
    """
    m = _BILINGUAL_RE.match(name or "")
    if not m:
        return name
    cjk, latin = m.group("cjk"), m.group("latin")
    if _VERSION_WORDS.search(latin):
        return name
    if sum(c.isalpha() for c in latin) < 2:
        return name
    return cjk.strip()


# 典型 UTF-8 → Latin-1/CP1252 亂碼的字元組合：Ã© â€™ å¤ æ™ ç‰ 等。
# 用於「已經無法還原」的亂碼（例如中間的位元組遺失），只作為警告，不自動修改。
_SUSPICIOUS_MOJIBAKE_RE = re.compile(
    "[ÂÃâã-ï]"
    "[\u0080-¿ŒœŠšŸŽžƒˆ˜–-™]"
)


def looks_like_garbled_latin(text: str) -> bool:
    """出現兩次以上典型亂碼字元組合時視為可疑。"""
    return len(_SUSPICIOUS_MOJIBAKE_RE.findall(text or "")) >= 2
