"""Minimal native desktop interface for stock_analysis.

Modern themed GUI built on ttkbootstrap with Markdown preview (tkinterweb),
a help tab (embedded usage guide + update check), and a backtest /
parameter-optimization tab.  All analysis, backtest, and optimisation functions
are re-used from the CLI / service layer — no duplicated business logic.
"""

from __future__ import annotations

import os
import queue
import re
import threading
import tkinter as tk
from collections.abc import Sequence
from pathlib import Path
from tkinter import messagebox, ttk

import ttkbootstrap as tb
from loguru import logger
from markdown import markdown
from tkinterweb import HtmlFrame

from src.analysis.backtest import (
    BacktestSpec,
    CostModel,
    optimize_ma_cross,
    run_backtest,
)
from src.app.backtest_records import persist_backtest_run
from src.app.help_text import USAGE_GUIDE_MD
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
from src.app.update_check import check_for_updates
from src.config import get_config, get_user_settings, save_user_settings
from src.data.gateway import DataGateway
from src.runtime_paths import user_data_root

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

STAGE_LABELS = {
    "validate_request": "校验输入",
    "acquire_data": "获取数据",
    "validate_evidence": "数据质量校验",
    "build_evidence": "本地指标计算",
    "generate_report": "生成分析报告",
    "render_chart": "生成 K 线图",
    "finish": "收尾",
}

DEFAULT_THEME = "cosmo"
BASE_FONT = "Microsoft YaHei UI"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _split_tickers(text: str) -> list[str]:
    """Split a GUI ticker input into individual codes."""
    return [part for part in re.split(r"[,;\s]+", text.strip()) if part]


def _format_batch_summary(items: Sequence[BatchItem], *, cancelled: bool) -> str:
    """Plain-text per-item summary shown in the result preview after a batch."""
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


def _local_version() -> str:
    """Read the project version from pyproject.toml."""
    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    try:
        text = pyproject.read_text(encoding="utf-8")
        match = re.search(r'(?m)^version\s*=\s*"([^"]+)"', text)
        return match.group(1) if match else "unknown"
    except OSError:
        return "unknown"


def _render_markdown_html(text: str, *, font_size: int = 14) -> str:
    """Convert Markdown text to styled HTML for tkinterweb."""
    body = markdown(
        text,
        extensions=["extra", "codehilite", "tables"],
    )
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
body {{ font-family: 'Segoe UI', 'Microsoft YaHei', sans-serif; font-size: {font_size}px;
       line-height: 1.65; color: #e0e0e0; background: #24262b; padding: 18px; }}
h1, h2, h3 {{ color: #f5f5f5; }}
h1 {{ border-bottom: 1px solid #454b57; padding-bottom: 8px; }}
h2 {{ border-bottom: 1px solid #33373f; padding-bottom: 4px; }}
a {{ color: #5dade2; }}
code {{ background: #343943; padding: 2px 6px; border-radius: 3px;
       font-size: {max(font_size - 1, 11)}px; }}
pre {{ background: #1a1c22; padding: 12px; border-radius: 6px; overflow-x: auto; }}
table {{ border-collapse: collapse; width: 100%; margin: 12px 0; }}
th, td {{ border: 1px solid #454b57; padding: 6px 10px; text-align: left; }}
th {{ background: #343943; }}
blockquote {{ border-left: 4px solid #5dade2; margin: 0; padding: 4px 16px;
             color: #aab; }}
hr {{ border: none; border-top: 1px solid #454b57; }}
</style></head><body>{body}</body></html>"""


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------


class StockAnalysisApp:
    """Single-window application with ttkbootstrap theming."""

    def __init__(self, root: tb.Window):
        self.root = root
        self.root.title("股票分析工具")
        self.root.geometry("1060x820")
        self.root.minsize(920, 700)

        self.current_result: AnalysisResult | None = None
        self.cancel_event: threading.Event | None = None
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self._token_preview_active = False

        self._build_header()

        # --- Notebook (tabs) ---
        self.notebook = ttk.Notebook(root)
        self.analysis_page = ttk.Frame(self.notebook, padding=14)
        self.backtest_page = ttk.Frame(self.notebook, padding=14)
        self.settings_page = ttk.Frame(self.notebook, padding=14)
        self.help_page = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(self.analysis_page, text=" 股票分析 ")
        self.notebook.add(self.backtest_page, text=" 回测 / 优化 ")
        self.notebook.add(self.settings_page, text=" API 设置 ")
        self.notebook.add(self.help_page, text=" 帮助 ")
        self.notebook.pack(fill=tk.BOTH, expand=True)

        self._build_analysis_page()
        self._build_backtest_page()
        self._build_settings_page()
        self._build_help_page()
        self._load_settings()
        self.root.after(100, self._process_events)

        # First-run prompt
        if not get_config().tushare_token.strip():
            self.notebook.select(self.settings_page)
            self.status_var.set("首次使用：请先保存 API 设置")

    # =======================================================================
    # Header
    # =======================================================================

    def _build_header(self) -> None:
        header = tb.Frame(self.root, bootstyle="primary", padding=(18, 10))
        header.pack(fill=tk.X, side=tk.TOP)

        tb.Label(
            header,
            text="股票分析工具",
            font=(BASE_FONT, 16, "bold"),
            bootstyle="inverse-primary",
        ).pack(side=tk.LEFT)
        tb.Label(
            header,
            text="本地分析 · 手动触发 · AI 报告可选",
            font=(BASE_FONT, 10),
            bootstyle="inverse-secondary",
        ).pack(side=tk.LEFT, padx=(14, 0))
        version = _local_version()
        tb.Label(
            header,
            text=f"v{version}",
            font=(BASE_FONT, 10),
            bootstyle="inverse-secondary",
        ).pack(side=tk.RIGHT)

    # =======================================================================
    # Analysis page
    # =======================================================================

    def _build_analysis_page(self) -> None:
        page = self.analysis_page
        page.columnconfigure(0, weight=1)
        page.rowconfigure(5, weight=1)

        # --- Card: analysis parameters ---
        params = tb.LabelFrame(page, text="分析参数", padding=12)
        params.grid(row=0, column=0, sticky=tk.EW, pady=(0, 10))
        params.columnconfigure(1, weight=1)

        tb.Label(params, text="股票代码（可多只，逗号/空格分隔）").grid(
            row=0, column=0, sticky=tk.W, pady=(0, 8)
        )
        self.ticker_var = tk.StringVar()
        ticker_entry = tb.Entry(params, textvariable=self.ticker_var, width=32)
        ticker_entry.grid(row=0, column=1, sticky=tk.EW, pady=(0, 8))
        ticker_entry.insert(0, "600519")

        tb.Label(params, text="分析模式").grid(
            row=1, column=0, sticky=tk.W, pady=(0, 8)
        )
        self.mode_labels = {info["desc"]: key for key, info in MODES.items()}
        self.mode_var = tk.StringVar(value=MODES["quick"]["desc"])
        mode_box = tb.Combobox(
            params,
            textvariable=self.mode_var,
            values=list(self.mode_labels),
            state="readonly",
        )
        mode_box.grid(row=1, column=1, sticky=tk.EW, pady=(0, 8))

        opts = tb.Frame(params)
        opts.grid(row=2, column=0, columnspan=2, sticky=tk.W)
        self.use_llm_var = tk.BooleanVar(value=True)
        tb.Checkbutton(
            opts,
            text="使用 AI 生成中文报告（会消耗 Token）",
            variable=self.use_llm_var,
            bootstyle="info",
        ).pack(side=tk.LEFT, padx=(0, 18))
        self.chart_var = tk.BooleanVar(value=False)
        tb.Checkbutton(
            opts,
            text="同时生成 K 线图",
            variable=self.chart_var,
            bootstyle="info",
        ).pack(side=tk.LEFT)

        # --- Action buttons ---
        actions = tb.Frame(page)
        actions.grid(row=1, column=0, sticky=tk.EW, pady=(0, 6))
        self.analyze_button = tb.Button(
            actions, text="开始分析", command=self._start_analysis, bootstyle="primary"
        )
        self.analyze_button.pack(side=tk.LEFT)
        self.cancel_button = tb.Button(
            actions,
            text="取消分析",
            command=self._cancel_analysis,
            state=tk.DISABLED,
            bootstyle="secondary",
        )
        self.cancel_button.pack(side=tk.LEFT, padx=(8, 0))
        self.open_button = tb.Button(
            actions,
            text="打开结果文件",
            command=self._open_result,
            state=tk.DISABLED,
            bootstyle="success-outline",
        )
        self.open_button.pack(side=tk.LEFT, padx=(8, 0))
        tb.Button(actions, text="打开输出目录", command=self._open_output_dir).pack(
            side=tk.LEFT, padx=(8, 0)
        )

        # --- Progress / status ---
        self.progress = tb.Progressbar(page, mode="indeterminate", bootstyle="info")
        self.progress.grid(row=2, column=0, sticky=tk.EW, pady=(6, 2))

        self.stage_var = tk.StringVar(value="")
        tb.Label(page, textvariable=self.stage_var).grid(
            row=3, column=0, sticky=tk.W, pady=(0, 2)
        )

        self.status_var = tk.StringVar(value="准备就绪")
        tb.Label(page, textvariable=self.status_var, bootstyle="secondary").grid(
            row=4, column=0, sticky=tk.W, pady=(0, 8)
        )

        # --- Card: result preview ---
        preview_frame = tb.LabelFrame(page, text="结果预览", padding=8)
        preview_frame.grid(row=5, column=0, sticky=tk.NSEW)
        preview_frame.columnconfigure(0, weight=1)
        preview_frame.rowconfigure(1, weight=1)

        toolbar = tb.Frame(preview_frame)
        toolbar.grid(row=0, column=0, sticky=tk.EW, pady=(0, 6))
        self._preview_toggle = tb.Button(
            toolbar,
            text="渲染预览",
            command=self._toggle_preview_mode,
            bootstyle="info-outline",
        )
        self._preview_toggle.pack(side=tk.LEFT)
        tb.Label(toolbar, text="字号", bootstyle="secondary").pack(
            side=tk.LEFT, padx=(18, 4)
        )
        tb.Button(
            toolbar, text="A-", command=lambda: self._preview_font_adj(-1),
            bootstyle="secondary-outline",
        ).pack(side=tk.LEFT)
        tb.Button(
            toolbar, text="A+", command=lambda: self._preview_font_adj(1),
            bootstyle="secondary-outline",
        ).pack(side=tk.LEFT, padx=(4, 0))
        self._preview_font_size = 14
        self._preview_font_label = tb.Label(
            toolbar, text=f"{self._preview_font_size}px", bootstyle="secondary"
        )
        self._preview_font_label.pack(side=tk.LEFT, padx=(8, 0))

        self._preview_text = tk.Text(
            preview_frame,
            wrap=tk.WORD,
            state=tk.DISABLED,
            bg="#1a1c22",
            fg="#e0e0e0",
            insertbackground="#e0e0e0",
            relief=tk.FLAT,
            padx=10,
            pady=10,
            font=(BASE_FONT, 11),
        )
        self._preview_html = HtmlFrame(preview_frame, messages_enabled=False)

        # Default to text view; switch to rendered HTML when a report loads
        self._preview_mode = "text"
        self._preview_text.grid(row=1, column=0, sticky=tk.NSEW)
        self._preview_text.grid_remove()
        self._preview_html.grid(row=1, column=0, sticky=tk.NSEW)
        self._preview_html.grid_remove()

        self._preview_content_raw = ""

    def _show_preview_text(self) -> None:
        self._preview_html.grid_remove()
        self._preview_text.grid()
        self._preview_mode = "text"
        self._preview_toggle.configure(text="渲染预览")

    def _show_preview_html(self) -> None:
        self._preview_text.grid_remove()
        self._preview_html.load_html(
            _render_markdown_html(
                self._preview_content_raw, font_size=self._preview_font_size
            )
        )
        self._preview_html.grid()
        self._preview_mode = "html"
        self._preview_toggle.configure(text="纯文本预览")

    def _toggle_preview_mode(self) -> None:
        """Switch between rendered HTML and raw text preview."""
        if self._preview_mode == "text":
            self._show_preview_html()
        else:
            self._show_preview_text()

    def _preview_font_adj(self, delta: int) -> None:
        self._preview_font_size = max(10, min(26, self._preview_font_size + delta))
        self._preview_font_label.configure(text=f"{self._preview_font_size}px")
        if self._preview_mode == "html":
            self._show_preview_html()

    def _set_preview(self, content: str) -> None:
        self._preview_content_raw = content
        self._preview_text.configure(state=tk.NORMAL)
        self._preview_text.delete("1.0", tk.END)
        self._preview_text.insert("1.0", content)
        self._preview_text.configure(state=tk.DISABLED)

        # Reports are Markdown: render by default; plain text stays in text view
        is_report = "##" in content or "# " in content
        if is_report:
            self._show_preview_html()
        else:
            self._show_preview_text()

    def _append_token_preview(self, text: str) -> None:
        """Append one LLM stream delta to the in-memory preview."""
        if not self._token_preview_active:
            self._token_preview_active = True
            position = STAGES.index("generate_report") + 1
            label = STAGE_LABELS["generate_report"]
            self.stage_var.set(f"○ 阶段 {position}/{len(STAGES)}：{label}（流式输出中）…")
        self._preview_text.configure(state=tk.NORMAL)
        self._preview_text.insert(tk.END, text)
        self._preview_text.see(tk.END)
        self._preview_text.configure(state=tk.DISABLED)

    # =======================================================================
    # Backtest / Optimisation page
    # =======================================================================

    def _build_backtest_page(self) -> None:
        page = self.backtest_page
        page.columnconfigure(0, weight=1)
        page.rowconfigure(8, weight=1)

        # --- Card: backtest parameters ---
        bt_card = tb.LabelFrame(page, text="回测参数", padding=12)
        bt_card.grid(row=0, column=0, sticky=tk.EW, pady=(0, 10))
        bt_card.columnconfigure(1, weight=1)

        tb.Label(bt_card, text="股票代码").grid(
            row=0, column=0, sticky=tk.W, pady=(0, 8)
        )
        self.bt_ticker_var = tk.StringVar(value="600519.SH")
        tb.Entry(bt_card, textvariable=self.bt_ticker_var, width=26).grid(
            row=0, column=1, sticky=tk.EW, pady=(0, 8)
        )

        tb.Label(bt_card, text="开始日期").grid(
            row=1, column=0, sticky=tk.W, pady=(0, 8)
        )
        self.bt_start_var = tk.StringVar(value="2024-01-01")
        tb.Entry(bt_card, textvariable=self.bt_start_var, width=26).grid(
            row=1, column=1, sticky=tk.EW, pady=(0, 8)
        )

        tb.Label(bt_card, text="结束日期").grid(
            row=2, column=0, sticky=tk.W, pady=(0, 8)
        )
        self.bt_end_var = tk.StringVar(value="2026-01-01")
        tb.Entry(bt_card, textvariable=self.bt_end_var, width=26).grid(
            row=2, column=1, sticky=tk.EW, pady=(0, 8)
        )

        tb.Label(bt_card, text="快均线周期").grid(
            row=3, column=0, sticky=tk.W, pady=(0, 8)
        )
        self.bt_fast_var = tk.StringVar(value="20")
        tb.Entry(bt_card, textvariable=self.bt_fast_var, width=26).grid(
            row=3, column=1, sticky=tk.EW, pady=(0, 8)
        )

        tb.Label(bt_card, text="慢均线周期").grid(
            row=4, column=0, sticky=tk.W, pady=(0, 8)
        )
        self.bt_slow_var = tk.StringVar(value="60")
        tb.Entry(bt_card, textvariable=self.bt_slow_var, width=26).grid(
            row=4, column=1, sticky=tk.EW, pady=(0, 8)
        )

        # --- Card: optimisation parameters ---
        opt_card = tb.LabelFrame(page, text="参数优化", padding=12)
        opt_card.grid(row=1, column=0, sticky=tk.EW, pady=(0, 10))
        opt_card.columnconfigure(1, weight=1)

        tb.Label(opt_card, text="优化网格 · 快均线（逗号分隔）").grid(
            row=0, column=0, sticky=tk.W, pady=(0, 8)
        )
        self.bt_fast_grid_var = tk.StringVar(value="5,10,20")
        tb.Entry(opt_card, textvariable=self.bt_fast_grid_var, width=26).grid(
            row=0, column=1, sticky=tk.EW, pady=(0, 8)
        )

        tb.Label(opt_card, text="优化网格 · 慢均线（逗号分隔）").grid(
            row=1, column=0, sticky=tk.W, pady=(0, 8)
        )
        self.bt_slow_grid_var = tk.StringVar(value="30,60,120")
        tb.Entry(opt_card, textvariable=self.bt_slow_grid_var, width=26).grid(
            row=1, column=1, sticky=tk.EW, pady=(0, 8)
        )

        tb.Label(opt_card, text="优化目标").grid(
            row=2, column=0, sticky=tk.W, pady=(0, 8)
        )
        self.bt_objective_var = tk.StringVar(value="sharpe")
        tb.Combobox(
            opt_card,
            textvariable=self.bt_objective_var,
            values=["sharpe", "total_return", "calmar", "robust"],
            state="readonly",
        ).grid(row=2, column=1, sticky=tk.EW, pady=(0, 8))

        # --- Action buttons ---
        btns = tb.Frame(page)
        btns.grid(row=2, column=0, sticky=tk.EW, pady=(0, 6))
        self.bt_run_btn = tb.Button(
            btns, text="运行回测", command=self._run_backtest, bootstyle="primary"
        )
        self.bt_run_btn.pack(side=tk.LEFT)
        self.bt_opt_btn = tb.Button(
            btns,
            text="参数优化",
            command=self._run_optimize,
            bootstyle="info-outline",
        )
        self.bt_opt_btn.pack(side=tk.LEFT, padx=(8, 0))
        tb.Button(
            btns,
            text="回测说明",
            command=self._open_backtest_help,
            bootstyle="secondary-outline",
        ).pack(side=tk.LEFT, padx=(8, 0))

        # --- Backtest status ---
        self.bt_status_var = tk.StringVar(value="")
        tb.Label(page, textvariable=self.bt_status_var, bootstyle="secondary").grid(
            row=3, column=0, sticky=tk.W, pady=(0, 6)
        )

        # --- Backtest results ---
        self.bt_result_frame = tb.LabelFrame(page, text="回测结果", padding=6)
        self.bt_result_frame.grid(row=4, column=0, columnspan=2, sticky=tk.NSEW)
        self.bt_result_frame.columnconfigure(0, weight=1)
        self.bt_result_frame.rowconfigure(0, weight=1)

        self.bt_result_text = tk.Text(
            self.bt_result_frame,
            wrap=tk.WORD,
            state=tk.DISABLED,
            height=12,
            font=("Consolas", 11),
        )
        bt_scroll = ttk.Scrollbar(
            self.bt_result_frame, orient=tk.VERTICAL, command=self.bt_result_text.yview
        )
        self.bt_result_text.configure(yscrollcommand=bt_scroll.set)
        self.bt_result_text.grid(row=0, column=0, sticky=tk.NSEW)
        bt_scroll.grid(row=0, column=1, sticky=tk.NS)

        # 修正行权重：结果区从 row=4 开始，令其伸展
        page.rowconfigure(4, weight=1)

    def _open_backtest_help(self) -> None:
        self.notebook.select(self.help_page)
        self._help_show_guide()

    def _run_backtest(self) -> None:
        """Run a single backtest on the current parameters."""
        ticker = self.bt_ticker_var.get().strip()
        start = self.bt_start_var.get().strip()
        end = self.bt_end_var.get().strip()
        try:
            ma_fast = int(self.bt_fast_var.get())
            ma_slow = int(self.bt_slow_var.get())
        except ValueError:
            messagebox.showerror("参数错误", "快/慢均线周期必须为整数")
            return

        self._set_bt_busy(True, "正在取数…")
        threading.Thread(
            target=self._bt_worker,
            args=(ticker, start, end, ma_fast, ma_slow),
            daemon=True,
        ).start()

    def _bt_worker(
        self, ticker: str, start: str, end: str, ma_fast: int, ma_slow: int
    ) -> None:
        try:
            gw = DataGateway()
            data = gw.fetch_daily_bars(ticker, start, end, adjustment="none")
            daily = data.daily
            if daily is None or daily.empty:
                self.events.put(("bt_error", "未获取到行情数据"))
                return

            result = run_backtest(
                daily,
                spec=BacktestSpec(
                    ma_fast=ma_fast,
                    ma_slow=ma_slow,
                    initial_cash=100_000,
                    costs=CostModel(),
                ),
            )
            persist_backtest_run(
                {
                    "ticker": ticker,
                    "start_date": start,
                    "end_date": end,
                    "strategy": "ma_cross",
                    "ma_fast": ma_fast,
                    "ma_slow": ma_slow,
                },
                result,
            )

            lines = [
                f"回测结果 — {ticker} ({start}~{end})",
                "=" * 40,
                f"总收益率：{result.total_return * 100:.2f}%",
                f"年化收益率：{result.annualized_return * 100:.2f}%",
                f"基准收益（买入持有）：{result.benchmark_return * 100:.2f}%",
                f"最大回撤：{result.max_drawdown * 100:.2f}%",
                f"夏普比率：{result.sharpe:.4f}",
                f"索提诺比率：{result.sortino:.4f}",
                f"交易次数：{result.trade_count}",
                f"胜率：{result.win_rate * 100:.2f}%",
                f"持仓暴露：{result.exposure * 100:.1f}%",
                f"期末持仓：{result.open_shares} 股",
                f"策略版本：{result.strategy_version}",
                f"数据哈希：{result.data_hash[:16]}…",
            ]
            if result.trade_count == 0:
                lines.append("")
                if result.open_shares > 0:
                    lines.append(
                        "[提示] 策略在样本期内买入后一直持有至期末（未完成卖出），"
                        "因此没有完整交易记录；期末持仓反映的是未实现的浮盈/浮亏。"
                    )
                else:
                    lines.append(
                        "[提示] 样本期内没有触发 MA("
                        f"{ma_fast},{ma_slow}) 买入信号，全程空仓，"
                        "收益指标不代表策略有效性。"
                    )
                lines.append(
                    "  建议：缩短窗口以包含明显趋势行情，或调小均线周期"
                    "（如 5/20、10/30），或换一只趋势更清晰的股票。"
                )
            if result.warnings:
                lines.append("")
                lines.extend(f"· {warning}" for warning in result.warnings)
            self.events.put(("bt_result", "\n".join(lines)))
        except Exception as exc:
            self.events.put(("bt_error", f"回测失败：{exc}"))

    def _run_optimize(self) -> None:
        """Run parameter optimisation on the current parameters."""
        ticker = self.bt_ticker_var.get().strip()
        start = self.bt_start_var.get().strip()
        end = self.bt_end_var.get().strip()
        objective = self.bt_objective_var.get()
        try:
            fast_grid = [
                int(x.strip())
                for x in self.bt_fast_grid_var.get().split(",")
                if x.strip()
            ]
            slow_grid = [
                int(x.strip())
                for x in self.bt_slow_grid_var.get().split(",")
                if x.strip()
            ]
        except ValueError:
            messagebox.showerror("参数错误", "参数网格必须为以逗号分隔的整数列表")
            return

        self._set_bt_busy(True, "正在优化…")
        threading.Thread(
            target=self._opt_worker,
            args=(ticker, start, end, fast_grid, slow_grid, objective),
            daemon=True,
        ).start()

    def _opt_worker(
        self,
        ticker: str,
        start: str,
        end: str,
        fast_grid: list[int],
        slow_grid: list[int],
        objective: str,
    ) -> None:
        try:
            gw = DataGateway()
            data = gw.fetch_daily_bars(ticker, start, end, adjustment="none")
            daily = data.daily
            if daily is None or daily.empty:
                self.events.put(("bt_error", "未获取到行情数据"))
                return

            result = optimize_ma_cross(
                daily,
                {"ma_fast": fast_grid, "ma_slow": slow_grid},
                objective=objective,
                initial_cash=100_000,
                costs=CostModel(),
            )
            persist_backtest_run(
                {
                    "ticker": ticker,
                    "start_date": start,
                    "end_date": end,
                    "strategy": "ma_cross",
                    "objective": objective,
                    "fast_grid": fast_grid,
                    "slow_grid": slow_grid,
                },
                result,
            )

            lines = [
                f"参数优化结果 — {ticker} ({start}~{end})",
                "=" * 40,
                f"优化目标：{objective}",
                f"遍历组合：{len(result.candidates)} 组",
                f"选中参数：MA({result.selected_parameters['ma_fast']},"
                f"{result.selected_parameters['ma_slow']})",
                "分段表现：train_sharpe="
                f"{result.train.sharpe:.4f} / val={result.validation.sharpe:.4f} "
                f"/ test={result.test.sharpe:.4f}",
                "",
            ]
            all_zero = all(cand["trade_count"] == 0 for cand in result.candidates)
            for i, cand in enumerate(result.candidates[:10], 1):
                params = cand["parameters"]
                lines.append(
                    f"  [{i}] MA({params['ma_fast']},{params['ma_slow']}) "
                    f"sharpe={cand['sharpe']:.4f} "
                    f"return={cand['total_return'] * 100:.2f}% "
                    f"trades={cand['trade_count']}"
                )
            if all_zero:
                lines.append("")
                lines.append(
                    "[提示] 所有参数组合在训练段都没有触发交易信号，"
                    "本次优化未产生有效结论。请缩小均线周期或调整回测窗口。"
                )
            for segment in (result.train, result.validation, result.test):
                if segment.warnings:
                    lines.append("")
                    lines.extend(f"· {warning}" for warning in segment.warnings)
            self.events.put(("bt_result", "\n".join(lines)))
        except Exception as exc:
            self.events.put(("bt_error", f"优化失败：{exc}"))

    def _set_bt_busy(self, busy: bool, status: str = "") -> None:
        state = tk.DISABLED if busy else tk.NORMAL
        self.bt_run_btn.configure(state=state)
        self.bt_opt_btn.configure(state=state)
        self.bt_status_var.set(status)

    # =======================================================================
    # Settings page
    # =======================================================================

    def _build_settings_page(self) -> None:
        page = self.settings_page
        page.columnconfigure(0, weight=1)

        # --- Card: primary configuration ---
        primary = tb.LabelFrame(page, text="凭据与模型配置", padding=12)
        primary.grid(row=0, column=0, sticky=tk.EW, pady=(0, 10))
        primary.columnconfigure(1, weight=1)

        self.tushare_token_var = tk.StringVar()
        self.llm_key_var = tk.StringVar()
        self.base_url_var = tk.StringVar()
        self.model_var = tk.StringVar()
        self.deep_model_var = tk.StringVar()
        self.secret_entries: list[tb.Entry] = []

        fields = [
            ("Tushare Token *", self.tushare_token_var, True),
            ("LLM API Key", self.llm_key_var, True),
            ("LLM 接口地址", self.base_url_var, False),
            ("普通模型", self.model_var, False),
            ("深度分析模型", self.deep_model_var, False),
        ]
        for row, (label, variable, secret) in enumerate(fields):
            tb.Label(primary, text=label).grid(
                row=row, column=0, sticky=tk.W, pady=(0, 8)
            )
            if label == "LLM 接口地址":
                entry: tk.Widget = tb.Combobox(
                    primary,
                    textvariable=variable,
                    values=[
                        "https://dashscope.aliyuncs.com/compatible-mode/v1",
                        "https://api.deepseek.com/v1",
                        "https://api.openai.com/v1",
                    ],
                )
            else:
                entry = tb.Entry(primary, textvariable=variable)
            entry.grid(row=row, column=1, sticky=tk.EW, pady=(0, 8))
            if secret:
                self.secret_entries.append(entry)  # type: ignore[arg-type]

        # --- Card: optional credentials ---
        optional = tb.LabelFrame(page, text="可选备用证书（供研究脚本使用）", padding=12)
        optional.grid(row=1, column=0, sticky=tk.EW, pady=(0, 10))
        optional.columnconfigure(1, weight=1)

        self.mairui_licence_var = tk.StringVar()
        self.biyingapi_appcode_var = tk.StringVar()

        tb.Label(optional, text="麦蕊 Licence").grid(
            row=0, column=0, sticky=tk.W, pady=(0, 8)
        )
        mairui_entry = tb.Entry(optional, textvariable=self.mairui_licence_var)
        mairui_entry.grid(row=0, column=1, sticky=tk.EW, pady=(0, 8))
        self.secret_entries.append(mairui_entry)

        tb.Label(optional, text="Biyingapi AppCode").grid(
            row=1, column=0, sticky=tk.W, pady=(0, 8)
        )
        biying_entry = tb.Entry(optional, textvariable=self.biyingapi_appcode_var)
        biying_entry.grid(row=1, column=1, sticky=tk.EW, pady=(0, 8))
        self.secret_entries.append(biying_entry)

        tb.Label(
            optional,
            text=(
                "这两项用于资金流交叉核验等 CLI 研究脚本（麦蕊可作全历史资金流"
                "免费核验/替代源，Biyingapi 为备用数据接口）。不参与主分析流程，可留空。"
            ),
            wraplength=640,
            bootstyle="secondary",
        ).grid(row=2, column=0, columnspan=2, sticky=tk.W, pady=(4, 0))

        # --- Actions ---
        self.show_secrets_var = tk.BooleanVar(value=False)
        tb.Checkbutton(
            page,
            text="显示密钥",
            variable=self.show_secrets_var,
            command=self._toggle_secrets,
            bootstyle="info",
        ).grid(row=2, column=0, sticky=tk.W, pady=(0, 10))

        tb.Button(
            page, text="保存设置", command=self._save_settings, bootstyle="primary"
        ).grid(row=3, column=0, sticky=tk.W)

        tb.Label(
            page,
            text=(
                "设置仅保存在当前 Windows 用户的本地目录，界面默认掩码显示，不会上传。"
                "Tushare Token 必需；LLM 配置可选（不使用 AI 报告时无需填写）。"
            ),
            wraplength=640,
            bootstyle="secondary",
        ).grid(row=4, column=0, sticky=tk.W, pady=(16, 0))

    # =======================================================================
    # Help page
    # =======================================================================

    def _build_help_page(self) -> None:
        page = self.help_page
        page.columnconfigure(0, weight=1)
        page.rowconfigure(1, weight=1)

        toolbar = tb.Frame(page)
        toolbar.grid(row=0, column=0, sticky=tk.EW, pady=(0, 8))
        tb.Button(
            toolbar,
            text="使用说明",
            command=self._help_show_guide,
            bootstyle="primary",
        ).pack(side=tk.LEFT)
        tb.Button(
            toolbar,
            text="检查更新",
            command=self._check_update_async,
            bootstyle="info-outline",
        ).pack(side=tk.LEFT, padx=(8, 0))

        tb.Label(toolbar, text="说明字号", bootstyle="secondary").pack(
            side=tk.LEFT, padx=(28, 4)
        )
        tb.Button(
            toolbar, text="A-", command=lambda: self._help_font_adj(-1),
            bootstyle="secondary-outline",
        ).pack(side=tk.LEFT)
        tb.Button(
            toolbar, text="A+", command=lambda: self._help_font_adj(1),
            bootstyle="secondary-outline",
        ).pack(side=tk.LEFT, padx=(4, 0))
        self._help_font_size = 16
        self._help_font_label = tb.Label(
            toolbar, text=f"{self._help_font_size}px", bootstyle="secondary"
        )
        self._help_font_label.pack(side=tk.LEFT, padx=(8, 0))

        self._help_viewer = HtmlFrame(page, messages_enabled=False)
        self._help_viewer.grid(row=1, column=0, sticky=tk.NSEW)
        self._help_show_guide()

    def _help_show_guide(self) -> None:
        """Render the built-in usage guide inside the help tab (no new window)."""
        self._help_viewer.load_html(
            _render_markdown_html(USAGE_GUIDE_MD, font_size=self._help_font_size)
        )

    def _help_font_adj(self, delta: int) -> None:
        self._help_font_size = max(11, min(26, self._help_font_size + delta))
        self._help_font_label.configure(text=f"{self._help_font_size}px")
        self._help_show_guide()

    def _check_update_async(self) -> None:
        """Check for updates via GitHub/Gitee releases API in a thread."""
        self.status_var.set("正在检查更新…")
        threading.Thread(target=self._check_update_worker, daemon=True).start()

    def _check_update_worker(self) -> None:
        local, results, download_url = check_for_updates()

        # Show result in the help tab (not a separate window)
        def _show() -> None:
            self.notebook.select(self.help_page)
            lines = [
                "## 检查更新结果",
                f"- 当前版本：**{local}**",
                "",
            ]
            lines.extend(f"- {line}" for line in results)
            if download_url:
                lines.append("")
                lines.append(f"[点击前往下载页面]({download_url})")
            self._help_viewer.load_html(
                _render_markdown_html("\n".join(lines), font_size=self._help_font_size)
            )
            self.status_var.set("准备就绪")

        self.root.after(0, _show)

    # =======================================================================
    # Event loop & worker wiring
    # =======================================================================

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

                # Backtest events
                elif event == "bt_result":
                    self._set_bt_busy(False)
                    self._show_bt_result(str(payload))
                elif event == "bt_error":
                    self._set_bt_busy(False)
                    messagebox.showerror("回测/优化失败", str(payload))
        except queue.Empty:
            pass
        self.root.after(100, self._process_events)

    def _update_stage(self, stage: str, status: str, message: str) -> None:
        position = STAGES.index(stage) + 1 if stage in STAGES else len(STAGES)
        label = STAGE_LABELS.get(stage, stage)
        arrow = "✓" if status == "done" else "○"
        self.stage_var.set(f"{arrow} 阶段 {position}/{len(STAGES)}：{label}")
        self.status_var.set(str(message))

    # =======================================================================
    # Analysis lifecycle
    # =======================================================================

    def _start_analysis(self) -> None:
        mode = self.mode_labels[self.mode_var.get()]
        codes = _split_tickers(self.ticker_var.get())

        self.analyze_button.configure(state=tk.DISABLED)
        self.open_button.configure(state=tk.DISABLED)
        self.cancel_button.configure(state=tk.NORMAL, bootstyle="secondary")
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
        self.progress.stop()
        self.analyze_button.configure(state=tk.NORMAL)
        self.open_button.configure(state=tk.NORMAL)
        self.cancel_button.configure(state=tk.DISABLED)
        self.stage_var.set("")
        self.status_var.set("分析已取消：未生成报告")

    def _batch_finished(self, items: list[BatchItem]) -> None:
        self.progress.stop()
        self.analyze_button.configure(state=tk.NORMAL)
        self.cancel_button.configure(state=tk.DISABLED)
        self.open_button.configure(state=tk.DISABLED)
        self.stage_var.set("")
        self._set_preview(_format_batch_summary(items, cancelled=False))
        ok = sum(1 for item in items if item.result is not None)
        self.status_var.set(f"批量完成：{ok}/{len(items)} 只成功")

    def _batch_cancelled(self, items: list[BatchItem]) -> None:
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

    # =======================================================================
    # Backtest result display
    # =======================================================================

    def _show_bt_result(self, text: str) -> None:
        self.bt_result_text.configure(state=tk.NORMAL)
        self.bt_result_text.delete("1.0", tk.END)
        self.bt_result_text.insert("1.0", text)
        self.bt_result_text.configure(state=tk.DISABLED)
        self.bt_status_var.set("完成")

    # =======================================================================
    # Settings
    # =======================================================================

    def _load_settings(self) -> None:
        values = get_user_settings()
        self.tushare_token_var.set(values["TUSHARE_TOKEN"])
        self.llm_key_var.set(values["LLM_API_KEY"])
        self.base_url_var.set(values["LLM_BASE_URL"])
        self.model_var.set(values["LLM_MODEL"])
        self.deep_model_var.set(values["LLM_MODEL_DEEP"])
        self.mairui_licence_var.set(values["MAIRUI_LICENCE"])
        self.biyingapi_appcode_var.set(values["BIYINGAPI_APPCODE"])

    def _toggle_secrets(self) -> None:
        mask = "" if self.show_secrets_var.get() else "•"
        for entry in self.secret_entries:
            if hasattr(entry, "configure"):
                try:
                    entry.configure(show=mask)
                except tk.TclError:
                    pass

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
                mairui_licence=self.mairui_licence_var.get(),
                biyingapi_appcode=self.biyingapi_appcode_var.get(),
            )
        except Exception as exc:
            messagebox.showerror("保存失败", str(exc))
            return
        self.status_var.set("API 设置已保存")
        messagebox.showinfo("保存成功", "API 设置已保存，仅对当前 Windows 用户生效。")

    # =======================================================================
    # File helpers
    # =======================================================================

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


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


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
    """Launch the themed desktop application."""
    _configure_logging()
    root = tb.Window(themename=DEFAULT_THEME)
    style = ttk.Style()
    style.configure(".", font=(BASE_FONT, 11))
    StockAnalysisApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
