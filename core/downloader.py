"""yt-dlp：驗證 YouTube 網址、取得影片資訊、下載最佳音訊（不做任何後製）。

下載下來的原始音訊只放在暫存資料夾，之後一律交給 FFmpeg 重新轉成 MP3。
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, urlparse

import config
from core.converter import find_executable
from core.errors import DownloadError, InvalidURLError

log = logging.getLogger(__name__)

_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
_YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com",
                  "youtube-nocookie.com", "www.youtube-nocookie.com"}
_SHORT_HOSTS = {"youtu.be", "www.youtu.be"}
_PATH_ID_PREFIXES = ("/shorts/", "/live/", "/embed/", "/v/", "/e/")


@dataclass(frozen=True)
class YouTubeURL:
    video_id: str

    @property
    def canonical(self) -> str:
        return f"https://www.youtube.com/watch?v={self.video_id}"


def parse_youtube_url(url: str) -> YouTubeURL:
    """接受 watch / youtu.be / shorts / live / embed / music.youtube.com 等格式。

    回傳只含影片 ID 的標準網址（去掉 &list=、&si= 等追蹤或播放清單參數）。
    """
    raw = (url or "").strip()
    if not raw:
        raise InvalidURLError("網址是空的")
    if not re.match(r"^https?://", raw, re.IGNORECASE):
        raw = "https://" + raw
    try:
        parsed = urlparse(raw)
    except ValueError as e:
        raise InvalidURLError("網址格式不正確", str(e)) from e
    host = (parsed.hostname or "").lower()

    video_id = ""
    if host in _SHORT_HOSTS:
        video_id = parsed.path.lstrip("/").split("/")[0]
    elif host in _YOUTUBE_HOSTS:
        if parsed.path in ("/watch", "/watch/"):
            video_id = (parse_qs(parsed.query).get("v") or [""])[0]
        else:
            for prefix in _PATH_ID_PREFIXES:
                if parsed.path.startswith(prefix):
                    video_id = parsed.path[len(prefix):].split("/")[0]
                    break
        if not video_id and "list" in parse_qs(parsed.query):
            raise InvalidURLError("這是播放清單網址，請貼上單一影片的網址（可以一行貼一個）")
    else:
        raise InvalidURLError("這不是 YouTube 網址", f"host={host!r}")

    if not _VIDEO_ID_RE.match(video_id):
        raise InvalidURLError("網址中找不到有效的 YouTube 影片 ID")
    return YouTubeURL(video_id)


def safe_url_for_log(url: str) -> str:
    """Log 只記錄標準化後的網址，避免把 si=、token 等參數寫進 Log。"""
    try:
        return parse_youtube_url(url).canonical
    except InvalidURLError:
        try:
            p = urlparse(url.strip())
            return f"{p.scheme}://{p.hostname or ''}{p.path}" if p.hostname else "<invalid>"
        except ValueError:
            return "<invalid>"


# yt-dlp 錯誤訊息 → 使用者看得懂的說明（依序比對，先比對到的優先）
_ERROR_PATTERNS: list[tuple[str, str]] = [
    (r"private video", "這是私人影片，無法存取"),
    (r"sign in to confirm your age|age[- ]restricted|inappropriate for some users",
     "這部影片有年齡限制，需要登入 YouTube 才能觀看，本工具不支援"),
    (r"not a bot", "YouTube 要求驗證「不是機器人」，請稍後再試或更新 yt-dlp"),
    (r"members[- ]only|join this channel", "這是頻道會員限定影片，無法下載"),
    (r"premieres? in|live event will begin|is upcoming", "影片尚未開始（首播或直播還沒開始）"),
    (r"not available in your country|blocked it in your country|geo.?restrict", "這部影片在你的地區無法觀看"),
    (r"copyright|removed by the uploader|account .*terminated|has been removed",
     "影片已被移除"),
    (r"video unavailable|this video is (?:not |un)available|does not exist|incomplete youtube id",
     "影片不存在或目前無法觀看"),
    (r"unsupported url", "網址格式不支援"),
    (r"http error 403", "YouTube 拒絕下載（HTTP 403），請更新 yt-dlp：pip install -U yt-dlp"),
    (r"http error 429|too many requests", "YouTube 暫時限制下載次數（HTTP 429），請稍後再試"),
    (r"requested format is not available|no video formats|only images are available",
     "找不到可下載的音訊格式，請更新 yt-dlp 或安裝 deno（brew install deno）"),
    (r"timed out|urlopen error|connection|network is unreachable|name resolution|"
     r"nodename nor servname|getaddrinfo|unable to download (?:webpage|api)|http error 5\d\d",
     "網路連線失敗，請確認網路後再試一次"),
]


def friendly_ytdlp_error(message: str) -> str:
    msg = message.lower()
    for pattern, friendly in _ERROR_PATTERNS:
        if re.search(pattern, msg):
            return friendly
    return "無法從 YouTube 取得這部影片"


class _YtdlpLogger:
    """把 yt-dlp 的輸出導進我們的 Log，而不是印到終端機。"""

    def debug(self, msg: str) -> None:
        if not msg.startswith("[debug] "):
            log.debug("yt-dlp: %s", msg)

    def info(self, msg: str) -> None:
        log.debug("yt-dlp: %s", msg)

    def warning(self, msg: str) -> None:
        log.warning("yt-dlp: %s", msg)

    def error(self, msg: str) -> None:
        log.error("yt-dlp: %s", msg)


def has_js_runtime() -> bool:
    return any(find_executable(n) for n in ("deno", "node", "bun"))


def _js_runtimes() -> dict:
    """YouTube 需要 JavaScript runtime 才能取得完整格式；有哪個就啟用哪個。"""
    runtimes = {}
    for name in ("deno", "node", "bun"):
        path = find_executable(name)
        if path:
            runtimes[name] = {"path": path}
    return runtimes or {"deno": {}}


ProgressCallback = Callable[[float, str], None]  # (0.0–1.0, 說明文字)


class YouTubeDownloader:
    def __init__(self, ffmpeg_location: str | None = None):
        self.ffmpeg_location = ffmpeg_location

    def _options(self, dest_dir: Path | None = None, on_progress: ProgressCallback | None = None) -> dict:
        opts = {
            "format": config.YTDLP_FORMAT,
            "noplaylist": True,
            "quiet": True,
            "no_warnings": False,
            "noprogress": True,
            "logger": _YtdlpLogger(),
            "socket_timeout": 30,
            "retries": 3,
            "fragment_retries": 3,
            "overwrites": True,
            "js_runtimes": _js_runtimes(),
            # 不使用 yt-dlp 的任何後製（轉檔 / 寫 metadata / 封面），全部由我們自己處理
            "postprocessors": [],
        }
        if self.ffmpeg_location:
            opts["ffmpeg_location"] = self.ffmpeg_location
        if dest_dir is not None:
            # 暫存檔名只用影片 ID，避免任何非 ASCII 檔名問題
            opts["outtmpl"] = {"default": str(Path(dest_dir) / "%(id)s.%(ext)s")}
            opts["paths"] = {"home": str(dest_dir), "temp": str(dest_dir)}
        if on_progress:
            opts["progress_hooks"] = [self._make_hook(on_progress)]
        return opts

    @staticmethod
    def _make_hook(on_progress: ProgressCallback):
        def hook(d: dict) -> None:
            if d.get("status") == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                done = d.get("downloaded_bytes") or 0
                if total:
                    on_progress(min(1.0, done / total), f"{done / 1e6:.1f} / {total / 1e6:.1f} MB")
            elif d.get("status") == "finished":
                on_progress(1.0, "下載完成")
        return hook

    def fetch_info(self, url: str) -> dict:
        import yt_dlp

        yurl = parse_youtube_url(url)
        try:
            with yt_dlp.YoutubeDL(self._options()) as ydl:
                info = ydl.extract_info(yurl.canonical, download=False)
        except yt_dlp.utils.DownloadError as e:
            raise DownloadError(friendly_ytdlp_error(str(e)), str(e)) from e
        except Exception as e:  # yt-dlp 內部偶爾會丟出非預期例外
            raise DownloadError(friendly_ytdlp_error(str(e)), f"{type(e).__name__}: {e}") from e
        if not info:
            raise DownloadError("無法取得影片資訊")
        if info.get("_type") == "playlist":
            raise InvalidURLError("這是播放清單網址，請貼上單一影片的網址")
        if info.get("is_live"):
            raise DownloadError("這是正在進行的直播，無法下載")
        return info

    def download_audio(self, info: dict, dest_dir: Path,
                       on_progress: ProgressCallback | None = None) -> Path:
        import yt_dlp

        dest_dir = Path(dest_dir)
        try:
            with yt_dlp.YoutubeDL(self._options(dest_dir, on_progress)) as ydl:
                result = ydl.process_ie_result(dict(info), download=True)
        except yt_dlp.utils.DownloadError as e:
            raise DownloadError(friendly_ytdlp_error(str(e)), str(e)) from e
        except Exception as e:
            raise DownloadError("音訊下載失敗", f"{type(e).__name__}: {e}") from e

        for item in (result or {}).get("requested_downloads") or []:
            path = Path(item.get("filepath") or "")
            if path.is_file() and path.stat().st_size > 0:
                return path
        # 找不到 filepath 時退而求其次：暫存資料夾裡以影片 ID 命名的檔案
        candidates = [p for p in dest_dir.glob(f"{info.get('id')}.*")
                      if p.suffix not in (".part", ".ytdl") and p.stat().st_size > 0]
        if candidates:
            return candidates[0]
        raise DownloadError("音訊下載失敗（沒有產生檔案）", f"dest_dir={dest_dir}")
