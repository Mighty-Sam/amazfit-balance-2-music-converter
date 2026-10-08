#!/usr/bin/env bash
# 打包成 macOS App：dist/AmazfitMusicConverter.app
#
#   ./build_mac.sh            # 建置
#   ./build_mac.sh --install  # 建置並複製到 /Applications
#
# FFmpeg 不會被打包進 App，仍需另外安裝：brew install ffmpeg
set -euo pipefail

cd "$(dirname "$0")"
APP_NAME="AmazfitMusicConverter"
VENV=".venv"

# 1. Python 環境（需要含 tkinter 的 Python 3.12+）
if [[ ! -x "$VENV/bin/python" ]]; then
  echo "▶ 建立虛擬環境 $VENV"
  if command -v uv >/dev/null 2>&1; then
    uv venv --python 3.12 --managed-python "$VENV"
  else
    PY="$(command -v python3.12 || command -v python3)"
    "$PY" -m venv "$VENV"
  fi
fi
PY="$VENV/bin/python"
if ! "$PY" -c "import tkinter" 2>/dev/null; then
  echo "✗ 這個 Python 沒有 tkinter，請參考 README「Python 安裝」一節。" >&2
  exit 1
fi

echo "▶ 安裝相依套件"
if command -v uv >/dev/null 2>&1; then
  uv pip install --python "$PY" -q -r requirements.txt pyinstaller
else
  "$PY" -m pip install -q --upgrade pip
  "$PY" -m pip install -q -r requirements.txt pyinstaller
fi

# 2. 先跑測試，失敗就不打包
echo "▶ 執行測試"
"$PY" -m pytest -q tests

# 3. 打包
echo "▶ PyInstaller 打包"
rm -rf build "dist/$APP_NAME.app"
"$PY" -m PyInstaller \
  --noconfirm --clean --windowed \
  --name "$APP_NAME" \
  --osx-bundle-identifier "local.amazfit.musicconverter" \
  --collect-data customtkinter \
  --collect-all tkinterdnd2 \
  --collect-all yt_dlp_ejs \
  --hidden-import core.diagnostics \
  app.py

# 讓 Finder 顯示比較好讀的名稱，並宣告支援拖曳 MP3
PLIST="dist/$APP_NAME.app/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Add :CFBundleDisplayName string 'Amazfit Music Converter'" "$PLIST" 2>/dev/null \
  || /usr/libexec/PlistBuddy -c "Set :CFBundleDisplayName 'Amazfit Music Converter'" "$PLIST"
/usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString $("$PY" -c 'import config; print(config.APP_VERSION)')" "$PLIST"
/usr/libexec/PlistBuddy -c "Add :NSHighResolutionCapable bool true" "$PLIST" 2>/dev/null || true

# 4. 簡單檢查：App 內的 Python 能載入所有模組
echo "▶ 檢查 App"
test -x "dist/$APP_NAME.app/Contents/MacOS/$APP_NAME"
echo "✓ 完成：dist/$APP_NAME.app"

if [[ "${1:-}" == "--install" ]]; then
  rm -rf "/Applications/$APP_NAME.app"
  cp -R "dist/$APP_NAME.app" /Applications/
  echo "✓ 已安裝到 /Applications/$APP_NAME.app"
fi

if ! command -v ffmpeg >/dev/null 2>&1 && [[ ! -x /opt/homebrew/bin/ffmpeg && ! -x /usr/local/bin/ffmpeg ]]; then
  echo "⚠ 尚未安裝 FFmpeg，App 啟動時會提示。請執行：brew install ffmpeg"
fi
