"""GUI 冒煙測試：實際建立視窗、貼上網址、跑完批次、執行診斷（YouTube 以假 downloader 取代）。

需要圖形環境；無法建立視窗時自動略過。
"""
import time

import pytest

import config
from core import batch
from core.pipeline import Pipeline
from tests.test_pipeline import GOOD_ID, MISSING_ID, MOJIBAKE_ID, FakeDownloader, url


@pytest.fixture
def window(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "SETTINGS_FILE", tmp_path / "settings.json")
    try:
        from gui.main_window import MainWindow
        win = MainWindow()
    except Exception as e:  # 沒有 display
        pytest.skip(f"無法建立視窗：{e}")
    win.withdraw()
    monkeypatch.setattr("gui.main_window.messagebox.showerror", lambda *a, **k: None)
    monkeypatch.setattr("gui.main_window.messagebox.showwarning", lambda *a, **k: None)
    yield win
    win.destroy()


def pump(win, until, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        win.update()
        if until():
            return
        time.sleep(0.02)
    raise TimeoutError


def test_batch_through_gui(window, monkeypatch, source_wav, tmp_path):
    infos = {GOOD_ID: {"title": "周杰倫 Jay Chou【晴天 Sunny Day】Official Music Video"},
             MOJIBAKE_ID: {"title": "æ™´å¤©", "artist": "å‘¨æ\x9d°å€«"}}
    fake = FakeDownloader(source_wav, infos)
    real_pipeline = Pipeline
    monkeypatch.setattr(batch, "Pipeline", lambda *a, **k: real_pipeline(*a, downloader=fake, **k))

    window.output_dir.set(str(tmp_path / "out"))
    window.url_box.insert("1.0", f"{url(GOOD_ID)}\n{url(MISSING_ID)}\n\n{url(MOJIBAKE_ID)}\nhttps://example.com/x\n")
    pump(window, lambda: window.tools is not None, 10)
    window._start()
    pump(window, lambda: window.worker is None)

    text = window.result_box.get("1.0", "end")
    assert "✓ [1/4] 周杰倫 - 晴天" in text
    assert "✗ [2/4]" in text and "影片不存在" in text
    assert "✓ [3/4] 周杰倫 - 晴天" in text
    assert "✗ [4/4]" in text and "不是 YouTube" in text
    assert "完成 2 / 4 首" in text
    assert window.counter_label.cget("text") == "4 / 4"
    assert sorted(p.name for p in (tmp_path / "out").glob("*.mp3")) == ["周杰倫 - 晴天 (2).mp3", "周杰倫 - 晴天.mp3"]


def test_single_success_message(window, monkeypatch, source_wav, tmp_path):
    fake = FakeDownloader(source_wav, {GOOD_ID: {"title": "周杰倫 - 晴天"}})
    real_pipeline = Pipeline
    monkeypatch.setattr(batch, "Pipeline", lambda *a, **k: real_pipeline(*a, downloader=fake, **k))
    window.output_dir.set(str(tmp_path))
    window.url_box.insert("1.0", url(GOOD_ID))
    pump(window, lambda: window.tools is not None, 10)
    window._start()
    pump(window, lambda: window.worker is None)
    text = window.result_box.get("1.0", "end")
    assert text.startswith("完成！\n\n歌曲：\n周杰倫 - 晴天\n\n輸出：\n")
    assert text.strip().endswith("周杰倫 - 晴天.mp3")
    assert all(lbl.cget("text").startswith("✓") for lbl in window.step_labels.values())


def test_analyze_tab(window, tmp_path, source_wav):
    result = Pipeline(config.BALANCE2_PROFILE, tmp_path).process_file(
        source_wav, {"id": "x", "track": "晴天", "artist": "周杰倫"})
    window._analyze(result.output_path)
    text = window.report_box.get("1.0", "end")
    assert "ID3v2.3: Yes" in text and "PASS" in text
    assert "PASS" in window.verdict_label.cget("text")


def test_compat_mode_locks_bitrate(window):
    window.compat_mode.set(False)
    window._on_compat_toggle()
    window.bitrate.set("320")
    window.compat_mode.set(True)
    window._on_compat_toggle()
    assert window.bitrate.get() == "192"
    assert window.bitrate_selector.cget("state") == "disabled"
