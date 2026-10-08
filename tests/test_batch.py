import threading

import pytest

import config
from core.batch import run_batch, split_urls
from core.pipeline import Pipeline
from tests.test_pipeline import GOOD_ID, MISSING_ID, MOJIBAKE_ID, FakeDownloader, url

A = "https://www.youtube.com/watch?v=aaaaaaaaaaa"
B = "https://youtu.be/bbbbbbbbbbb"


@pytest.mark.parametrize("text,expected", [
    (f"{A}\n{B}", [A, B]),
    (f"  {A}  \n\n\n{B}\n", [A, B]),
    (f"{A} {B}", [A, B]),
    (f"{A},{B}", [A, B]),
    (f"{A}\n{A}\n{B}", [A, B]),  # 重複的只保留一個
    (f"1. {A}\n2. {B}", [A, B]),  # 清單編號忽略
    (f"- {A}\n• {B}", [A, B]),
    (f"<{A}>", [A]),
    ("", []),
    ("   \n  ", []),
    ("not-a-url", ["not-a-url"]),  # 保留，讓 pipeline 回報錯誤
])
def test_split_urls(text, expected):
    assert split_urls(text) == expected


def test_split_urls_glued_together():
    assert split_urls(f"{A}{B}") == [A + B]  # 黏在一起無法可靠拆開，交給 pipeline 回報


def _pipeline(source_wav, out):
    infos = {GOOD_ID: {"title": "周杰倫 - 晴天"}, MOJIBAKE_ID: {"title": "æ™´å¤©"}}
    return Pipeline(config.BALANCE2_PROFILE, out, downloader=FakeDownloader(source_wav, infos))


def test_run_batch_events_and_isolation(source_wav, tmp_path):
    events = []
    urls = [url(GOOD_ID), url(MISSING_ID), url(MOJIBAKE_ID), "garbage"]
    results = run_batch(urls, config.BALANCE2_PROFILE, tmp_path, events.append,
                        pipeline=_pipeline(source_wav, tmp_path))
    assert [r.ok for r in results] == [True, False, True, False]
    starts = [(e.index, e.total) for e in events if e.kind == "job_start"]
    assert starts == [(1, 4), (2, 4), (3, 4), (4, 4)]
    assert events[-1].kind == "finished" and len(events[-1].results) == 4
    assert any(e.kind == "progress" for e in events)


def test_run_batch_stop(source_wav, tmp_path):
    stop = threading.Event()
    events = []

    def emit(e):
        events.append(e)
        if e.kind == "job_done" and e.index == 1:
            stop.set()

    results = run_batch([url(GOOD_ID)] * 3, config.BALANCE2_PROFILE, tmp_path, emit, stop,
                        pipeline=_pipeline(source_wav, tmp_path))
    assert len(results) == 1
    assert events[-1].kind == "finished" and events[-1].total == 3


def test_run_batch_always_finishes_on_unexpected_error(tmp_path):
    class Boom:
        def process_url(self, *a, **k):
            raise RuntimeError("boom")

    events = []
    run_batch([A, B], config.BALANCE2_PROFILE, tmp_path, events.append, pipeline=Boom())
    assert events[-1].kind == "finished"
