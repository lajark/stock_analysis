"""Minimal native desktop interface for stock_analysis."""

import os
import queue
import re
import threading
import tkinter as tk
from collections.abc import Sequence
from pathlib import Path
from tkinter import messagebox, ttk

from loguru import logger

from src.app.service import (
    MODES,
    STAGES,
    AnalysisCancelledError,
    AnalysisRequest,
    AnalysisResult,
    BatchItem,
    analyze_batch,
    analyze_stock,
)
from src.config import get_config, get_user_settings, save_user_settings
from src.runtime_paths import user_data_root

# Short Chinese labels keyed by the canonical service stage names.
STAGE_LABELS = {
    "validate_request": "校验输入",
    "acquire_data": "获取数据",
    "validate_evidence": "数据质量校验",
    "build_evidence": "本地指标计算",
    "generate_report": "生成分析报告",
    "render_chart": "生成 K 线图",
    "finish": "收尾",
}


def _split_tickers(text: str) -> list[str]:
    """Split a GUI ticker input into individual codes.

    Accepts commas, semicolons, whitespace or newlines as separators; at least
    one code triggers the batch path in the UI (single code keeps the legacy
    single-stock behavior).
    """
    return [part for part in re.split(r"[,;\s]+", text.strip()) if part]


def _format_batch_summary(items: Sequence[BatchItem], *, cancelled: bool) -> str:
    """Plain-text per-item summary shown in the result preview after a batch.

    Successful items list outcome + report path; cancelled items (cancelled
    mode only) are marked with ``○``; everything else carries the isolated
    error message. Never persisted anywhere (same constraint as streaming
    preview).
    """
    lines = ["批量分析结果"]
    for index, item in enumerate(items, start=1):
        tag = f"[{index}/{len(items)}]"
        if item.result is not None:
            result = item.result
            lines.append(
                f"✓ {tag} {result.ticker} {result.stock_name} — "
                f"{result.elapsed_seconds:.1f}s"
            )
            lines.append(f"    {result.output_path}")
        elif cancelled and item.error is not None and "取消" in item.error:
            lines.append(f"○ {tag} {item.request.ticker} — 已取消")
        elif item.error is not None:
            lines.append(f"✗ {tag} {item.request.ticker} — {item.error}")
        else:
            lines.append(f"✗ {tag} {item.request.ticker} — 未知错误")
    return "\n".join(lines)


class StockAnalysisApp:
    """Single-window Tkinter application."""

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("股票分析工具")
        self.root.geometry("820x680")
        self.root.minsize(720, 580)
        self.current_result: AnalysisResult | None = None
        self.cancel_event: threading.Event | None = None
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self._token_preview_active = False

        self.notebook = ttk.Notebook(root)
        self.analysis_page = ttk.Frame(self.notebook, padding=18)
        self.settings_page = ttk.Frame(self.notebook, padding=18)
        self.notebook.add(self.analysis_page, text="股票分析")
        self.notebook.add(self.settings_page, text="API 设置")
        self.notebook.pack(fill=tk.BOTH, expand=True)

        self._build_analysis_page()
        self._build_settings_page()
        self._load_settings()
        self.root.after(100, self._process_events)

        if not get_config().tushare_token.strip():
            self.notebook.select(self.settings_page)
            self.status_var.set("首次使用：请先保存 API 设置")

    def _build_analysis_page(self) -> None:
        page = self.analysis_page
        page.columnconfigure(1, weight=1)
        page.rowconfigure(8, weight=1)

        ttk.Label(page, text="股票代码（可多只，逗号/空格分隔）").grid(
            row=0, column=0, sticky=tk.W, pady=(0, 12)
        )
        self.ticker_var = tk.StringVar()
        ticker_entry = ttk.Entry(page, textvariable=self.ticker_var, width=26)
        ticker_entry.grid(row=0, column=1, sticky=tk.EW, pady=(0, 12))
        ticker_entry.insert(0, "600519")

        ttk.Label(page, text="分析模式").grid(row=1, column=0, sticky=tk.W, pady=(0, 12))
        self.mode_labels = {info["desc"]: key for key, info in MODES.items()}
        self.mode_var = tk.StringVar(value=MODES["quick"]["desc"])
        mode_box = ttk.Combobox(
            page,
            textvariable=self.mode_var,
            values=list(self.mode_labels),
            state="readonly",
        )
        mode_box.grid(row=1, column=1, sticky=tk.EW, pady=(0, 12))

        self.use_llm_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            page,
            text="使用 AI 生成中文报告（会消耗 Token）",
            variable=self.use_llm_var,
        ).grid(row=2, column=1, sticky=tk.W, pady=(0, 8))

        self.chart_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            page,
            text="同时生成 K 线图",
            variable=self.chart_var,
        ).grid(row=3, column=1, sticky=tk.W, pady=(0, 14))

        actions = ttk.Frame(page)
        actions.grid(row=4, column=0, columnspan=2, sticky=tk.EW)
        self.analyze_button = ttk.Button(actions, text="开始分析", command=self._start_analysis)
        self.analyze_button.pack(side=tk.LEFT)
        self.cancel_button = ttk.Button(
            actions,
            text="取消分析",
            command=self._cancel_analysis,
            state=tk.DISABLED,
        )
        self.cancel_button.pack(side=tk.LEFT, padx=(10, 0))
        self.open_button = ttk.Button(
            actions,
            text="打开结果文件",
            command=self._open_result,
            state=tk.DISABLED,
        )
        self.open_button.pack(side=tk.LEFT, padx=(10, 0))
        ttk.Button(actions, text="打开输出目录", command=self._open_output_dir).pack(
            side=tk.LEFT, padx=(10, 0)
        )

        self.progress = ttk.Progressbar(page, mode="indeterminate")
        self.progress.grid(row=5, column=0, columnspan=2, sticky=tk.EW, pady=(16, 6))
        self.stage_var = tk.StringVar(value="")
        ttk.Label(page, textvariable=self.stage_var).grid(
            row=6, column=0, columnspan=2, sticky=tk.W, pady=(0, 4)
        )
        self.status_var = tk.StringVar(value="准备就绪")
        ttk.Label(page, textvariable=self.status_var).grid(
            row=7, column=0, columnspan=2, sticky=tk.W, pady=(0, 10)
        )

        preview_frame = ttk.LabelFrame(page, text="结果预览", padding=8)
        preview_frame.grid(row=8, column=0, columnspan=2, sticky=tk.NSEW)
        preview_frame.columnconfigure(0, weight=1)
        preview_frame.rowconfigure(0, weight=1)
        self.preview = tk.Text(preview_frame, wrap=tk.WORD, state=tk.DISABLED)
        preview_scroll = ttk.Scrollbar(
            preview_frame,
            orient=tk.VERTICAL,
            command=self.preview.yview,
        )
        self.preview.configure(yscrollcommand=preview_scroll.set)
        self.preview.grid(row=0, column=0, sticky=tk.NSEW)
        preview_scroll.grid(row=0, column=1, sticky=tk.NS)

    def _build_settings_page(self) -> None:
        page = self.settings_page
        page.columnconfigure(1, weight=1)

        self.tushare_token_var = tk.StringVar()
        self.llm_key_var = tk.StringVar()
        self.base_url_var = tk.StringVar()
        self.model_var = tk.StringVar()
        self.deep_model_var = tk.StringVar()
        self.secret_entries: list[ttk.Entry] = []

        fields = [
            ("Tushare Token *", self.tushare_token_var, True),
            ("LLM API Key", self.llm_key_var, True),
            ("LLM 接口地址", self.base_url_var, False),
            ("普通模型", self.model_var, False),
            ("深度分析模型", self.deep_model_var, False),
        ]
        for row, (label, variable, secret) in enumerate(fields):
            ttk.Label(page, text=label).grid(row=row, column=0, sticky=tk.W, pady=(0, 12))
            if label == "LLM 接口地址":
                entry: ttk.Entry = ttk.Combobox(
                    page,
                    textvariable=variable,
                    values=[
                        "https://dashscope.aliyuncs.com/compatible-mode/v1",
                        "https://api.deepseek.com/v1",
                        "https://api.openai.com/v1",
                    ],
                )
            else:
                entry = ttk.Entry(page, textvariable=variable, show="•" if secret else "")
            entry.grid(row=row, column=1, sticky=tk.EW, pady=(0, 12))
            if secret:
                self.secret_entries.append(entry)

        self.show_secrets_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            page,
            text="显示密钥",
            variable=self.show_secrets_var,
            command=self._toggle_secrets,
        ).grid(row=5, column=1, sticky=tk.W, pady=(0, 14))

        ttk.Button(page, text="保存设置", command=self._save_settings).grid(
            row=6, column=1, sticky=tk.W
        )
        ttk.Label(
            page,
            text=(
                "设置仅保存在当前 Windows 用户的本地目录。LLM 配置可选；"
                "不使用 AI 报告时无需填写 LLM API Key。"
            ),
            wraplength=620,
        ).grid(row=7, column=0, columnspan=2, sticky=tk.W, pady=(18, 0))

    def _load_settings(self) -> None:
        values = get_user_settings()
        self.tushare_token_var.set(values["TUSHARE_TOKEN"])
        self.llm_key_var.set(values["LLM_API_KEY"])
        self.base_url_var.set(values["LLM_BASE_URL"])
        self.model_var.set(values["LLM_MODEL"])
        self.deep_model_var.set(values["LLM_MODEL_DEEP"])

    def _toggle_secrets(self) -> None:
        mask = "" if self.show_secrets_var.get() else "•"
        for entry in self.secret_entries:
            entry.configure(show=mask)

    def _save_settings(self) -> None:
        if not self.tushare_token_var.get().strip():
            messagebox.showerror("缺少配置", "Tushare Token 不能为空。")
            return
        if not self.base_url_var.get().strip() or not self.model_var.get().strip():
            messagebox.showerror("缺少配置", "LLM 接口地址和普通模型不能为空。")
            return
        try:
            save_user_settings(
                tushare_token=self.tushare_token_var.get(),
                llm_api_key=self.llm_key_var.get(),
                llm_base_url=self.base_url_var.get(),
                llm_model=self.model_var.get(),
                llm_model_deep=self.deep_model_var.get(),
            )
        except Exception as exc:
            messagebox.showerror("保存失败", str(exc))
            return
        self.status_var.set("API 设置已保存")
        messagebox.showinfo("保存成功", "API 设置已保存，仅对当前 Windows 用户生效。")

    def _start_analysis(self) -> None:
        mode = self.mode_labels[self.mode_var.get()]
        codes = _split_tickers(self.ticker_var.get())

        self.analyze_button.configure(state=tk.DISABLED)
        self.open_button.configure(state=tk.DISABLED)
        self.cancel_button.configure(state=tk.NORMAL)
        self.cancel_event = threading.Event()
        self.current_result = None
        self._token_preview_active = False
        self.stage_var.set("")
        self.progress.start(12)
        self._set_preview("")

        if len(codes) > 1:
            requests = [
                AnalysisRequest(
                    ticker=code,
                    mode=mode,
                    use_llm=self.use_llm_var.get(),
                    chart=self.chart_var.get(),
                )
                for code in codes
            ]
            self.status_var.set(f"正在准备批量分析…（共 {len(codes)} 只）")
            threading.Thread(
                target=self._batch_worker, args=(requests,), daemon=True
            ).start()
            return

        request = AnalysisRequest(
            ticker=codes[0] if codes else self.ticker_var.get(),
            mode=mode,
            use_llm=self.use_llm_var.get(),
            chart=self.chart_var.get(),
        )
        self.status_var.set("正在准备分析…")
        threading.Thread(
            target=self._analysis_worker, args=(request,), daemon=True
        ).start()

    def _cancel_analysis(self) -> None:
        """Request cancellation; the worker honours it at the next checkpoint."""
        if self.cancel_event is not None:
            self.cancel_event.set()
            self.cancel_button.configure(state=tk.DISABLED)
            self.status_var.set("正在取消…（将在安全检查点生效）")

    def _analysis_worker(self, request: AnalysisRequest) -> None:
        try:
            result = analyze_stock(
                request,
                progress=lambda message: self.events.put(("progress", message)),
                cancel_event=self.cancel_event,
                stage_progress=lambda stage, status, message: self.events.put(
                    ("stage", (stage, status, message))
                ),
                token_callback=lambda text: self.events.put(("token", text)),
            )
        except AnalysisCancelledError:
            self.events.put(("cancelled", None))
            return
        except Exception as exc:
            self.events.put(("failed", str(exc)))
            return
        self.events.put(("finished", result))

    def _batch_worker(self, requests: list[AnalysisRequest]) -> None:
        """Batch analysis worker: failure-isolated items, one shared cancel event.

        Batch mode deliberately disables the LLM streaming preview (scope
        decision): each item uses the non-streaming report path, and per-item
        stage events carry a ``[k/N]`` prefix via ``item_prefix``.
        """
        total = len(requests)
        self.events.put(("batch_started", total))
        try:
            items = analyze_batch(
                requests,
                max_workers=get_config().batch.max_workers,
                cancel_event=self.cancel_event,
                stage_progress=lambda stage, status, message: self.events.put(
                    ("stage", (stage, status, message))
                ),
                item_prefix=lambda index, count: f"[{index + 1}/{count}]",
            )
        except Exception as exc:
            self.events.put(("failed", str(exc)))
            return
        if self.cancel_event is not None and self.cancel_event.is_set():
            self.events.put(("batch_cancelled", items))
        else:
            self.events.put(("batch_done", items))

    def _process_events(self) -> None:
        try:
            while True:
                event, payload = self.events.get_nowait()
                if event == "progress":
                    self.status_var.set(str(payload))
                elif event == "stage" and isinstance(payload, tuple) and len(payload) == 3:
                    self._update_stage(str(payload[0]), str(payload[1]), str(payload[2]))
                elif event == "token":
                    self._append_token_preview(str(payload))
                elif event == "batch_started":
                    self.status_var.set(f"批量任务已提交，共 {payload} 只")
                elif event == "batch_done" and isinstance(payload, list):
                    self._batch_finished(payload)
                elif event == "batch_cancelled" and isinstance(payload, list):
                    self._batch_cancelled(payload)
                elif event == "failed":
                    self._analysis_failed(str(payload))
                elif event == "cancelled":
                    self._analysis_cancelled()
                elif event == "finished" and isinstance(payload, AnalysisResult):
                    self._analysis_finished(payload)
        except queue.Empty:
            pass
        self.root.after(100, self._process_events)

    def _update_stage(self, stage: str, status: str, message: str) -> None:
        """Deterministic stage progress line: '阶段 k/N：<名称>（进行中）'."""
        position = STAGES.index(stage) + 1 if stage in STAGES else len(STAGES)
        label = STAGE_LABELS.get(stage, stage)
        arrow = "✓" if status == "done" else "○"
        self.stage_var.set(f"{arrow} 阶段 {position}/{len(STAGES)}：{label}")
        self.status_var.set(str(message))

    def _analysis_finished(self, result: AnalysisResult) -> None:
        self.progress.stop()
        self.analyze_button.configure(state=tk.NORMAL)
        self.open_button.configure(state=tk.NORMAL)
        self.cancel_button.configure(state=tk.DISABLED)
        self.stage_var.set("")
        self.current_result = result
        self.status_var.set(
            f"完成：{result.stock_name} ({result.ticker})，耗时 {result.elapsed_seconds:.1f} 秒"
        )
        try:
            content = result.output_path.read_text(encoding="utf-8")
        except OSError:
            content = f"结果已保存至：\n{result.output_path}"
        self._set_preview(content)

    def _analysis_cancelled(self) -> None:
        """A cancelled run leaves no partial report behind (see service checkpoints)."""
        self.progress.stop()
        self.analyze_button.configure(state=tk.NORMAL)
        self.open_button.configure(state=tk.NORMAL)
        self.cancel_button.configure(state=tk.DISABLED)
        self.stage_var.set("")
        self.status_var.set("分析已取消：未生成报告")

    def _batch_finished(self, items: list[BatchItem]) -> None:
        """Batch completed normally: per-item summary in preview."""
        self.progress.stop()
        self.analyze_button.configure(state=tk.NORMAL)
        self.cancel_button.configure(state=tk.DISABLED)
        self.open_button.configure(state=tk.DISABLED)  # Batch has no single result file.
        self.stage_var.set("")
        self._set_preview(_format_batch_summary(items, cancelled=False))
        ok = sum(1 for item in items if item.result is not None)
        self.status_var.set(f"批量完成：{ok}/{len(items)} 只成功")

    def _batch_cancelled(self, items: list[BatchItem]) -> None:
        """Batch cancelled mid-run: completed items stay listed, rest marked."""
        self.progress.stop()
        self.analyze_button.configure(state=tk.NORMAL)
        self.cancel_button.configure(state=tk.DISABLED)
        self.open_button.configure(state=tk.DISABLED)
        self.stage_var.set("")
        self._set_preview(_format_batch_summary(items, cancelled=True))
        done = sum(1 for item in items if item.result is not None)
        self.status_var.set(f"批量已取消：已分析 {done}/{len(items)} 只")

    def _analysis_failed(self, message: str) -> None:
        self.progress.stop()
        self.analyze_button.configure(state=tk.NORMAL)
        self.cancel_button.configure(state=tk.DISABLED)
        self.status_var.set("分析未完成")
        if "API 设置" in message or "Token" in message or "API Key" in message:
            self.notebook.select(self.settings_page)
        messagebox.showerror("分析失败", message)

    def _set_preview(self, content: str) -> None:
        self.preview.configure(state=tk.NORMAL)
        self.preview.delete("1.0", tk.END)
        self.preview.insert("1.0", content)
        self.preview.configure(state=tk.DISABLED)

    def _append_token_preview(self, text: str) -> None:
        """Append one LLM stream delta to the in-memory preview (never persisted)."""
        if not self._token_preview_active:
            self._token_preview_active = True
            position = STAGES.index("generate_report") + 1
            label = STAGE_LABELS["generate_report"]
            self.stage_var.set(f"○ 阶段 {position}/{len(STAGES)}：{label}（流式输出中）…")
        self.preview.configure(state=tk.NORMAL)
        self.preview.insert(tk.END, text)
        self.preview.see(tk.END)
        self.preview.configure(state=tk.DISABLED)

    def _open_result(self) -> None:
        if self.current_result:
            self._open_path(self.current_result.output_path)

    def _open_output_dir(self) -> None:
        output_dir = get_config().output_dir
        output_dir.mkdir(parents=True, exist_ok=True)
        self._open_path(output_dir)

    @staticmethod
    def _open_path(path: Path) -> None:
        try:
            os.startfile(str(path))  # type: ignore[attr-defined]
        except OSError as exc:
            messagebox.showerror("无法打开", str(exc))


def _configure_logging() -> None:
    log_dir = user_data_root() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    logger.remove()
    logger.add(
        log_dir / "app_{time:YYYY-MM-DD}.log",
        level="INFO",
        rotation="10 MB",
        retention="7 days",
        encoding="utf-8",
    )


def main() -> None:
    """Launch the desktop application."""
    _configure_logging()
    root = tk.Tk()
    StockAnalysisApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
