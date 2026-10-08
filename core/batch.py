"""批次處理：一次多個網址，依序處理，某一首失敗不影響其他首。"""
from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import config
from core import converter
from core.pipeline import JobResult, Pipeline, ProgressEvent

log = logging.getLogger(__name__)

_URL_RE = re.compile(r"(?:https?://)?(?:[\w-]+\.)*(?:youtube\.com|youtu\.be|youtube-nocookie\.com)/\S+",
                     re.IGNORECASE)

_LIST_MARKER_RE = re.compile(r"^(?:\d+[.)、:：]?|[-*•・#]+)$")  # 「1.」「2)」「-」「•」等清單編號


def split_urls(text: str) -> list[str]:
    """從貼上的文字中取出所有網址：一行一個、空白或逗號分隔都可以；重複的只保留一個。

    不是 YouTube 的非空白片段也保留，讓 pipeline 回報「這不是 YouTube 網址」而不是默默忽略。
    """
    urls: list[str] = []
    for token in re.split(r"[\s,，、]+", text or ""):
        token = token.strip().strip("<>\"'()（）")
        if not token or _LIST_MARKER_RE.match(token):
            continue
        found = _URL_RE.findall(token)
        for u in found or [token]:
            if u not in urls:
                urls.append(u)
    return urls


@dataclass
class BatchEvent:
    kind: str  # "job_start" | "progress" | "job_done" | "finished"
    index: int = 0
    total: int = 0
    url: str = ""
    progress: ProgressEvent | None = None
    result: JobResult | None = None
    results: list[JobResult] = field(default_factory=list)


def run_batch(urls: list[str], profile: config.ConvertProfile, output_dir: Path,
              emit: Callable[[BatchEvent], None], stop: threading.Event | None = None,
              tools: converter.FFmpegTools | None = None,
              pipeline: Pipeline | None = None) -> list[JobResult]:
    """依序處理所有網址。永遠會送出最後的 "finished" 事件（即使發生非預期錯誤）。"""
    results: list[JobResult] = []
    total = len(urls)
    log.info("批次開始：%d 個網址，設定=%s", total, profile.name)
    try:
        pipeline = pipeline or Pipeline(profile, output_dir, tools=tools)
        for i, url in enumerate(urls, 1):
            if stop is not None and stop.is_set():
                log.info("使用者停止，剩 %d 個未處理", total - i + 1)
                break
            emit(BatchEvent("job_start", i, total, url))
            result = pipeline.process_url(
                url, lambda e, i=i: emit(BatchEvent("progress", i, total, url, progress=e)))
            results.append(result)
            emit(BatchEvent("job_done", i, total, url, result=result))
    except Exception:
        log.exception("批次處理發生非預期錯誤")
    finally:
        ok = sum(r.ok for r in results)
        log.info("批次結束：成功 %d / %d", ok, total)
        emit(BatchEvent("finished", total=total, results=results))
    return results
