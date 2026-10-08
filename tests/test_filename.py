import unicodedata

import pytest

from core.filename import build_basename, remove_title_noise, resolve_output_path, sanitize_filename


@pytest.mark.parametrize("raw,expected", [
    ("周杰倫 - 晴天 [Official Video] [1080p]", "周杰倫 - 晴天"),
    ("周杰倫 - 晴天 [Official Video]", "周杰倫 - 晴天"),
    ("周杰倫 - 晴天 [Official Audio]", "周杰倫 - 晴天"),
    ("周杰倫 - 晴天 (Official Video)", "周杰倫 - 晴天"),
    ("周杰倫 - 晴天 (Official Audio)", "周杰倫 - 晴天"),
    ("周杰倫 - 晴天 [MV]", "周杰倫 - 晴天"),
    ("周杰倫 - 晴天 [HD]", "周杰倫 - 晴天"),
    ("周杰倫 - 晴天 [4K]", "周杰倫 - 晴天"),
    ("周杰倫 - 晴天 (Official Music Video)", "周杰倫 - 晴天"),
    ("周杰倫 - 晴天【官方MV】", "周杰倫 - 晴天"),
    ("周杰倫 - 晴天 (Lyric Video)", "周杰倫 - 晴天"),
    ("周杰倫 - 晴天 [MV] [HD]", "周杰倫 - 晴天"),
    ("周杰倫 - 晴天 - Official Music Video", "周杰倫 - 晴天"),
    ("周杰倫 Jay Chou【晴天 Sunny Day】Official Music Video", "周杰倫 Jay Chou【晴天 Sunny Day】"),
    ("五月天 - 倔強 官方MV", "五月天 - 倔強"),
    # 不要過度清理
    ("周杰倫 - 晴天", "周杰倫 - 晴天"),
    ("晴天 (Live)", "晴天 (Live)"),
    ("晴天 (feat. 某某)", "晴天 (feat. 某某)"),
    ("晴天 (Acoustic Version)", "晴天 (Acoustic Version)"),
    ("Video Killed the Radio Star", "Video Killed the Radio Star"),
    ("Official", "Official"),
    ("MV", "MV"),
    ("The Remv", "The Remv"),
    ("[MV]", "[MV]"),  # 整個標題都是雜訊時保留原樣，避免變成空字串
])
def test_remove_title_noise(raw, expected):
    assert remove_title_noise(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    ("周杰倫 - 晴天", "周杰倫 - 晴天"),
    ("AC/DC - Back In Black", "AC／DC - Back In Black"),
    ('a:b*c?d"e<f>g|h\\i', "a：b＊c？d＂e＜f＞g｜h＼i"),
    ("  .hidden.  ", "hidden"),
    ("晴天\x00\x07", "晴天"),
    ("æ™´å¤©", "晴天"),  # 檔名也不能出現 mojibake
    ("CON", "_CON"),
    ("", "untitled"),
    ("...", "untitled"),
])
def test_sanitize_filename(raw, expected):
    assert sanitize_filename(raw) == expected


def test_sanitize_filename_is_nfc():
    nfd = unicodedata.normalize("NFD", "한국어")
    assert sanitize_filename(nfd) == unicodedata.normalize("NFC", "한국어")


def test_sanitize_filename_truncates_by_utf8_bytes():
    name = sanitize_filename("晴" * 300, max_bytes=200)
    assert len(name.encode("utf-8")) <= 200
    assert set(name) == {"晴"}  # 不會切在多位元組字元中間


@pytest.mark.parametrize("title,artist,expected", [
    ("晴天", "周杰倫", "周杰倫 - 晴天"),
    ("晴天", "", "晴天"),
    ("", "周杰倫", "周杰倫"),
    ("æ™´å¤©", "周杰倫", "周杰倫 - 晴天"),
])
def test_build_basename(title, artist, expected):
    assert build_basename(title, artist) == expected


def test_filename_and_metadata_are_independent():
    """檔名「周杰倫 - 晴天」，但 Metadata 的 Title 只有「晴天」。"""
    from core.metadata import extract_metadata
    meta = extract_metadata({"title": "周杰倫 - 晴天 [Official Video] [1080p]"})
    assert meta.title == "晴天" and meta.artist == "周杰倫"
    assert build_basename(meta.title, meta.artist) == "周杰倫 - 晴天"


def test_resolve_output_path_avoids_overwrite(tmp_path):
    first = resolve_output_path(tmp_path, "周杰倫 - 晴天")
    assert first.name == "周杰倫 - 晴天.mp3"
    first.write_bytes(b"x")
    second = resolve_output_path(tmp_path, "周杰倫 - 晴天")
    assert second.name == "周杰倫 - 晴天 (2).mp3"
    second.write_bytes(b"x")
    assert resolve_output_path(tmp_path, "周杰倫 - 晴天").name == "周杰倫 - 晴天 (3).mp3"
    assert resolve_output_path(tmp_path, "周杰倫 - 晴天", overwrite=True) == first
