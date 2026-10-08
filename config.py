"""集中管理所有設定。

其他模組不應該硬編碼任何音訊 / Metadata 參數，一律從這裡讀取。
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, replace
from pathlib import Path

APP_NAME = "Amazfit Music Converter"
APP_ID = "AmazfitMusicConverter"
APP_VERSION = "1.0.0"

# ---------------------------------------------------------------------------
# 路徑
# ---------------------------------------------------------------------------
IS_FROZEN = getattr(sys, "frozen", False)  # PyInstaller 打包後為 True
PROJECT_DIR = Path(__file__).resolve().parent

DEFAULT_OUTPUT_DIR = Path.home() / "Music" / "Amazfit"

SETTINGS_FILE = Path.home() / "Library" / "Application Support" / APP_ID / "settings.json"

# 開發模式寫到專案的 logs/；打包成 .app 後 bundle 是唯讀的，改寫到 ~/Library/Logs
LOG_DIR = (Path.home() / "Library" / "Logs" / APP_ID) if IS_FROZEN else (PROJECT_DIR / "logs")
LOG_FILE_PATTERN = "converter-{date}.log"  # date = YYYY-MM-DD

# 從 Finder 啟動的 .app 不會繼承 shell 的 PATH，所以額外搜尋 Homebrew 的位置
EXTRA_BIN_DIRS = ("/opt/homebrew/bin", "/usr/local/bin", "/opt/local/bin")

FFMPEG_INSTALL_HINT = (
    "找不到 FFmpeg。\n\n"
    "本工具需要 ffmpeg 與 ffprobe 來轉換與檢查音訊。\n\n"
    "macOS 安裝方式（Homebrew）：\n"
    "  1. 若尚未安裝 Homebrew，請先到 https://brew.sh 依指示安裝\n"
    "  2. 打開「終端機」執行：brew install ffmpeg\n"
    "  3. 安裝完成後重新啟動本程式"
)

JS_RUNTIME_HINT = (
    "找不到 JavaScript runtime（deno / node）。\n"
    "新版 YouTube 需要它才能取得音訊格式，建議執行：brew install deno"
)

# ---------------------------------------------------------------------------
# 音訊
# ---------------------------------------------------------------------------
BITRATE_CHOICES_KBPS = (128, 192, 256, 320)
DEFAULT_BITRATE_KBPS = 192

YTDLP_FORMAT = "bestaudio/best"
FFMPEG_TIMEOUT_SEC = 15 * 60
FFPROBE_TIMEOUT_SEC = 60

# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------
# mutagen 的 Encoding 常數：0 = ISO-8859-1, 1 = UTF-16 (含 BOM), 2 = UTF-16BE, 3 = UTF-8
ID3_ENCODING_UTF16 = 1

# 標題常見格式「周杰倫 Jay Chou【晴天 Sunny Day】」：
# 開啟後若名稱是「中日韓文字 + 空白 + 純英文」，只保留中日韓部分，手錶上比較好讀。
# 只套用在從影片標題解析出來的名稱，不會改動 YouTube 提供的 artist/track 欄位。
STRIP_BILINGUAL_LATIN_SUFFIX = True

# 檔名長度上限（以 UTF-8 bytes 計，APFS / FAT32 都是 255，保留空間給副檔名與序號）
MAX_FILENAME_BYTES = 200
OUTPUT_EXTENSION = ".mp3"


# ---------------------------------------------------------------------------
# 轉換設定檔
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ConvertProfile:
    name: str
    bitrate_kbps: int
    sample_rate_hz: int = 44100
    channels: int = 2
    codec: str = "libmp3lame"
    id3_version: int = 3  # ID3v2.3
    id3_text_encoding: int = ID3_ENCODING_UTF16
    write_id3v1: bool = False  # ID3v1 只能存 Latin-1，中文一定會壞，不寫
    strip_existing_tags: bool = True  # 寫入前清除所有舊 ID3v1 / ID3v2 / APE
    unicode_form: str = "NFC"
    validate: bool = True
    overwrite_existing: bool = False  # False = 自動改名為「xxx (2).mp3」


# Amazfit Balance 2 相容模式：固定參數，不允許 GUI 修改
BALANCE2_PROFILE = ConvertProfile(name="Amazfit Balance 2 相容模式", bitrate_kbps=192)


def custom_profile(bitrate_kbps: int) -> ConvertProfile:
    """關閉相容模式時使用：只開放調整位元率，Metadata 仍維持最安全的格式。"""
    if bitrate_kbps not in BITRATE_CHOICES_KBPS:
        raise ValueError(f"不支援的位元率：{bitrate_kbps}")
    return replace(BALANCE2_PROFILE, name="自訂", bitrate_kbps=bitrate_kbps)


def make_profile(compat_mode: bool, bitrate_kbps: int = DEFAULT_BITRATE_KBPS,
                 overwrite_existing: bool = False) -> ConvertProfile:
    profile = BALANCE2_PROFILE if compat_mode else custom_profile(bitrate_kbps)
    return replace(profile, overwrite_existing=overwrite_existing)
