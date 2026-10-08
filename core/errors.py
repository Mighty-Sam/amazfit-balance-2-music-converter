"""統一的錯誤型別：每個錯誤都帶有給使用者看的中文訊息與給 Log 的技術細節。"""
from __future__ import annotations


class ConverterError(Exception):
    """所有可預期錯誤的基底類別。

    user_message: 顯示在 GUI 上、一般人看得懂的訊息
    detail:       寫進 Log 的技術細節
    """

    def __init__(self, user_message: str, detail: str = ""):
        super().__init__(user_message)
        self.user_message = user_message
        self.detail = detail

    def __str__(self) -> str:
        return self.user_message


class InvalidURLError(ConverterError):
    pass


class DownloadError(ConverterError):
    pass


class FFmpegNotFoundError(ConverterError):
    pass


class ConversionError(ConverterError):
    pass


class MetadataError(ConverterError):
    pass


class OutputFileError(ConverterError):
    pass
