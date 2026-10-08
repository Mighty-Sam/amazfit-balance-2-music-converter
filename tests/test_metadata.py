"""Metadata 測試：寫入 → 儲存 → 重新讀取，文字必須完全一致。"""
import unicodedata

import pytest
from mutagen.id3 import ID3, TIT2
from mutagen.mp3 import MP3

import config
from core.metadata import TrackMetadata, extract_metadata, read_tags, strip_all_tags, write_tags
from core.text import clean_text, fix_mojibake, looks_like_mojibake

WORDS = ["周杰倫", "晴天", "告五人", "愛人錯過", "蔡依林", "倒帶", "五月天", "倔強",
         "日本語", "한국어", "English"]


# --------------------------------------------------------------------------
# 寫入 → 重新讀取
# --------------------------------------------------------------------------
@pytest.mark.parametrize("original", WORDS)
def test_roundtrip_title_and_artist(blank_mp3, original):
    write_tags(blank_mp3, TrackMetadata(title=original, artist=original, album=original))

    reloaded = read_tags(blank_mp3)
    assert reloaded["title"] == original
    assert reloaded["artist"] == original
    assert reloaded["album"] == original

    # 用全新的 mutagen 物件再讀一次，確認版本與編碼
    tags = ID3(blank_mp3, translate=False)
    assert tags.version == (2, 3, 0)
    for frame_id in ("TIT2", "TPE1", "TALB"):
        assert tags[frame_id].encoding == config.ID3_ENCODING_UTF16
        assert tags[frame_id].text == [original]


@pytest.mark.parametrize("title,artist", [("晴天", "周杰倫"), ("愛人錯過", "告五人"),
                                          ("倒帶", "蔡依林"), ("倔強", "五月天")])
def test_roundtrip_song_pairs(blank_mp3, title, artist):
    write_tags(blank_mp3, TrackMetadata(title=title, artist=artist, year="2003", track="1", genre="華語流行"))
    reloaded = read_tags(blank_mp3)
    assert reloaded == {"title": title, "artist": artist, "year": "2003", "track": "1", "genre": "華語流行"}


def test_all_text_frames_are_utf16_with_bom(blank_mp3):
    write_tags(blank_mp3, TrackMetadata(title="晴天", artist="周杰倫", album="葉惠美",
                                        year="2003", track="3", genre="Pop"))
    tags = ID3(blank_mp3, translate=False)
    text_frames = [f for f in tags.values() if f.FrameID.startswith("T")]
    assert {f.FrameID for f in text_frames} == {"TIT2", "TPE1", "TALB", "TYER", "TRCK", "TCON"}
    assert all(f.encoding == config.ID3_ENCODING_UTF16 for f in text_frames)
    # 原始位元組：ID3v2.3 header，TIT2 內容以編碼位元 0x01 + BOM 開頭
    raw = blank_mp3.read_bytes()
    assert raw[:4] == b"ID3\x03"
    idx = raw.index(b"TIT2")
    assert raw[idx + 10] == 0x01
    assert raw[idx + 11:idx + 13] in (b"\xff\xfe", b"\xfe\xff")


def test_empty_optional_fields_are_not_written(blank_mp3):
    write_tags(blank_mp3, TrackMetadata(title="晴天"))
    tags = ID3(blank_mp3, translate=False)
    assert set(tags.keys()) == {"TIT2"}
    assert read_tags(blank_mp3) == {"title": "晴天"}


def test_mp3_still_playable_after_tagging(blank_mp3):
    write_tags(blank_mp3, TrackMetadata(title="晴天", artist="周杰倫"))
    audio = MP3(blank_mp3)
    assert audio.info.sample_rate == 44100
    assert audio.info.channels == 2
    assert audio.info.length > 1.5


# --------------------------------------------------------------------------
# 清除舊 Metadata
# --------------------------------------------------------------------------
def test_old_id3v24_and_id3v1_are_removed(dirty_mp3):
    raw = dirty_mp3.read_bytes()
    assert raw[:4] == b"ID3\x04" and raw[-128:-125] == b"TAG"  # 前置條件

    write_tags(dirty_mp3, TrackMetadata(title="晴天", artist="周杰倫"))

    raw = dirty_mp3.read_bytes()
    assert raw[:4] == b"ID3\x03"
    assert raw[-128:-125] != b"TAG", "ID3v1 應被移除"
    assert raw.count(b"ID3\x04") == 0
    assert raw.count(b"ID3\x03") == 1
    tags = ID3(dirty_mp3, translate=False)
    assert "COMM::eng" not in tags and not tags.getall("COMM"), "舊的 comment 應被清除"
    assert read_tags(dirty_mp3) == {"title": "晴天", "artist": "周杰倫"}


def test_strip_all_tags_handles_stacked_id3v2(blank_mp3):
    write_tags(blank_mp3, TrackMetadata(title="A"))
    data = blank_mp3.read_bytes()
    # 在檔案最前面再疊一層 ID3v2.4 標籤
    stacked = ID3()
    stacked.add(TIT2(encoding=3, text=["stale"]))
    extra = blank_mp3.with_suffix(".extra")
    extra.write_bytes(b"")
    stacked.save(extra, v2_version=4)
    blank_mp3.write_bytes(extra.read_bytes() + data)
    assert blank_mp3.read_bytes().count(b"ID3") >= 2

    strip_all_tags(blank_mp3)
    assert blank_mp3.read_bytes()[:3] != b"ID3"


# --------------------------------------------------------------------------
# Unicode 正規化 / 控制字元 / mojibake
# --------------------------------------------------------------------------
def test_nfc_normalization(blank_mp3):
    nfd = unicodedata.normalize("NFD", "한국어 Café")
    assert nfd != "한국어 Café"
    written = write_tags(blank_mp3, TrackMetadata(title=nfd))
    assert written.title == "한국어 Café"
    assert read_tags(blank_mp3)["title"] == "한국어 Café"


@pytest.mark.parametrize("dirty,expected", [
    ("晴\x00天", "晴天"),
    ("﻿晴天", "晴天"),
    ("晴​天", "晴天"),
    ("‮倒帶‬", "倒帶"),
    ("周杰倫\t-\n晴天", "周杰倫 - 晴天"),
    ("晴天\x07\x1b", "晴天"),
])
def test_control_characters_removed(dirty, expected):
    assert clean_text(dirty) == expected


@pytest.mark.parametrize("text", [
    "周杰倫", "日本語", "한국어", "English", "🎵 晴天 🌤️", "👨‍👩‍👧 家庭",
    "（全形括號）", "ＡＢＣ１２３", "晴天！？", "愛　人", "Beyoncé", "Café del Mar",
])
def test_clean_text_preserves_valid_text(text):
    assert clean_text(text) == unicodedata.normalize("NFC", text)


def to_mojibake(text: str, rounds: int = 1, codec: str = "cp1252") -> str:
    """模擬「UTF-8 bytes 被當成 CP1252 / Latin-1 解碼」。

    CP1252 沒有定義的 0x81/0x8D/0x8F/0x90/0x9D 依瀏覽器 (WHATWG) 的做法變成 C1 控制字元。
    """
    for _ in range(rounds):
        out = []
        for b in text.encode("utf-8"):
            try:
                out.append(bytes([b]).decode(codec))
            except UnicodeDecodeError:
                out.append(chr(b))
        text = "".join(out)
    return text


MOJIBAKE_CASES = [(to_mojibake(w), w) for w in WORDS if not w.isascii()] + [
    ("æ™´å¤©", "晴天"),  # 需求文件中的實際例子
    (to_mojibake("晴天", rounds=2), "晴天"),  # 被錯誤轉碼兩次
    (to_mojibake("周杰倫", codec="latin-1"), "周杰倫"),
    (to_mojibake("告五人 - 愛人錯過"), "告五人 - 愛人錯過"),
    ("周杰倫 - " + to_mojibake("晴天"), "周杰倫 - 晴天"),  # 只有部分是亂碼
]


@pytest.mark.parametrize("broken,fixed", MOJIBAKE_CASES)
def test_mojibake_repair(broken, fixed):
    assert looks_like_mojibake(broken)
    assert fix_mojibake(broken) == fixed
    assert clean_text(broken) == fixed


@pytest.mark.parametrize("text", ["English", "Beyoncé", "Mötley Crüe", "naïve café", "晴天", "Ångström"])
def test_mojibake_no_false_positive(text):
    assert not looks_like_mojibake(text)
    assert fix_mojibake(text) == text


def test_mojibake_written_as_fixed_text(blank_mp3):
    meta = extract_metadata({"id": "x", "title": "æ™´å¤©"})
    assert meta.title == "晴天"
    assert meta.fixes, "應記錄修正"
    write_tags(blank_mp3, meta)
    assert read_tags(blank_mp3)["title"] == "晴天"


# --------------------------------------------------------------------------
# 從 YouTube 資訊推導 Metadata
# --------------------------------------------------------------------------
@pytest.mark.parametrize("info,title,artist", [
    ({"title": "周杰倫 Jay Chou【晴天 Sunny Day】Official Music Video", "channel": "周杰倫 Jay Chou"},
     "晴天", "周杰倫"),
    ({"title": "告五人 Accusefive【愛人錯過 Somebody Loves You】Official Music Video"}, "愛人錯過", "告五人"),
    ({"title": "周杰倫 - 晴天 [Official Video] [1080p]"}, "晴天", "周杰倫"),
    ({"title": "蔡依林 Jolin Tsai《倒帶》Official MV"}, "倒帶", "蔡依林"),
    ({"title": "五月天 - 倔強 (Official Audio)"}, "倔強", "五月天"),
    ({"title": "晴天 - 周杰倫", "channel": "周杰倫"}, "晴天", "周杰倫"),
    ({"title": "Some Song", "channel": "Taylor Swift - Topic"}, "Some Song", "Taylor Swift"),
    ({"title": "Big Buck Bunny 60fps 4K - Official Blender Foundation Short Film",
      "categories": ["Film & Animation"], "channel": "Blender"},
     "Big Buck Bunny 60fps 4K - Official Blender Foundation Short Film", ""),
    ({"title": "周杰倫 - 晴天", "categories": ["Music"]}, "晴天", "周杰倫"),
    ({"title": "晴天 (Live)", "channel": "隨便一個頻道"}, "晴天 (Live)", ""),
    ({"title": "Video title", "track": "晴天", "artists": ["周杰倫"], "album": "葉惠美",
      "release_year": 2003}, "晴天", "周杰倫"),
    ({"title": "x", "track": "晴天", "artist": "周杰倫"}, "晴天", "周杰倫"),
])
def test_extract_metadata(info, title, artist):
    meta = extract_metadata({"id": "abc", **info})
    assert meta.title == title
    assert meta.artist == artist


def test_extract_metadata_youtube_music_fields():
    meta = extract_metadata({"id": "a", "title": "t", "track": "晴天", "artists": ["周杰倫"],
                             "album": "葉惠美", "release_year": 2003, "track_number": 3,
                             "genres": ["Pop"]})
    assert (meta.title, meta.artist, meta.album, meta.year, meta.track, meta.genre) == \
        ("晴天", "周杰倫", "葉惠美", "2003", "3", "Pop")


@pytest.mark.parametrize("info", [{}, {"id": "abc"}, {"title": None}, {"title": "", "channel": ""}])
def test_extract_metadata_never_fails_on_missing_data(info):
    meta = extract_metadata(info)
    assert meta.title  # 一定有標題
    assert meta.artist == ""


def test_invalid_year_and_track_are_dropped(blank_mp3):
    written = write_tags(blank_mp3, TrackMetadata(title="晴天", year="20xx", track="abc"))
    assert written.year == "" and written.track == ""
    assert read_tags(blank_mp3) == {"title": "晴天"}
