"""完整流程與驗收測試（用假的 downloader 取代 YouTube，離線也能跑）。"""
import shutil
from pathlib import Path

import pytest
from mutagen.id3 import ID3, TIT2

import config
from core.diagnostics import analyze_mp3
from core.downloader import parse_youtube_url
from core.errors import DownloadError, InvalidURLError
from core.id3_inspect import inspect_tags
from core.metadata import read_tags
from core.pipeline import Pipeline, Step

GOOD_ID = "aaaaaaaaaaa"
MOJIBAKE_ID = "bbbbbbbbbbb"
MISSING_ID = "ccccccccccc"


class FakeDownloader:
    def __init__(self, source_audio: Path, infos: dict):
        self.source_audio = source_audio
        self.infos = infos

    def fetch_info(self, url):
        vid = parse_youtube_url(url).video_id
        if vid not in self.infos:
            raise DownloadError("影片不存在或目前無法觀看", "This video is unavailable")
        return {"id": vid, **self.infos[vid]}

    def download_audio(self, info, dest_dir, on_progress=None):
        dst = Path(dest_dir) / f"{info['id']}.webm"
        shutil.copy(self.source_audio, dst)
        if on_progress:
            on_progress(1.0, "done")
        return dst


@pytest.fixture
def pipeline(source_wav, tmp_path):
    infos = {
        GOOD_ID: {"title": "周杰倫 Jay Chou【晴天 Sunny Day】Official Music Video",
                  "channel": "周杰倫 Jay Chou", "categories": ["Music"]},
        MOJIBAKE_ID: {"title": "æ™´å¤©", "artist": "å‘¨æ\x9d°å€«"},
    }
    return Pipeline(config.BALANCE2_PROFILE, tmp_path / "out", downloader=FakeDownloader(source_wav, infos))


def url(vid):
    return f"https://www.youtube.com/watch?v={vid}"


def assert_acceptance(path: Path):
    """需求 §21 驗收標準。"""
    assert path.name == "周杰倫 - 晴天.mp3"
    tags = read_tags(path)
    assert tags["title"] == "晴天"
    assert tags["artist"] == "周杰倫"
    raw = inspect_tags(path)
    assert [t.version_label for t in raw.v2_tags] == ["ID3v2.3"]
    assert raw.v1 is None
    assert {f.encoding_name for f in raw.primary.text_frames} == {"UTF-16"}
    assert all(f.has_bom for f in raw.primary.text_frames)
    assert ID3(path, translate=False).version == (2, 3, 0)


def test_acceptance_from_youtube_title(pipeline):
    events = []
    result = pipeline.process_url(url(GOOD_ID), events.append)
    assert result.ok, result.error
    assert_acceptance(result.output_path)
    assert result.validation.ok
    assert result.summary().startswith("完成！")
    steps_done = [e.step for e in events if e.status == "done"]
    assert steps_done == list(Step)


def test_acceptance_from_mojibake_metadata(pipeline):
    result = pipeline.process_url(url(MOJIBAKE_ID))
    assert result.ok, result.error
    assert_acceptance(result.output_path)
    assert any("修復亂碼" in n for n in result.notes)


def test_audio_converted_to_balance2_format(pipeline):
    result = pipeline.process_url(url(GOOD_ID))
    report = analyze_mp3(result.output_path)
    assert (report.codec, report.sample_rate, report.channels, report.bitrate_kbps) == ("mp3", 44100, 2, 192)
    assert report.verdict == "PASS", report.format()


def test_batch_continues_after_failure(pipeline):
    urls = [url(GOOD_ID), url(MISSING_ID), "not a url", url(MOJIBAKE_ID)]
    results = [pipeline.process_url(u) for u in urls]
    assert [r.ok for r in results] == [True, False, False, True]
    assert results[1].error == "影片不存在或目前無法觀看"
    assert results[1].failed_step == Step.FETCH_INFO
    assert "YouTube" in results[2].error or "網址" in results[2].error
    # 同名檔案不覆蓋，自動改名
    assert results[3].output_path.name == "周杰倫 - 晴天 (2).mp3"


def test_overwrite_option(source_wav, tmp_path):
    infos = {GOOD_ID: {"title": "周杰倫 - 晴天"}}
    profile = config.make_profile(True, overwrite_existing=True)
    p = Pipeline(profile, tmp_path, downloader=FakeDownloader(source_wav, infos))
    first = p.process_url(url(GOOD_ID)).output_path
    second = p.process_url(url(GOOD_ID)).output_path
    assert first == second and len(list(tmp_path.glob("*.mp3"))) == 1


def test_ffmpeg_failure_is_reported(tmp_path):
    bad_audio = tmp_path / "bad.webm"
    bad_audio.write_bytes(b"this is not audio")
    p = Pipeline(config.BALANCE2_PROFILE, tmp_path / "out",
                 downloader=FakeDownloader(bad_audio, {GOOD_ID: {"title": "x"}}))
    result = p.process_url(url(GOOD_ID))
    assert not result.ok
    assert result.failed_step == Step.CONVERT
    assert "FFmpeg" in result.error


def test_unwritable_output_dir_is_reported(source_wav, tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    p = Pipeline(config.BALANCE2_PROFILE, blocker / "sub",
                 downloader=FakeDownloader(source_wav, {GOOD_ID: {"title": "x"}}))
    result = p.process_url(url(GOOD_ID))
    assert not result.ok and "輸出資料夾" in result.error


def test_process_local_file(source_wav, tmp_path):
    p = Pipeline(config.BALANCE2_PROFILE, tmp_path)
    result = p.process_file(source_wav, {"id": "x", "track": "晴天", "artist": "周杰倫"})
    assert result.ok, result.error
    assert_acceptance(result.output_path)


# --------------------------------------------------------------------------
# URL 解析
# --------------------------------------------------------------------------
@pytest.mark.parametrize("raw", [
    "https://www.youtube.com/watch?v=aqz-KE-bpKQ",
    "https://youtube.com/watch?v=aqz-KE-bpKQ&list=PL123&index=2",
    "https://youtu.be/aqz-KE-bpKQ?si=abc",
    "youtu.be/aqz-KE-bpKQ",
    "https://m.youtube.com/watch?v=aqz-KE-bpKQ",
    "https://music.youtube.com/watch?v=aqz-KE-bpKQ&feature=share",
    "https://www.youtube.com/shorts/aqz-KE-bpKQ",
    "https://www.youtube.com/live/aqz-KE-bpKQ?feature=share",
    "  https://www.youtube.com/embed/aqz-KE-bpKQ  ",
])
def test_parse_youtube_url_valid(raw):
    assert parse_youtube_url(raw).canonical == "https://www.youtube.com/watch?v=aqz-KE-bpKQ"


@pytest.mark.parametrize("raw", [
    "", "hello", "https://example.com/watch?v=aqz-KE-bpKQ", "https://www.youtube.com/",
    "https://www.youtube.com/watch?v=short", "https://www.youtube.com/playlist?list=PL123",
    "https://www.youtube.com/@channel",
])
def test_parse_youtube_url_invalid(raw):
    with pytest.raises(InvalidURLError):
        parse_youtube_url(raw)


def test_friendly_errors():
    from core.downloader import friendly_ytdlp_error as f
    assert "私人" in f("ERROR: [youtube] x: Private video. Sign in if you've been granted access")
    assert "網路" in f("ERROR: Unable to download webpage: <urlopen error [Errno 8] nodename nor servname>")
    assert "不存在" in f("ERROR: [youtube] x: Video unavailable")
    assert "年齡" in f("Sign in to confirm your age")


# --------------------------------------------------------------------------
# 診斷模式
# --------------------------------------------------------------------------
def test_diagnose_good_file(pipeline):
    result = pipeline.process_url(url(GOOD_ID))
    report = analyze_mp3(result.output_path)
    text = report.format()
    assert report.verdict == "PASS"
    for expected in ("ID3v1: None", "ID3v2.3: Yes", "ID3v2.4: No", "晴天", "周杰倫",
                     "UTF-16", "Valid", "44.1 kHz", "192 kbps", "Stereo", "PASS"):
        assert expected in text, expected


def test_diagnose_typical_youtube_download(dirty_mp3):
    """一般下載器的輸出：ID3v2.4 + UTF-8 + ID3v1 + 亂碼 → FAIL 並說明原因。"""
    report = analyze_mp3(dirty_mp3)
    text = report.format()
    assert report.verdict == "FAIL"
    assert "ID3v2.4: Yes" in text and "ID3v1: Yes" in text
    assert any("UTF-8" in i.message for i in report.issues)
    assert any("亂碼" in i.message for i in report.issues)


def test_diagnose_latin1_v1_chinese(blank_mp3):
    tags = ID3()
    tags.add(TIT2(encoding=0, text=["Sunny"]))
    tags.save(blank_mp3, v1=2, v2_version=3)
    data = bytearray(blank_mp3.read_bytes())
    data[-125:-121] = "晴天".encode("big5")  # ID3v1 title 欄位塞進非 ASCII
    blank_mp3.write_bytes(bytes(data))
    report = analyze_mp3(blank_mp3)
    assert report.verdict == "FAIL"
    assert any("ID3v1" in i.message for i in report.issues)


def test_diagnose_missing_file(tmp_path):
    assert analyze_mp3(tmp_path / "nope.mp3").verdict == "FAIL"


def test_diagnose_garbage_file(tmp_path):
    p = tmp_path / "x.mp3"
    p.write_bytes(b"\x00" * 1000)
    report = analyze_mp3(p)
    assert report.verdict == "FAIL"
    report.format()  # 不可丟出例外
