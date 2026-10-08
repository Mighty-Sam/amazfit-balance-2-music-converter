"""桌面 GUI（customtkinter）。

所有耗時工作在背景執行緒進行，透過 queue 把進度傳回 Tk 主執行緒更新畫面，
避免視窗卡住。拖曳 MP3 需要 tkinterdnd2，沒有安裝時自動退回「選擇檔案」按鈕。
"""
from __future__ import annotations

import json
import logging
import queue
import subprocess
import threading
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

import config
from core import converter
from core.batch import BatchEvent, run_batch, split_urls
from core.diagnostics import analyze_mp3
from core.downloader import has_js_runtime
from core.errors import ConverterError
from core.logsetup import current_log_path
from core.pipeline import STEPS, ProgressEvent, Step

log = logging.getLogger(__name__)

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    _DND_BASE = (TkinterDnD.DnDWrapper,)
except Exception:  # 套件不存在或 tkdnd 無法載入
    TkinterDnD = None
    DND_FILES = None
    _DND_BASE = ()

COLOR_OK = ("#1a7f37", "#3fb950")
COLOR_FAIL = ("#cf222e", "#f85149")
COLOR_WARN = ("#9a6700", "#d29922")
COLOR_ACTIVE = ("#0969da", "#58a6ff")
COLOR_MUTED = ("gray45", "gray60")

STEP_ICONS = {"pending": "○", "start": "●", "progress": "●", "done": "✓", "fail": "✗", "skip": "－"}
STEP_COLORS = {"pending": COLOR_MUTED, "start": COLOR_ACTIVE, "progress": COLOR_ACTIVE,
               "done": COLOR_OK, "fail": COLOR_FAIL, "skip": COLOR_MUTED}


def short_path(path: Path | str) -> str:
    text = str(path)
    home = str(Path.home())
    return "~" + text[len(home):] if text.startswith(home) else text


def load_settings() -> dict:
    try:
        return json.loads(config.SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_settings(data: dict) -> None:
    try:
        config.SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        config.SETTINGS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as e:
        log.warning("無法儲存設定：%s", e)


def open_in_finder(path: Path) -> None:
    try:
        if path.is_file():
            subprocess.run(["open", "-R", str(path)], check=False)
        else:
            subprocess.run(["open", str(path)], check=False)
    except OSError as e:
        log.warning("無法開啟 Finder：%s", e)


class MainWindow(ctk.CTk, *_DND_BASE):
    def __init__(self):
        super().__init__()
        self.dnd_enabled = False
        if TkinterDnD is not None:
            try:
                self.TkdndVersion = TkinterDnD._require(self)
                self.dnd_enabled = True
            except Exception as e:
                log.warning("拖曳功能無法使用：%s", e)

        self.title(config.APP_NAME)
        self.geometry("760x860")
        self.minsize(640, 700)

        self.events: queue.Queue = queue.Queue()
        self.stop_flag = threading.Event()
        self.worker: threading.Thread | None = None
        self.tools: converter.FFmpegTools | None = None
        settings = load_settings()

        self.output_dir = ctk.StringVar(value=settings.get("output_dir", str(config.DEFAULT_OUTPUT_DIR)))
        self.compat_mode = ctk.BooleanVar(value=settings.get("compat_mode", True))
        self.bitrate = ctk.StringVar(value=str(settings.get("bitrate", config.DEFAULT_BITRATE_KBPS)))
        self.overwrite = ctk.BooleanVar(value=settings.get("overwrite", False))

        self.tabs = ctk.CTkTabview(self)
        self.tabs.pack(fill="both", expand=True, padx=12, pady=(4, 12))
        self._build_convert_tab(self.tabs.add("轉換"))
        self._build_analyze_tab(self.tabs.add("分析 MP3"))

        self._on_compat_toggle()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(100, self._poll_events)
        self.after(200, self._check_environment)

    # ================================================================ 轉換分頁
    def _build_convert_tab(self, tab: ctk.CTkFrame) -> None:
        tab.grid_columnconfigure(0, weight=1)
        bold = ctk.CTkFont(size=14, weight="bold")
        row = 0

        self.env_banner = ctk.CTkFrame(tab, fg_color=("#fff8c5", "#3d2f00"))
        self.env_banner_label = ctk.CTkLabel(self.env_banner, text="", justify="left", anchor="w",
                                             text_color=COLOR_WARN)
        self.env_banner_label.pack(side="left", fill="x", expand=True, padx=10, pady=8)
        ctk.CTkButton(self.env_banner, text="重新檢查", width=80,
                      command=self._check_environment).pack(side="right", padx=10)
        self.env_banner_row = row
        row += 1

        ctk.CTkLabel(tab, text="YouTube URL", font=bold, anchor="w").grid(row=row, column=0, sticky="w")
        row += 1
        ctk.CTkLabel(tab, text="貼上 YouTube 網址（可一次貼多個，一行一個）", anchor="w",
                     text_color=COLOR_MUTED).grid(row=row, column=0, sticky="w")
        row += 1
        self.url_box = ctk.CTkTextbox(tab, height=96, wrap="none")
        self.url_box.grid(row=row, column=0, sticky="ew", pady=(2, 10))
        row += 1

        ctk.CTkLabel(tab, text="輸出資料夾", font=bold, anchor="w").grid(row=row, column=0, sticky="w")
        row += 1
        out_row = ctk.CTkFrame(tab, fg_color="transparent")
        out_row.grid(row=row, column=0, sticky="ew", pady=(2, 10))
        out_row.grid_columnconfigure(0, weight=1)
        ctk.CTkEntry(out_row, textvariable=self.output_dir).grid(row=0, column=0, sticky="ew")
        ctk.CTkButton(out_row, text="選擇…", width=70, command=self._choose_output).grid(row=0, column=1, padx=(6, 0))
        ctk.CTkButton(out_row, text="開啟", width=60, fg_color="gray40",
                      command=lambda: open_in_finder(Path(self.output_dir.get()).expanduser())
                      ).grid(row=0, column=2, padx=(6, 0))
        row += 1

        opts = ctk.CTkFrame(tab, fg_color="transparent")
        opts.grid(row=row, column=0, sticky="ew", pady=(0, 10))
        ctk.CTkSwitch(opts, text="Amazfit Balance 2 相容模式", variable=self.compat_mode,
                      command=self._on_compat_toggle).grid(row=0, column=0, sticky="w")
        self.compat_hint = ctk.CTkLabel(opts, text="", text_color=COLOR_MUTED, anchor="w", justify="left")
        self.compat_hint.grid(row=1, column=0, columnspan=3, sticky="w")
        br = ctk.CTkFrame(opts, fg_color="transparent")
        br.grid(row=0, column=1, padx=(24, 0))
        ctk.CTkLabel(br, text="位元率 (kbps)").pack(side="left", padx=(0, 6))
        self.bitrate_selector = ctk.CTkSegmentedButton(
            br, values=[str(b) for b in config.BITRATE_CHOICES_KBPS], variable=self.bitrate)
        self.bitrate_selector.pack(side="left")
        ctk.CTkCheckBox(opts, text="覆蓋已存在的檔案", variable=self.overwrite).grid(row=0, column=2, padx=(24, 0))
        row += 1

        btn_row = ctk.CTkFrame(tab, fg_color="transparent")
        btn_row.grid(row=row, column=0, sticky="ew", pady=(0, 10))
        btn_row.grid_columnconfigure(0, weight=1)
        self.convert_btn = ctk.CTkButton(btn_row, text="轉換成 Amazfit MP3", height=40,
                                         font=ctk.CTkFont(size=15, weight="bold"), command=self._start)
        self.convert_btn.grid(row=0, column=0, sticky="ew")
        self.stop_btn = ctk.CTkButton(btn_row, text="停止", width=80, height=40, fg_color="gray40",
                                      state="disabled", command=self._stop)
        self.stop_btn.grid(row=0, column=1, padx=(8, 0))
        row += 1

        prog = ctk.CTkFrame(tab)
        prog.grid(row=row, column=0, sticky="ew", pady=(0, 10))
        prog.grid_columnconfigure(1, weight=1)
        self.counter_label = ctk.CTkLabel(prog, text="", font=bold, width=70, anchor="w")
        self.counter_label.grid(row=0, column=0, padx=(12, 4), pady=(8, 2), sticky="w")
        self.current_label = ctk.CTkLabel(prog, text="等待開始", anchor="w")
        self.current_label.grid(row=0, column=1, sticky="ew", pady=(8, 2), padx=(0, 12))
        self.overall_bar = ctk.CTkProgressBar(prog)
        self.overall_bar.set(0)
        self.overall_bar.grid(row=1, column=0, columnspan=2, sticky="ew", padx=12, pady=(2, 8))

        steps_frame = ctk.CTkFrame(prog, fg_color="transparent")
        steps_frame.grid(row=2, column=0, columnspan=2, sticky="ew", padx=12)
        self.step_labels: dict[Step, ctk.CTkLabel] = {}
        for i, step in enumerate(STEPS):
            lbl = ctk.CTkLabel(steps_frame, text="", anchor="w")
            lbl.grid(row=i // 4, column=i % 4, sticky="w", padx=(0, 16))
            self.step_labels[step] = lbl
        self.step_detail = ctk.CTkLabel(prog, text="", anchor="w", text_color=COLOR_MUTED)
        self.step_detail.grid(row=3, column=0, columnspan=2, sticky="ew", padx=12)
        self.step_bar = ctk.CTkProgressBar(prog, height=6)
        self.step_bar.set(0)
        self.step_bar.grid(row=4, column=0, columnspan=2, sticky="ew", padx=12, pady=(2, 10))
        self._reset_steps()
        row += 1

        res_head = ctk.CTkFrame(tab, fg_color="transparent")
        res_head.grid(row=row, column=0, sticky="ew")
        res_head.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(res_head, text="結果", font=bold, anchor="w").grid(row=0, column=0, sticky="w")
        ctk.CTkButton(res_head, text="開啟 Log", width=80, fg_color="gray40",
                      command=lambda: open_in_finder(current_log_path())).grid(row=0, column=1)
        row += 1
        self.result_box = ctk.CTkTextbox(tab, height=200, state="disabled")
        self.result_box.grid(row=row, column=0, sticky="nsew", pady=(2, 0))
        tab.grid_rowconfigure(row, weight=1)

    def _on_compat_toggle(self) -> None:
        if self.compat_mode.get():
            p = config.BALANCE2_PROFILE
            self.bitrate.set(str(p.bitrate_kbps))
            self.bitrate_selector.configure(state="disabled")
            self.compat_hint.configure(
                text=f"MP3 · {p.sample_rate_hz / 1000:g} kHz · Stereo · {p.bitrate_kbps} kbps · "
                     f"ID3v2.3 · UTF-16 · 清除舊 Metadata · Unicode NFC · 移除 ID3v1 · 驗證 Metadata")
        else:
            self.bitrate_selector.configure(state="normal")
            self.compat_hint.configure(text="自訂位元率；Metadata 仍使用 ID3v2.3 + UTF-16 並進行驗證")

    def _choose_output(self) -> None:
        current = Path(self.output_dir.get()).expanduser()
        chosen = filedialog.askdirectory(initialdir=str(current if current.exists() else Path.home()),
                                         title="選擇輸出資料夾")
        if chosen:
            self.output_dir.set(chosen)

    def _check_environment(self) -> None:
        problems = []
        try:
            self.tools = converter.check_ffmpeg()
            log.info("FFmpeg: %s", self.tools.version)
        except ConverterError as e:
            self.tools = None
            log.error("FFmpeg 檢查失敗：%s", e.detail)
            problems.append(e.user_message)
        if not has_js_runtime():
            problems.append(config.JS_RUNTIME_HINT)
        if problems:
            self.env_banner_label.configure(text="\n\n".join(problems))
            self.env_banner.grid(row=self.env_banner_row, column=0, sticky="ew", pady=(0, 10))
        else:
            self.env_banner.grid_remove()
        if self.tools is None:
            self.convert_btn.configure(state="disabled")
            messagebox.showerror("找不到 FFmpeg", config.FFMPEG_INSTALL_HINT, parent=self)
        elif self.worker is None:
            self.convert_btn.configure(state="normal")

    # ---------------------------------------------------------------- 執行
    def _start(self) -> None:
        urls = split_urls(self.url_box.get("1.0", "end"))
        if not urls:
            messagebox.showwarning("沒有網址", "請先貼上 YouTube 網址。", parent=self)
            return
        if self.tools is None:
            messagebox.showerror("找不到 FFmpeg", config.FFMPEG_INSTALL_HINT, parent=self)
            return
        out_dir = Path(self.output_dir.get().strip() or config.DEFAULT_OUTPUT_DIR).expanduser()
        profile = config.make_profile(self.compat_mode.get(), int(self.bitrate.get()), self.overwrite.get())
        save_settings({"output_dir": str(out_dir), "compat_mode": self.compat_mode.get(),
                       "bitrate": int(self.bitrate.get()), "overwrite": self.overwrite.get()})

        self._set_results("")
        self._reset_steps()
        self.overall_bar.set(0)
        self.convert_btn.configure(state="disabled", text="轉換中…")
        self.stop_btn.configure(state="normal")
        self.stop_flag.clear()
        self.worker = threading.Thread(
            target=run_batch, daemon=True,
            args=(urls, profile, out_dir, self.events.put, self.stop_flag, self.tools))
        self.worker.start()

    def _stop(self) -> None:
        self.stop_flag.set()
        self.stop_btn.configure(state="disabled")
        self.current_label.configure(text="目前這首完成後停止…")

    def _poll_events(self) -> None:
        try:
            while True:
                self._handle_event(self.events.get_nowait())
        except queue.Empty:
            pass
        self.after(100, self._poll_events)

    def _handle_event(self, ev: BatchEvent) -> None:
        if ev.kind == "job_start":
            self._reset_steps()
            self.counter_label.configure(text=f"{ev.index} / {ev.total}")
            self.current_label.configure(text=ev.url)
            self.overall_bar.set((ev.index - 1) / ev.total)
        elif ev.kind == "progress":
            self._update_step(ev.progress)
        elif ev.kind == "job_done":
            r = ev.result
            self.overall_bar.set(ev.index / ev.total)
            if r.ok:
                line = f"✓ [{ev.index}/{ev.total}] {r.metadata.display_name}\n    → {short_path(r.output_path)}\n"
                for note in r.notes:
                    line += f"    ・{note}\n"
            else:
                where = f"（{r.failed_step.value}）" if r.failed_step else ""
                line = f"✗ [{ev.index}/{ev.total}] {r.source}\n    {r.error}{where}\n"
            self._append_result(line)
        elif ev.kind == "finished":
            self._on_finished(ev)

    def _on_finished(self, ev: BatchEvent) -> None:
        results = ev.results
        ok = [r for r in results if r.ok]
        self.worker = None
        self.convert_btn.configure(state="normal" if self.tools else "disabled", text="轉換成 Amazfit MP3")
        self.stop_btn.configure(state="disabled")
        stopped = len(results) < ev.total
        if len(results) == 1 and ok and not stopped:
            r = ok[0]
            self._set_results(f"完成！\n\n歌曲：\n{r.metadata.display_name}\n\n輸出：\n{short_path(r.output_path)}\n"
                              + "".join(f"\n・{n}" for n in r.notes))
        else:
            summary = f"\n完成 {len(ok)} / {ev.total} 首"
            if len(results) - len(ok):
                summary += f"，失敗 {len(results) - len(ok)} 首（詳細請看 Log）"
            if stopped:
                summary += f"，已停止（剩下 {ev.total - len(results)} 首未處理）"
            self._append_result(summary + "\n")
        self.current_label.configure(text="全部完成" if not stopped else "已停止")
        self.counter_label.configure(text=f"{len(results)} / {ev.total}")
        if ok and len(ok) == len(results) and not stopped:
            self.url_box.delete("1.0", "end")
        if any(r.error == "Metadata 驗證失敗，請查看 Log" for r in results):
            messagebox.showwarning("驗證失敗", f"Metadata 驗證失敗，請查看 Log\n\n{short_path(current_log_path())}",
                                   parent=self)

    def _reset_steps(self) -> None:
        for step, lbl in self.step_labels.items():
            lbl.configure(text=f"{STEP_ICONS['pending']} {step.value}", text_color=STEP_COLORS["pending"])
        self.step_detail.configure(text="")
        self.step_bar.set(0)

    def _update_step(self, e: ProgressEvent) -> None:
        lbl = self.step_labels[e.step]
        lbl.configure(text=f"{STEP_ICONS[e.status]} {e.step.value}", text_color=STEP_COLORS[e.status])
        if e.status == "progress":
            self.step_bar.set(e.fraction)
            pct = f"{e.fraction * 100:.0f}%"
            self.step_detail.configure(text=f"{e.step.value} {pct}  {e.message}".rstrip())
        elif e.status == "start":
            self.step_bar.set(0)
            self.step_detail.configure(text=f"{e.step.value}…")
        elif e.status == "done":
            self.step_bar.set(1)
            if e.step == Step.FETCH_INFO and e.message:
                self.current_label.configure(text=e.message)
        elif e.status == "fail":
            self.step_detail.configure(text=e.message, text_color=COLOR_FAIL)
            return
        self.step_detail.configure(text_color=COLOR_MUTED)

    def _set_results(self, text: str) -> None:
        self.result_box.configure(state="normal")
        self.result_box.delete("1.0", "end")
        self.result_box.insert("end", text)
        self.result_box.configure(state="disabled")

    def _append_result(self, text: str) -> None:
        self.result_box.configure(state="normal")
        self.result_box.insert("end", text)
        self.result_box.see("end")
        self.result_box.configure(state="disabled")

    # ============================================================== 分析分頁
    def _build_analyze_tab(self, tab: ctk.CTkFrame) -> None:
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(2, weight=1)
        self.drop_zone = ctk.CTkFrame(tab, height=110, border_width=2, border_color=COLOR_MUTED)
        self.drop_zone.grid(row=0, column=0, sticky="ew", pady=(4, 8))
        self.drop_zone.grid_propagate(False)
        self.drop_zone.grid_columnconfigure(0, weight=1)
        self.drop_zone.grid_rowconfigure(0, weight=1)
        hint = "把 MP3 拖曳到這裡" if self.dnd_enabled else "選擇一個 MP3 進行分析"
        self.drop_label = ctk.CTkLabel(self.drop_zone, text=f"{hint}\n檢查 ID3 版本、文字編碼、Unicode 與 Balance 2 相容性",
                                       font=ctk.CTkFont(size=14))
        self.drop_label.grid(row=0, column=0)
        if self.dnd_enabled:
            for widget in (self.drop_zone, self.drop_label):
                widget.drop_target_register(DND_FILES)
                widget.dnd_bind("<<Drop>>", self._on_drop)

        bar = ctk.CTkFrame(tab, fg_color="transparent")
        bar.grid(row=1, column=0, sticky="ew", pady=(0, 6))
        bar.grid_columnconfigure(1, weight=1)
        ctk.CTkButton(bar, text="選擇 MP3…", width=110, command=self._choose_mp3).grid(row=0, column=0)
        self.verdict_label = ctk.CTkLabel(bar, text="", font=ctk.CTkFont(size=18, weight="bold"), anchor="e")
        self.verdict_label.grid(row=0, column=1, sticky="e")

        self.report_box = ctk.CTkTextbox(tab, font=ctk.CTkFont(family="Menlo", size=13), state="disabled")
        self.report_box.grid(row=2, column=0, sticky="nsew")

    def _choose_mp3(self) -> None:
        path = filedialog.askopenfilename(title="選擇 MP3", parent=self,
                                          initialdir=str(Path(self.output_dir.get()).expanduser()),
                                          filetypes=[("MP3", "*.mp3 *.MP3"), ("所有檔案", "*")])
        if path:
            self._analyze(Path(path))

    def _on_drop(self, event) -> None:
        paths = self.tk.splitlist(event.data)
        if paths:
            self._analyze(Path(paths[0]))

    def _analyze(self, path: Path) -> None:
        log.info("診斷：%s", path.name)
        try:
            report = analyze_mp3(path, self.tools)
            text = report.format()
            verdict = report.verdict
        except Exception as e:  # 診斷模式絕不能讓程式崩潰
            log.exception("診斷失敗")
            text, verdict = f"無法分析這個檔案：{e}", "FAIL"
        color = {"PASS": COLOR_OK, "WARN": COLOR_WARN, "FAIL": COLOR_FAIL}[verdict]
        self.verdict_label.configure(text=f"Balance 2 相容性：{verdict}", text_color=color)
        self.drop_zone.configure(border_color=color)
        self.report_box.configure(state="normal")
        self.report_box.delete("1.0", "end")
        self.report_box.insert("end", text)
        self.report_box.configure(state="disabled")

    # ================================================================== 其他
    def report_callback_exception(self, exc, val, tb) -> None:  # Tk callback 的未處理例外
        log.error("GUI 錯誤", exc_info=(exc, val, tb))
        messagebox.showerror("發生錯誤", f"{val}\n\n詳細內容已寫入 Log。", parent=self)

    def _on_close(self) -> None:
        if self.worker and self.worker.is_alive():
            if not messagebox.askyesno("仍在轉換", "目前還在轉換中，確定要離開嗎？", parent=self):
                return
        self.destroy()


def run() -> None:
    ctk.set_appearance_mode("system")
    ctk.set_default_color_theme("blue")
    app = MainWindow()
    app.mainloop()
