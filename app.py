"""Amazfit Balance 2 Music Converter — GUI 進入點。

    python app.py                      # 開啟 GUI
    python app.py --self-test          # 不開視窗，檢查環境並實際轉換一段測試音訊
    python app.py --self-test URL      # 同上，再實際下載一個 YouTube 網址

打包後的 App 也可以：
    dist/AmazfitMusicConverter.app/Contents/MacOS/AmazfitMusicConverter --self-test
"""
from __future__ import annotations

import logging
import platform
import subprocess
import sys
import tempfile
from pathlib import Path

import config
from core.converter import ensure_search_path
from core.logsetup import current_log_path, setup_logging


def self_test(url: str | None = None) -> int:
    """環境自我檢查，回傳 0 表示全部通過。"""
    from core.converter import check_ffmpeg
    from core.diagnostics import analyze_mp3
    from core.downloader import has_js_runtime
    from core.errors import ConverterError
    from core.pipeline import Pipeline

    ok = True

    def report(name: str, passed: bool, detail: str = "") -> None:
        nonlocal ok
        ok = ok and passed
        print(f"[{'✓' if passed else '✗'}] {name}" + (f"：{detail}" if detail else ""), flush=True)

    try:
        tools = check_ffmpeg()
        report("FFmpeg", True, tools.version)
    except ConverterError as e:
        report("FFmpeg", False, e.user_message)
        return 1
    report("JavaScript runtime (yt-dlp)", has_js_runtime(), "" if has_js_runtime() else config.JS_RUNTIME_HINT)
    try:
        import yt_dlp
        import yt_dlp_ejs  # noqa: F401
        report("yt-dlp", True, yt_dlp.version.__version__)
    except ImportError as e:
        report("yt-dlp", False, str(e))
    try:
        import tkinter
        root = tkinter.Tk()
        root.withdraw()
        detail = f"Tk {tkinter.TkVersion}"
        try:
            from tkinterdnd2 import TkinterDnD
            detail += f", tkdnd {TkinterDnD._require(root)}"
        except Exception as e:
            detail += f", 拖曳功能無法使用（{e}）"
        root.destroy()
        report("GUI (Tk)", True, detail)
    except Exception as e:
        report("GUI (Tk)", False, str(e))

    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "tone.wav"
        subprocess.run([tools.ffmpeg, "-loglevel", "error", "-y", "-f", "lavfi",
                        "-i", "sine=frequency=440:duration=3", str(src)], check=True)
        pipeline = Pipeline(config.BALANCE2_PROFILE, Path(tmp) / "out", tools=tools)
        mojibake = {"id": "selftest", "track": "æ™´å¤©", "artist": "周杰倫".encode().decode("latin-1")}
        result = pipeline.process_file(src, mojibake)
        report("轉換 + 修復亂碼 + ID3v2.3/UTF-16 + 驗證", result.ok,
               result.output_path.name if result.ok else result.error)
        if result.ok:
            diag = analyze_mp3(result.output_path, tools)
            report("診斷模式", diag.verdict == "PASS", f"{diag.title} / {diag.artist} / {diag.verdict}")
        if url:
            result = pipeline.process_url(url)
            report("YouTube 下載", result.ok,
                   f"{result.metadata.display_name}" if result.ok else result.error)
    print(f"\nLog：{current_log_path()}")
    return 0 if ok else 1


def main() -> int:
    ensure_search_path()
    setup_logging()
    log = logging.getLogger("app")
    log.info("%s %s 啟動 (Python %s, macOS %s)", config.APP_NAME, config.APP_VERSION,
             platform.python_version(), platform.mac_ver()[0] or "?")
    if "--self-test" in sys.argv:
        args = [a for a in sys.argv[1:] if not a.startswith("-")]
        return self_test(args[0] if args else None)
    try:
        from gui.main_window import run
        run()
    except Exception:
        log.exception("程式發生未處理的錯誤")
        raise
    return 0


if __name__ == "__main__":
    sys.exit(main())
