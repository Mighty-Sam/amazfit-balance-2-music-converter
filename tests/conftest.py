import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.converter import ensure_search_path, find_executable  # noqa: E402

ensure_search_path()
FFMPEG = find_executable("ffmpeg")
requires_ffmpeg = pytest.mark.skipif(FFMPEG is None, reason="需要 ffmpeg")


def make_audio(path: Path, *extra_args: str, duration: float = 2.0) -> Path:
    """用 ffmpeg 產生一段測試音訊（440 Hz 正弦波）。"""
    cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
           "-i", f"sine=frequency=440:duration={duration}", *extra_args, str(path)]
    subprocess.run(cmd, check=True)
    return path


@pytest.fixture(scope="session")
def _base_mp3(tmp_path_factory) -> Path:
    if FFMPEG is None:
        pytest.skip("需要 ffmpeg")
    path = tmp_path_factory.mktemp("base") / "base.mp3"
    return make_audio(path, "-ac", "2", "-ar", "44100", "-c:a", "libmp3lame", "-b:a", "192k",
                      "-id3v2_version", "0", "-write_id3v1", "0")


@pytest.fixture
def blank_mp3(_base_mp3, tmp_path) -> Path:
    """沒有任何標籤的 MP3（每個測試一份新的複本）。"""
    dst = tmp_path / "blank.mp3"
    shutil.copy(_base_mp3, dst)
    return dst


@pytest.fixture
def dirty_mp3(tmp_path) -> Path:
    """模擬 YouTube 下載器產生的檔案：同時有 ID3v2.4 (UTF-8) 與 ID3v1。"""
    if FFMPEG is None:
        pytest.skip("需要 ffmpeg")
    return make_audio(tmp_path / "dirty.mp3", "-ac", "2", "-ar", "48000", "-c:a", "libmp3lame",
                      "-b:a", "128k", "-id3v2_version", "4", "-write_id3v1", "1",
                      "-metadata", "title=æ™´å¤©", "-metadata", "artist=Jay Chou",
                      "-metadata", "comment=old comment")


@pytest.fixture
def source_wav(tmp_path) -> Path:
    if FFMPEG is None:
        pytest.skip("需要 ffmpeg")
    return make_audio(tmp_path / "source.wav", "-ac", "1", "-ar", "48000")
