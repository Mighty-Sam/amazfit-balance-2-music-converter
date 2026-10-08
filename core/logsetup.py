"""Log：每天一個檔案 logs/converter-YYYY-MM-DD.log。"""
from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

import config

_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


class DailyFileHandler(logging.Handler):
    """每筆紀錄依「當天日期」寫到對應檔案；程式跨日執行也會自動換檔。"""

    def __init__(self, log_dir: Path):
        super().__init__()
        self.log_dir = Path(log_dir)
        self._current_date: date | None = None
        self._handler: logging.FileHandler | None = None

    def current_path(self) -> Path:
        return self.log_dir / config.LOG_FILE_PATTERN.format(date=date.today().isoformat())

    def emit(self, record: logging.LogRecord) -> None:
        today = date.today()
        if today != self._current_date or self._handler is None:
            if self._handler:
                self._handler.close()
            self.log_dir.mkdir(parents=True, exist_ok=True)
            self._handler = logging.FileHandler(self.current_path(), encoding="utf-8")
            self._handler.setFormatter(self.formatter)
            self._current_date = today
        self._handler.emit(record)

    def close(self) -> None:
        if self._handler:
            self._handler.close()
        super().close()


class _HomeDirFilter(logging.Filter):
    """把 Log 中的家目錄路徑換成 ~，避免記錄使用者帳號名稱。"""

    _home = str(Path.home())

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        if self._home in msg:
            record.msg = msg.replace(self._home, "~")
            record.args = None
        return True


_daily_handler: DailyFileHandler | None = None


def setup_logging(log_dir: Path = config.LOG_DIR, level: int = logging.DEBUG) -> DailyFileHandler:
    global _daily_handler
    if _daily_handler is not None:
        return _daily_handler
    handler = DailyFileHandler(log_dir)
    handler.setFormatter(logging.Formatter(_FORMAT))
    handler.setLevel(level)
    handler.addFilter(_HomeDirFilter())
    root = logging.getLogger()
    root.setLevel(level)
    root.addHandler(handler)
    _daily_handler = handler
    return handler


def current_log_path() -> Path:
    return (_daily_handler or DailyFileHandler(config.LOG_DIR)).current_path()
