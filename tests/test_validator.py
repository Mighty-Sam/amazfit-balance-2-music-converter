"""驗證器測試：正確的檔案要通過，各種「手錶可能讀錯」的檔案都要被抓出來。"""
import shutil

import pytest
from mutagen.id3 import ID3, TIT2, TPE1

import config
from core.id3_inspect import inspect_tags
from core.metadata import TrackMetadata, write_tags
from core.validator import validate_mp3
from tests.conftest import make_audio

EXPECTED = TrackMetadata(title="晴天", artist="周杰倫")


def failed_names(report):
    return {c.name for c in report.failures}


@pytest.fixture
def good_mp3(blank_mp3):
    write_tags(blank_mp3, EXPECTED)
    return blank_mp3


def test_good_file_passes(good_mp3):
    report = validate_mp3(good_mp3, EXPECTED)
    assert report.ok, report.format()
    assert not report.warnings
    names = {c.name for c in report.checks}
    for required in ("MP3 可正常解析", "FFprobe 可解析", "ID3v2.3 存在", "沒有 ID3v1",
                     "文字編碼 UTF-16", "Title 可正常讀取", "Artist 可正常讀取",
                     "中文 Unicode 正常", "沒有 UTF-8 → Latin-1 亂碼"):
        assert required in names


def test_good_file_without_artist_passes(blank_mp3):
    meta = TrackMetadata(title="晴天")
    write_tags(blank_mp3, meta)
    assert validate_mp3(blank_mp3, meta).ok


def test_expected_mismatch_fails(good_mp3):
    report = validate_mp3(good_mp3, TrackMetadata(title="雨天", artist="周杰倫"))
    assert failed_names(report) == {"Title 可正常讀取"}


def test_id3v1_present_fails(good_mp3):
    ID3(good_mp3).save(good_mp3, v1=2, v2_version=3)
    assert inspect_tags(good_mp3).v1 is not None
    assert "沒有 ID3v1" in failed_names(validate_mp3(good_mp3, EXPECTED))


def test_id3v24_utf8_fails(blank_mp3):
    tags = ID3()
    tags.add(TIT2(encoding=3, text=["晴天"]))
    tags.add(TPE1(encoding=3, text=["周杰倫"]))
    tags.save(blank_mp3, v2_version=4)
    failed = failed_names(validate_mp3(blank_mp3, EXPECTED))
    assert {"ID3v2.3 存在", "文字編碼 UTF-16"} <= failed
    assert "Title 可正常讀取" not in failed  # 文字本身是對的，問題在格式


def test_latin1_encoding_fails(blank_mp3):
    tags = ID3()
    tags.add(TIT2(encoding=0, text=["Sunny"]))
    tags.save(blank_mp3, v2_version=3)
    report = validate_mp3(blank_mp3, TrackMetadata(title="Sunny"))
    assert "文字編碼 UTF-16" in failed_names(report)


def test_mojibake_in_file_fails(blank_mp3):
    """模擬一般下載器的錯誤：把 UTF-8 bytes 當 Latin-1 寫進去。"""
    tags = ID3()
    tags.add(TIT2(encoding=1, text=["æ™´å¤©"]))
    tags.save(blank_mp3, v2_version=3)
    report = validate_mp3(blank_mp3)
    assert "沒有 UTF-8 → Latin-1 亂碼" in failed_names(report)


def test_undecodable_garbage_warns(blank_mp3):
    tags = ID3()
    tags.add(TIT2(encoding=1, text=["å‘¨æ°å€«"]))  # 遺失位元組、已無法還原的亂碼
    tags.save(blank_mp3, v2_version=3)
    report = validate_mp3(blank_mp3)
    assert any(w.name == "沒有疑似亂碼字元" for w in report.warnings)


def test_control_char_in_file_fails(blank_mp3):
    tags = ID3()
    tags.add(TIT2(encoding=1, text=["晴​天"]))
    tags.save(blank_mp3, v2_version=3)
    assert "中文 Unicode 正常" in failed_names(validate_mp3(blank_mp3))


def test_stacked_tags_fail(good_mp3, tmp_path):
    extra = tmp_path / "extra.bin"
    extra.write_bytes(b"")
    stale = ID3()
    stale.add(TIT2(encoding=3, text=["stale"]))
    stale.save(extra, v2_version=4)
    good_mp3.write_bytes(extra.read_bytes() + good_mp3.read_bytes())
    failed = failed_names(validate_mp3(good_mp3, EXPECTED))
    assert "只有一個 ID3v2 標籤" in failed


def test_wrong_audio_format_fails(tmp_path):
    path = make_audio(tmp_path / "x.mp3", "-ac", "1", "-ar", "48000", "-c:a", "libmp3lame",
                      "-b:a", "128k", "-id3v2_version", "0")
    write_tags(path, EXPECTED)
    report = validate_mp3(path, EXPECTED)
    assert failed_names(report) == {"音訊格式符合設定"}


def test_custom_bitrate_profile(tmp_path):
    profile = config.make_profile(compat_mode=False, bitrate_kbps=320)
    path = make_audio(tmp_path / "x.mp3", "-ac", "2", "-ar", "44100", "-c:a", "libmp3lame",
                      "-b:a", "320k", "-id3v2_version", "0")
    write_tags(path, EXPECTED, profile)
    assert validate_mp3(path, EXPECTED, profile).ok


def test_not_an_mp3_fails(source_wav, tmp_path):
    fake = tmp_path / "fake.mp3"
    shutil.copy(source_wav, fake)
    report = validate_mp3(fake)
    assert not report.ok


def test_garbage_file_fails(tmp_path):
    p = tmp_path / "garbage.mp3"
    p.write_bytes(b"not audio at all" * 10)
    assert not validate_mp3(p).ok


# --------------------------------------------------------------------------
# 原始位元組解析器
# --------------------------------------------------------------------------
def _v23_tag(frame_id: bytes, payload: bytes) -> bytes:
    frame = frame_id + len(payload).to_bytes(4, "big") + b"\x00\x00" + payload
    size = len(frame)
    syncsafe = bytes([(size >> 21) & 0x7F, (size >> 14) & 0x7F, (size >> 7) & 0x7F, size & 0x7F])
    return b"ID3\x03\x00\x00" + syncsafe + frame


def test_inspect_detects_utf16_without_bom(tmp_path):
    p = tmp_path / "nobom.mp3"
    p.write_bytes(_v23_tag(b"TIT2", b"\x01" + "晴天".encode("utf-16-le")))
    frame = inspect_tags(p).primary.text_frames[0]
    assert frame.text == "晴天" and not frame.has_bom and frame.error


def test_inspect_reads_utf16_with_bom(tmp_path):
    p = tmp_path / "bom.mp3"
    p.write_bytes(_v23_tag(b"TIT2", b"\x01" + "晴天".encode("utf-16") + b"\x00\x00"))
    frame = inspect_tags(p).primary.text_frames[0]
    assert (frame.text, frame.has_bom, frame.error, frame.encoding_name) == ("晴天", True, "", "UTF-16")


def test_inspect_detects_invalid_utf8(tmp_path):
    p = tmp_path / "bad.mp3"
    p.write_bytes(_v23_tag(b"TIT2", b"\x03\xff\xfe\xfa"))
    frame = inspect_tags(p).primary.text_frames[0]
    assert frame.text is None and "解碼失敗" in frame.error


def test_inspect_dirty_download(dirty_mp3):
    info = inspect_tags(dirty_mp3)
    assert info.versions() == {4}
    assert info.v1 is not None
    assert {f.encoding_name for f in info.primary.text_frames} == {"UTF-8"}
