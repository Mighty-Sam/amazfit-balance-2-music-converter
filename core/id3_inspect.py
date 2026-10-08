"""獨立的 ID3 原始位元組解析器（不依賴 mutagen）。

驗證與診斷模式都用它：直接看檔案裡「實際存了什麼」——
有幾個 ID3v2 標籤、版本、每個文字欄位用什麼編碼、有沒有 BOM、有沒有 ID3v1 / APE。
這樣才能發現 mutagen 讀起來正常、但硬體播放器會讀錯的情況。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

ENCODING_NAMES = {0: "ISO-8859-1", 1: "UTF-16", 2: "UTF-16BE", 3: "UTF-8"}


@dataclass
class RawTextFrame:
    frame_id: str
    encoding: int
    text: str | None  # None = 解碼失敗
    has_bom: bool = False
    error: str = ""

    @property
    def encoding_name(self) -> str:
        return ENCODING_NAMES.get(self.encoding, f"未知({self.encoding})")


@dataclass
class RawID3v2Tag:
    major: int
    revision: int
    offset: int
    size: int  # 含 header
    flags: int
    location: str  # "start" | "end"
    text_frames: list[RawTextFrame] = field(default_factory=list)
    other_frame_ids: list[str] = field(default_factory=list)
    parse_error: str = ""

    @property
    def version_label(self) -> str:
        return f"ID3v2.{self.major}"


@dataclass
class RawID3v1:
    title: str
    artist: str
    album: str
    has_non_ascii: bool


@dataclass
class RawTagInfo:
    v2_tags: list[RawID3v2Tag] = field(default_factory=list)
    v1: RawID3v1 | None = None
    has_ape: bool = False

    def versions(self) -> set[int]:
        return {t.major for t in self.v2_tags}

    @property
    def primary(self) -> RawID3v2Tag | None:
        return self.v2_tags[0] if self.v2_tags else None

    def text(self, frame_id: str) -> str | None:
        """主要標籤（第一個）中某個文字欄位的值。"""
        if not self.primary:
            return None
        for f in self.primary.text_frames:
            if f.frame_id == frame_id:
                return f.text
        return None


def _syncsafe(b: bytes) -> int:
    return (b[0] << 21) | (b[1] << 14) | (b[2] << 7) | b[3]


def _unsync(data: bytes) -> bytes:
    return data.replace(b"\xff\x00", b"\xff")


def _decode_text(encoding: int, payload: bytes) -> tuple[str | None, bool, str]:
    """回傳 (文字, 是否有 BOM, 錯誤訊息)。多個值以 / 連接。"""
    try:
        if encoding == 0:
            text = payload.decode("latin-1")
            return "/".join(v for v in text.split("\x00") if v), False, ""
        if encoding == 1:
            values, has_bom = [], True
            # 每個值都必須有自己的 BOM，以 2-byte 的 \x00\x00 分隔
            chunks = []
            i, start = 0, 0
            while i + 1 < len(payload):
                if payload[i] == 0 and payload[i + 1] == 0:
                    chunks.append(payload[start:i])
                    start = i + 2
                i += 2
            chunks.append(payload[start:])
            for chunk in chunks:
                if not chunk:
                    continue
                if chunk[:2] not in (b"\xff\xfe", b"\xfe\xff"):
                    has_bom = False
                    values.append(chunk.decode("utf-16-le"))
                else:
                    values.append(chunk.decode("utf-16"))
            return "/".join(values), has_bom, "" if has_bom else "UTF-16 缺少 BOM"
        if encoding == 2:
            text = payload.decode("utf-16-be")
        elif encoding == 3:
            text = payload.decode("utf-8")
        else:
            return None, False, f"未知的文字編碼 {encoding}"
        return "/".join(v for v in text.split("\x00") if v), False, ""
    except UnicodeDecodeError as e:
        return None, False, f"解碼失敗：{e}"


def _parse_frames(tag: RawID3v2Tag, body: bytes) -> None:
    major = tag.major
    pos = 0
    if tag.flags & 0x40:  # extended header
        if major == 3 and len(body) >= 4:
            pos = 4 + int.from_bytes(body[0:4], "big")
        elif major == 4 and len(body) >= 4:
            pos = _syncsafe(body[0:4])
    id_len, header_len = (3, 6) if major == 2 else (4, 10)
    while pos + header_len <= len(body):
        frame_id_raw = body[pos:pos + id_len]
        if frame_id_raw[:1] == b"\x00":
            break  # padding
        try:
            frame_id = frame_id_raw.decode("ascii")
        except UnicodeDecodeError:
            tag.parse_error = f"無效的 frame ID 於 offset {pos}"
            break
        if major == 2:
            size = int.from_bytes(body[pos + 3:pos + 6], "big")
            frame_flags = 0
        elif major == 4:
            size = _syncsafe(body[pos + 4:pos + 8])
            frame_flags = int.from_bytes(body[pos + 8:pos + 10], "big")
        else:
            size = int.from_bytes(body[pos + 4:pos + 8], "big")
            frame_flags = int.from_bytes(body[pos + 8:pos + 10], "big")
        data = body[pos + header_len:pos + header_len + size]
        pos += header_len + size
        if len(data) < size:
            tag.parse_error = f"{frame_id} 長度超出標籤範圍"
            break
        if major == 4 and frame_flags & 0x0002:  # frame-level unsynchronisation
            data = _unsync(data)
        is_text = frame_id.startswith("T") and frame_id not in ("TXXX", "TXX")
        if is_text and data:
            encoding = data[0]
            text, has_bom, err = _decode_text(encoding, data[1:])
            tag.text_frames.append(RawTextFrame(frame_id, encoding, text, has_bom, err))
        else:
            tag.other_frame_ids.append(frame_id)


def _parse_v2_at(data: bytes, offset: int, location: str) -> RawID3v2Tag | None:
    if data[offset:offset + 3] != b"ID3" or len(data) < offset + 10:
        return None
    major, revision, flags = data[offset + 3], data[offset + 4], data[offset + 5]
    body_size = _syncsafe(data[offset + 6:offset + 10])
    total = 10 + body_size + (10 if major == 4 and flags & 0x10 else 0)
    tag = RawID3v2Tag(major, revision, offset, total, flags, location)
    body = data[offset + 10:offset + 10 + body_size]
    if flags & 0x80 and major in (2, 3):  # 整個標籤 unsynchronisation
        body = _unsync(body)
    if major in (2, 3, 4):
        _parse_frames(tag, body)
    else:
        tag.parse_error = f"不支援的 ID3v2.{major}"
    return tag


def inspect_tags(path: Path) -> RawTagInfo:
    data = Path(path).read_bytes()
    info = RawTagInfo()

    # 檔頭：可能疊了好幾個 ID3v2
    offset = 0
    while True:
        tag = _parse_v2_at(data, offset, "start")
        if not tag:
            break
        info.v2_tags.append(tag)
        offset += tag.size

    # 檔尾：ID3v1 (128 bytes)
    end = len(data)
    if end >= 128 and data[end - 128:end - 125] == b"TAG":
        v1 = data[end - 128:end]

        def field_(a: int, b: int) -> str:
            return v1[a:b].split(b"\x00")[0].decode("latin-1").strip()

        info.v1 = RawID3v1(field_(3, 33), field_(33, 63), field_(63, 93),
                           has_non_ascii=any(c >= 0x80 for c in v1[3:93]))
        end -= 128
    # APEv2 footer
    if end >= 32 and data[end - 32:end - 24] == b"APETAGEX":
        info.has_ape = True
        end -= int.from_bytes(data[end - 20:end - 16], "little") + 32
    # 檔尾的 ID3v2.4（以 3DI footer 標示）
    if end >= 10 and data[end - 10:end - 7] == b"3DI":
        body_size = _syncsafe(data[end - 4:end])
        start = end - 10 - body_size - 10
        tag = _parse_v2_at(data, start, "end") if start >= 0 else None
        if tag:
            info.v2_tags.append(tag)
    return info
