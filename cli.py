"""命令列介面。

  轉換 YouTube（可多個）：
    python cli.py "https://www.youtube.com/watch?v=XXXXXXXXXXX" [URL ...]

  轉換本機音訊檔並指定 Metadata：
    python cli.py --from-file song.wav --title 晴天 --artist 周杰倫

  分析 MP3（診斷模式）：
    python cli.py --analyze "周杰倫 - 晴天.mp3"
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import config
from core.converter import ensure_search_path
from core.errors import ConverterError
from core.logsetup import current_log_path, setup_logging
from core.pipeline import Pipeline, ProgressEvent


def _print_progress(event: ProgressEvent) -> None:
    if event.status == "progress":
        return
    mark = {"start": "…", "done": "✓", "fail": "✗", "skip": "-"}.get(event.status, " ")
    extra = f"  {event.message}" if event.message else ""
    print(f"  [{mark}] {event.step.value}{extra}", flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=f"{config.APP_NAME}（CLI）")
    parser.add_argument("urls", nargs="*", help="YouTube 網址（可多個）")
    parser.add_argument("-o", "--output", default=str(config.DEFAULT_OUTPUT_DIR), help="輸出資料夾")
    parser.add_argument("-b", "--bitrate", type=int, choices=config.BITRATE_CHOICES_KBPS,
                        default=config.DEFAULT_BITRATE_KBPS, help="位元率 kbps（關閉相容模式時有效）")
    parser.add_argument("--no-compat", action="store_true", help="關閉 Amazfit Balance 2 相容模式")
    parser.add_argument("--overwrite", action="store_true", help="覆蓋已存在的檔案")
    parser.add_argument("--from-file", type=Path, help="改用本機音訊檔作為來源")
    parser.add_argument("--title", help="搭配 --from-file：歌曲名稱")
    parser.add_argument("--artist", default="", help="搭配 --from-file：歌手")
    parser.add_argument("--album", default="", help="搭配 --from-file：專輯")
    parser.add_argument("--analyze", type=Path, metavar="MP3", help="診斷模式：分析 MP3")
    args = parser.parse_args(argv)

    ensure_search_path()
    setup_logging()

    if args.analyze:
        from core.diagnostics import analyze_mp3
        report = analyze_mp3(args.analyze)
        print(report.format())
        return 0 if report.verdict != "FAIL" else 1

    profile = config.make_profile(not args.no_compat, args.bitrate, args.overwrite)
    pipeline = Pipeline(profile, Path(args.output))

    jobs: list[tuple[str, callable]] = []
    if args.from_file:
        info = {"id": args.from_file.stem, "title": args.title or args.from_file.stem}
        if args.title and args.artist:
            info.update(track=args.title, artist=args.artist)
        if args.album:
            info["album"] = args.album
        jobs.append((str(args.from_file), lambda cb: pipeline.process_file(args.from_file, info, cb)))
    for url in args.urls:
        jobs.append((url, lambda cb, u=url: pipeline.process_url(u, cb)))
    if not jobs:
        parser.print_help()
        return 2

    try:
        from core.converter import check_ffmpeg
        check_ffmpeg()
    except ConverterError as e:
        print(e.user_message, file=sys.stderr)
        return 3

    failures = 0
    for i, (label, job) in enumerate(jobs, 1):
        print(f"\n[{i} / {len(jobs)}] {label}")
        result = job(_print_progress)
        print()
        print(result.summary())
        if not result.ok:
            failures += 1
    print(f"\n成功 {len(jobs) - failures} / {len(jobs)}，Log：{current_log_path()}")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
