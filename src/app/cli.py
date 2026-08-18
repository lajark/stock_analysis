"""CLI 入口 — 股票分析命令行工具。

用法:
    python -m src.app.cli --ticker 600519.SH --mode trade
    python -m src.app.cli --ticker 600519.SH --mode quick --no-llm
    python -m src.app.cli --tickers 600519.SH,000858.SZ --mode quick
    python -m src.app.cli --history
    python -m src.app.cli --history --ticker 600519.SH
    python -m src.app.cli --stats
"""

import json
import sys
from datetime import datetime
from pathlib import Path

import typer
from loguru import logger
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from src.analysis.backtest import (
    BacktestSpec,
    optimize_ma_cross,
    optimize_ma_cross_multi,
    optimize_ma_cross_rolling,
    run_backtest,
)
from src.app.backtest_records import persist_backtest_run
from src.app.service import MODES, AnalysisRequest, analyze_batch, analyze_stock, validate_ticker
from src.config import get_config
from src.data.gateway import DataGateway
from src.runtime_paths import user_data_root

app = typer.Typer(
    name="stock-analysis",
    help="个人股票分析工具 — 本地计算 + LLM 报告",
    add_completion=False,
    invoke_without_command=True,
)
console = Console()


@app.callback()
def _main_callback(ctx: typer.Context):
    """如果未提供子命令，默认执行 analyze。"""
    if ctx.invoked_subcommand is None:
        # 无子命令时显示帮助
        console.print("[bold cyan]stock_analysis[/] — 个人股票分析工具")
        console.print("\n[bold]可用命令:[/]")
        console.print("  analyze  分析股票（默认）")
        console.print("  history  查看历史记录")
        console.print("  stats    查看统计信息")
        console.print("  modes    列出分析模式")
        console.print("\n[bold]快速使用:[/]")
        console.print("  python -m src.app.cli analyze --ticker 600519.SH --mode trade")
        console.print("  python -m src.app.cli analyze --ticker 600519.SH --mode quick --no-llm")
        console.print("  python -m src.app.cli analyze --tickers 600519.SH,000858.SZ --mode quick")
        console.print("\n[dim]使用 --help 查看各命令详细选项[/]")


# ------------------------------------------------------------------
# 日志
# ------------------------------------------------------------------
def _setup_logging(debug: bool = False):
    logger.remove()
    level = "DEBUG" if debug else "INFO"
    log_dir = user_data_root() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    logger.add(
        sys.stderr,
        level=level,
        format=(
            "<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | "
            "<level>{message}</level>"
        ),
    )
    logger.add(
        log_dir / "analysis_{time:YYYY-MM-DD}.log",
        level="DEBUG",
        rotation="10 MB",
        retention="7 days",
        encoding="utf-8",
    )


def _validate_ticker(ticker: str) -> str:
    return validate_ticker(ticker)


# ------------------------------------------------------------------
# 分析命令
# ------------------------------------------------------------------
@app.command()
def analyze(
    ticker: str = typer.Option(
        None, "--ticker", "-t", help="股票代码，如 600519.SH 或 600519"
    ),
    tickers: str = typer.Option(
        None, "--tickers", help="多只股票，逗号分隔，如 600519,000858"
    ),
    date: str = typer.Option(
        None, "--date", "-d", help="分析日期 YYYY-MM-DD，默认最新交易日"
    ),
    mode: str = typer.Option(
        "quick", "--mode", "-m", help="分析模式：quick | deep | value | trade"
    ),
    no_llm: bool = typer.Option(
        False, "--no-llm", help="跳过 LLM 调用，仅输出分析包 JSON"
    ),
    chart: bool = typer.Option(
        False, "--chart", help="生成 K 线技术分析图 (HTML)"
    ),
    workers: int = typer.Option(
        None,
        "--workers",
        "-w",
        min=1,
        help="批量并发数，默认取配置 batch.max_workers（默认 1 = 串行）",
    ),
    debug: bool = typer.Option(False, "--debug", help="开启 DEBUG 日志"),
):
    """分析股票，生成 Markdown 报告。"""
    _setup_logging(debug)

    if ticker and tickers:
        console.print("[red]不能同时使用 --ticker 和 --tickers[/]")
        raise typer.Exit(1)
    if not ticker and not tickers:
        console.print("[red]请指定 --ticker 或 --tickers[/]")
        raise typer.Exit(1)

    codes = (
        [_validate_ticker(ticker)]
        if ticker
        else [_validate_ticker(t.strip()) for t in tickers.split(",")]
    )

    if mode not in MODES:
        console.print(f"[red]未知模式: {mode}，可用: {', '.join(MODES.keys())}[/]")
        raise typer.Exit(1)

    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")

    mode_info = MODES[mode]
    max_workers = workers or get_config().batch.max_workers
    worker_note = f" | 并发: {max_workers}" if len(codes) > 1 and max_workers > 1 else ""
    console.print(
        Panel.fit(
            f"[bold cyan]{mode_info['desc']}[/] | {len(codes)} 只股票 | "
            f"日期: {date} | 模型: {mode_info['model']}{worker_note}",
            title="stock_analysis",
        )
    )

    if len(codes) == 1:
        _analyze_single(codes[0], date, mode, no_llm, chart)
        return

    items = analyze_batch(
        [
            AnalysisRequest(
                ticker=code,
                date=date,
                mode=mode,
                use_llm=not no_llm,
                chart=chart,
            )
            for code in codes
        ],
        max_workers=max_workers,
    )
    failed = 0
    for i, item in enumerate(items, start=1):
        label = f"[{i}/{len(items)}]"
        if item.error:
            failed += 1
            console.print(f"  [red]{label} {item.request.ticker} 失败: {item.error}[/]")
            continue
        assert item.result is not None
        console.print(
            f"  [green]{label} {item.result.ticker} -> {item.result.output_path}[/]"
        )
        if item.result.tokens:
            console.print(
                f"      [dim]Token: {item.result.tokens.get('input_tokens', 0)}+"
                f"{item.result.tokens.get('output_tokens', 0)} | "
                f"耗时: {item.result.elapsed_seconds:.1f}s[/]"
            )
    if failed:
        console.print(f"[yellow]{failed}/{len(items)} 只股票失败（其余已分别完成）[/]")


def _analyze_single(
    code: str,
    date: str,
    mode: str,
    no_llm: bool,
    chart: bool = False,
):
    """分析单只股票。"""
    try:
        result = analyze_stock(
            AnalysisRequest(
                ticker=code,
                date=date,
                mode=mode,
                use_llm=not no_llm,
                chart=chart,
            ),
            progress=lambda message: console.print(f"[dim]{message}[/]"),
        )
    except Exception as e:
        console.print(f"[red]失败: {e}[/]")
        return
    output_label = "报告" if result.output_kind == "report" else "JSON"
    console.print(f"  [green]{output_label} -> {result.output_path}[/]")
    if result.chart_path:
        console.print(f"  [green]图表 -> {result.chart_path}[/]")
    if result.tokens:
        console.print(
            f"  [dim]Token: {result.tokens.get('input_tokens', 0)}+"
            f"{result.tokens.get('output_tokens', 0)} | 耗时: {result.elapsed_seconds:.1f}s[/]"
        )


# ------------------------------------------------------------------
# 历史命令
# ------------------------------------------------------------------
@app.command()
def history(
    ticker: str = typer.Option(None, "--ticker", "-t", help="按股票代码筛选"),
    limit: int = typer.Option(20, "--limit", "-n", help="显示条数"),
):
    """查看分析历史记录。"""
    from src.app.history import AnalysisHistory

    h = AnalysisHistory()
    records = h.list(ticker=ticker, limit=limit)

    if not records:
        console.print("[dim]暂无分析记录[/]")
        return

    table = Table(title="分析历史")
    table.add_column("ID", style="dim")
    table.add_column("日期")
    table.add_column("股票")
    table.add_column("模式")
    table.add_column("Token")
    table.add_column("费用")

    for r in records:
        tokens = r.get("tokens", {}).get("total_tokens", 0)
        cost = r.get("cost", 0)
        table.add_row(
            str(r["id"]),
            r["date"],
            f"{r['name']} ({r['ticker']})",
            r["mode"],
            str(tokens),
            f"CNY {cost:.4f}",
        )

    console.print(table)


# ------------------------------------------------------------------
# 统计命令
# ------------------------------------------------------------------
@app.command()
def stats():
    """查看分析统计信息。"""
    from src.app.history import AnalysisHistory

    h = AnalysisHistory()
    s = h.stats()

    if s["total"] == 0:
        console.print("[dim]暂无分析记录[/]")
        return

    console.print(Panel.fit(
        f"[bold]总分析次数: {s['total']}[/]\n"
        f"总 Token: {s['total_tokens']:,}\n"
        f"总费用: CNY {s['total_cost']:.4f}",
        title="统计",
    ))

    if s["by_mode"]:
        console.print("\n[bold]按模式:[/]")
        for mode, count in s["by_mode"].items():
            console.print(f"  {mode}: {count} 次")

    if s["by_ticker"]:
        console.print("\n[bold]按股票 (Top 10):[/]")
        for ticker, count in s["by_ticker"].items():
            console.print(f"  {ticker}: {count} 次")


# ------------------------------------------------------------------
# 模式列表
# ------------------------------------------------------------------
@app.command()
def modes():
    """列出所有分析模式。"""
    table = Table(title="分析模式")
    table.add_column("模式", style="cyan")
    table.add_column("说明")
    table.add_column("模型")
    table.add_column("知识库")

    for name, info in MODES.items():
        table.add_row(
            name,
            info["desc"],
            info["model"],
            "是" if info["kb"] else "否",
        )

    console.print(table)
    console.print("\n[dim]用法: python -m src.app.cli --ticker 600519.SH --mode <模式>[/]")


# ------------------------------------------------------------------
# 回测与参数优化
# ------------------------------------------------------------------
def _parse_int_grid(value: str) -> list[int]:
    try:
        values = [int(item.strip()) for item in value.split(",") if item.strip()]
    except ValueError as error:
        raise typer.BadParameter("参数必须是逗号分隔的整数") from error
    if not values:
        raise typer.BadParameter("参数网格不能为空")
    return values


def _write_json_output(payload: dict, output: Path | None) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text + "\n", encoding="utf-8")
        console.print(f"[green]结果 -> {output}[/]")
    else:
        console.print(text)


@app.command()
def backtest(
    ticker: str = typer.Option(..., "--ticker", "-t", help="股票代码，如 600519.SH"),
    start_date: str = typer.Option(..., "--start", help="开始日期 YYYY-MM-DD"),
    end_date: str = typer.Option(..., "--end", help="结束日期 YYYY-MM-DD"),
    ma_fast: int = typer.Option(20, "--fast", min=1, help="快均线周期"),
    ma_slow: int = typer.Option(60, "--slow", min=2, help="慢均线周期"),
    adjustment: str = typer.Option("none", "--adjustment", help="none | qfq | hfq"),
    initial_cash: float = typer.Option(100_000.0, "--cash", min=1, help="初始资金"),
    output: Path | None = typer.Option(None, "--output", help="可选 JSON 输出路径"),
):
    """运行只做多、T+1 开盘成交的研究回测。"""
    try:
        code = _validate_ticker(ticker)
        data = DataGateway().fetch_daily_bars(
            code, start_date, end_date, adjustment=adjustment
        )
        result = run_backtest(
            data.daily,
            spec=BacktestSpec(
                ma_fast=ma_fast,
                ma_slow=ma_slow,
                initial_cash=initial_cash,
                adjustment=adjustment,
            ),
        )
    except Exception as error:
        console.print(f"[red]回测失败: {error}[/]")
        raise typer.Exit(1) from error
    run = persist_backtest_run(
        {
            "ticker": code,
            "start_date": start_date,
            "end_date": end_date,
            "strategy": "ma_cross",
            "parameters": result.parameters,
            "data_hash": result.data_hash,
            "adjustment": adjustment,
        },
        result,
    )
    payload = result.to_dict()
    payload["run_id"] = run.run_id
    _write_json_output(payload, output)


@app.command()
def optimize(
    ticker: str = typer.Option(..., "--ticker", "-t", help="股票代码，如 600519.SH"),
    start_date: str = typer.Option(..., "--start", help="开始日期 YYYY-MM-DD"),
    end_date: str = typer.Option(..., "--end", help="结束日期 YYYY-MM-DD"),
    fast_grid: str = typer.Option("5,10,20", "--fast-grid", help="快均线候选值"),
    slow_grid: str = typer.Option("30,60,120", "--slow-grid", help="慢均线候选值"),
    objective: str = typer.Option(
        "sharpe", "--objective", help="sharpe | total_return | calmar | robust"
    ),
    train_ratio: float = typer.Option(0.6, "--train-ratio", min=0.1, max=0.89),
    validation_ratio: float = typer.Option(0.2, "--validation-ratio", min=0.05, max=0.89),
    adjustment: str = typer.Option("none", "--adjustment", help="none | qfq | hfq"),
    max_trials: int | None = typer.Option(
        None, "--max-trials", min=1, help="有效参数组合数上限，防止网格无界膨胀"
    ),
    time_budget: float | None = typer.Option(
        None,
        "--time-budget",
        min=1,
        help="运行时间预算（秒），超限提前终止并保留已有最优",
    ),
    memory_budget_mb: int | None = typer.Option(
        None,
        "--memory-limit-mb",
        min=1,
        help="内存预算（Python 堆 MiB，tracemalloc 近似），超限提前终止",
    ),
    output: Path | None = typer.Option(None, "--output", help="可选 JSON 输出路径"),
):
    """在训练集选参，并独立报告验证集和测试集结果。"""
    try:
        code = _validate_ticker(ticker)
        data = DataGateway().fetch_daily_bars(
            code, start_date, end_date, adjustment=adjustment
        )
        result = optimize_ma_cross(
            data.daily,
            {"ma_fast": _parse_int_grid(fast_grid), "ma_slow": _parse_int_grid(slow_grid)},
            objective=objective,
            train_ratio=train_ratio,
            validation_ratio=validation_ratio,
            adjustment=adjustment,
            max_trials=max_trials,
            time_budget_s=time_budget,
            memory_budget_mb=memory_budget_mb,
        )
    except Exception as error:
        console.print(f"[red]参数优化失败: {error}[/]")
        raise typer.Exit(1) from error
    run = persist_backtest_run(
        {
            "ticker": code,
            "start_date": start_date,
            "end_date": end_date,
            "strategy": "ma_cross",
            "objective": objective,
            "adjustment": adjustment,
            "max_trials": max_trials,
            "time_budget": time_budget,
            "memory_budget_mb": memory_budget_mb,
            "parameter_grid": {
                "ma_fast": _parse_int_grid(fast_grid),
                "ma_slow": _parse_int_grid(slow_grid),
            },
        },
        result,
    )
    payload = result.to_dict()
    payload["run_id"] = run.run_id
    _write_json_output(payload, output)


@app.command("optimize-rolling")
def optimize_rolling(
    ticker: str = typer.Option(..., "--ticker", "-t", help="股票代码，如 600519.SH"),
    start_date: str = typer.Option(..., "--start", help="开始日期 YYYY-MM-DD"),
    end_date: str = typer.Option(..., "--end", help="结束日期 YYYY-MM-DD"),
    train_size: int = typer.Option(..., "--train-size", min=1, help="每个窗口训练日数"),
    validation_size: int = typer.Option(
        ..., "--validation-size", min=1, help="每个窗口验证日数"
    ),
    test_size: int = typer.Option(..., "--test-size", min=1, help="每个窗口测试日数"),
    step_size: int | None = typer.Option(
        None, "--step-size", min=1, help="窗口滚动步长，默认等于测试日数"
    ),
    fast_grid: str = typer.Option("5,10,20", "--fast-grid", help="快均线候选值"),
    slow_grid: str = typer.Option("30,60,120", "--slow-grid", help="慢均线候选值"),
    objective: str = typer.Option(
        "sharpe", "--objective", help="sharpe | total_return | calmar | robust"
    ),
    adjustment: str = typer.Option("none", "--adjustment", help="none | qfq | hfq"),
    min_trades: int = typer.Option(
        1, "--min-trades", min=0, help="训练集允许选中的最小完成交易次数"
    ),
    max_trials: int | None = typer.Option(
        None, "--max-trials", min=1, help="每个滚动窗口的有效参数组合数上限"
    ),
    time_budget: float | None = typer.Option(
        None,
        "--time-budget",
        min=1,
        help="运行时间预算（秒），超限提前终止并保留已有最优",
    ),
    memory_budget_mb: int | None = typer.Option(
        None,
        "--memory-limit-mb",
        min=1,
        help="内存预算（Python 堆 MiB，tracemalloc 近似），超限提前终止",
    ),
    output: Path | None = typer.Option(None, "--output", help="可选 JSON 输出路径"),
):
    """滚动训练/验证/测试并报告参数稳定性，不自动改写系统参数。"""
    fast_values = _parse_int_grid(fast_grid)
    slow_values = _parse_int_grid(slow_grid)
    try:
        code = _validate_ticker(ticker)
        data = DataGateway().fetch_daily_bars(
            code, start_date, end_date, adjustment=adjustment
        )
        result = optimize_ma_cross_rolling(
            data.daily,
            {"ma_fast": fast_values, "ma_slow": slow_values},
            train_size=train_size,
            validation_size=validation_size,
            test_size=test_size,
            step_size=step_size,
            objective=objective,
            min_trades=min_trades,
            adjustment=adjustment,
            max_trials=max_trials,
            time_budget_s=time_budget,
            memory_budget_mb=memory_budget_mb,
        )
    except Exception as error:
        console.print(f"[red]滚动参数优化失败: {error}[/]")
        raise typer.Exit(1) from error
    run = persist_backtest_run(
        {
            "ticker": code,
            "start_date": start_date,
            "end_date": end_date,
            "strategy": "ma_cross",
            "optimization": "rolling",
            "objective": objective,
            "adjustment": adjustment,
            "train_size": train_size,
            "validation_size": validation_size,
            "test_size": test_size,
            "step_size": step_size,
            "min_trades": min_trades,
            "max_trials": max_trials,
            "time_budget": time_budget,
            "memory_budget_mb": memory_budget_mb,
            "parameter_grid": {"ma_fast": fast_values, "ma_slow": slow_values},
        },
        result,
    )
    payload = result.to_dict()
    payload["run_id"] = run.run_id
    _write_json_output(payload, output)


@app.command("optimize-multi")
def optimize_multi(
    tickers: str = typer.Option(
        ..., "--tickers", "-t", help="股票代码，逗号分隔，如 600519.SH,000858.SZ"
    ),
    start_date: str = typer.Option(..., "--start", help="开始日期 YYYY-MM-DD"),
    end_date: str = typer.Option(..., "--end", help="结束日期 YYYY-MM-DD"),
    train_size: int = typer.Option(..., "--train-size", min=1, help="每个窗口训练日数"),
    validation_size: int = typer.Option(
        ..., "--validation-size", min=1, help="每个窗口验证日数"
    ),
    test_size: int = typer.Option(..., "--test-size", min=1, help="每个窗口测试日数"),
    step_size: int | None = typer.Option(
        None, "--step-size", min=1, help="窗口滚动步长，默认等于测试日数"
    ),
    fast_grid: str = typer.Option("5,10,20", "--fast-grid", help="快均线候选值"),
    slow_grid: str = typer.Option("30,60,120", "--slow-grid", help="慢均线候选值"),
    objective: str = typer.Option(
        "robust", "--objective", help="sharpe | total_return | calmar | robust"
    ),
    adjustment: str = typer.Option("none", "--adjustment", help="none | qfq | hfq"),
    min_trades: int = typer.Option(
        1, "--min-trades", min=0, help="训练集允许选中的最小完成交易次数"
    ),
    min_successful_symbols: int = typer.Option(
        2, "--min-successful-symbols", min=1, help="形成稳定聚合结论所需的最少标的数"
    ),
    max_trials: int | None = typer.Option(
        None, "--max-trials", min=1, help="每个标的每个滚动窗口的有效参数组合数上限"
    ),
    time_budget: float | None = typer.Option(
        None,
        "--time-budget",
        min=1,
        help="运行时间预算（秒），超限提前终止并保留已有最优",
    ),
    memory_budget_mb: int | None = typer.Option(
        None,
        "--memory-limit-mb",
        min=1,
        help="内存预算（Python 堆 MiB，tracemalloc 近似），超限提前终止",
    ),
    states: str | None = typer.Option(
        None, "--states", help="可选市场状态，按 tickers 顺序逗号分隔"
    ),
    output: Path | None = typer.Option(None, "--output", help="可选 JSON 输出路径"),
):
    """跨股票滚动优化，按标的等权汇总参数稳定性。"""
    codes = [_validate_ticker(item.strip()) for item in tickers.split(",") if item.strip()]
    if not codes:
        raise typer.BadParameter("tickers 不能为空")
    state_values = [item.strip() for item in states.split(",")] if states else []
    if state_values and len(state_values) != len(codes):
        raise typer.BadParameter("states 数量必须与 tickers 一致")
    market_states = dict(zip(codes, state_values)) if state_values else None
    fast_values = _parse_int_grid(fast_grid)
    slow_values = _parse_int_grid(slow_grid)
    daily_by_symbol = {}
    gateway = DataGateway()
    fetch_warnings: list[str] = []
    for code in codes:
        try:
            data = gateway.fetch_market_data(
                code, start_date, end_date, adjustment=adjustment
            )
        except Exception as error:
            fetch_warnings.append(f"{code} 获取失败：{error}")
            continue
        daily_by_symbol[code] = data.daily
        fetch_warnings.extend(f"{code}: {warning}" for warning in data.warnings)
    if not daily_by_symbol:
        console.print("[red]没有成功获取任何标的的日线数据[/]")
        raise typer.Exit(1)
    try:
        result = optimize_ma_cross_multi(
            daily_by_symbol,
            {"ma_fast": fast_values, "ma_slow": slow_values},
            train_size=train_size,
            validation_size=validation_size,
            test_size=test_size,
            step_size=step_size,
            objective=objective,
            min_trades=min_trades,
            adjustment=adjustment,
            market_state_by_symbol=market_states,
            min_successful_symbols=min_successful_symbols,
            max_trials=max_trials,
            time_budget_s=time_budget,
            memory_budget_mb=memory_budget_mb,
        )
    except Exception as error:
        console.print(f"[red]多标的参数优化失败: {error}[/]")
        raise typer.Exit(1) from error
    run = persist_backtest_run(
        {
            "tickers": codes,
            "start_date": start_date,
            "end_date": end_date,
            "strategy": "ma_cross",
            "optimization": "multi_rolling",
            "objective": objective,
            "adjustment": adjustment,
            "train_size": train_size,
            "validation_size": validation_size,
            "test_size": test_size,
            "step_size": step_size,
            "min_trades": min_trades,
            "min_successful_symbols": min_successful_symbols,
            "max_trials": max_trials,
            "time_budget": time_budget,
            "memory_budget_mb": memory_budget_mb,
            "parameter_grid": {"ma_fast": fast_values, "ma_slow": slow_values},
        },
        result,
    )
    payload = result.to_dict()
    payload["run_id"] = run.run_id
    payload["fetch_warnings"] = fetch_warnings
    _write_json_output(payload, output)


# ------------------------------------------------------------------
# 对比命令
# ------------------------------------------------------------------
@app.command()
def compare(
    tickers: str = typer.Option(
        ..., "--tickers", "-t", help="对比股票，逗号分隔，如 600519,000858,002837"
    ),
    date: str = typer.Option(
        None, "--date", "-d", help="分析日期 YYYY-MM-DD"
    ),
    chart: bool = typer.Option(
        False, "--chart", help="生成对比走势图 (HTML)"
    ),
):
    """多股票横向对比分析。"""
    from rich.table import Table

    from src.analysis.comparison import compare_stocks
    from src.data.gateway import DataGateway

    codes = [_validate_ticker(t.strip()) for t in tickers.split(",")]
    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")

    gateway = DataGateway()
    stocks_data = {}

    console.print(f"\n[bold cyan]多股票对比[/] — {len(codes)} 只, {date}")

    for code in codes:
        console.print(f"[dim]获取 {code}...[/]", end=" ")
        try:
            data = gateway.fetch_market_data(code, "20240101", date)
            for warning in data.warnings:
                console.print(f"[yellow]{warning}[/]")
            stocks_data[code] = {
                "info": data.stock_info,
                "daily": data.daily,
                "daily_basic": data.daily_basic,
            }
            console.print(f"[green]{data.stock_info['name']} ({len(data.daily)} 日线)[/]")
        except Exception as e:
            console.print(f"[red]失败: {e}[/]")

    if not stocks_data:
        console.print("[red]无有效数据[/]")
        return

    # 对比分析
    result = compare_stocks(stocks_data)

    # 表格输出
    table = Table(title="关键指标对比")
    table.add_column("股票")
    table.add_column("代码")
    table.add_column("收盘价")
    table.add_column("趋势")
    table.add_column("RSI")
    table.add_column("MACD")
    table.add_column("PE(TTM)")
    table.add_column("买入置信度")
    table.add_column("卖出置信度")

    for row in result["stocks"]:
        pe = f"{row['pe_ttm']:.1f}" if row["pe_ttm"] else "N/A"
        table.add_row(
            row["name"],
            row["code"],
            str(row["close"]),
            row["trend"],
            str(row["rsi"]),
            row["macd_status"],
            pe,
            f"{row['buy_confidence']}%",
            f"{row['sell_confidence']}%",
        )

    console.print(table)

    # 排名
    if result["ranking"].get("pe_lowest"):
        console.print("\n[bold]PE 最低:[/]")
        for code, pe in result["ranking"]["pe_lowest"]:
            console.print(f"  {code}: {pe:.1f}")

    if result["ranking"].get("buy_confidence_highest"):
        console.print("\n[bold]买入置信度最高:[/]")
        for code, conf in result["ranking"]["buy_confidence_highest"]:
            console.print(f"  {code}: {conf}%")

    # 图表
    if chart:
        config = get_config()
        console.print("[dim]生成对比图...[/]", end=" ")
        from src.reports.charts import create_comparison_chart

        daily_data = {f"{d['info']['name']}({c})": d["daily"] for c, d in stocks_data.items()}
        chart_name = f"compare_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
        chart_path = str(config.output_dir / "charts" / chart_name)
        (config.output_dir / "charts").mkdir(parents=True, exist_ok=True)
        create_comparison_chart(daily_data, title=f"走势对比 - {date}", output_path=chart_path)
        console.print(f"[green]OK -> {chart_path}[/]")


# ------------------------------------------------------------------
# 成本命令
# ------------------------------------------------------------------
@app.command()
def cost():
    """查看 Token 使用和成本统计。"""
    from src.app.history import AnalysisHistory

    h = AnalysisHistory()
    s = h.stats()

    if s["total"] == 0:
        console.print("[dim]暂无分析记录[/]")
        return

    table = Table(title="Token 成本统计")
    table.add_column("指标", style="cyan")
    table.add_column("数值", style="green")

    table.add_row("总分析次数", str(s["total"]))
    table.add_row("总 Token 消耗", f"{s['total_tokens']:,}")
    table.add_row("总费用", f"CNY {s['total_cost']:.4f}")

    if s["total"] > 0:
        avg_tokens = s["total_tokens"] / s["total"]
        avg_cost = s["total_cost"] / s["total"]
        table.add_row("平均 Token/次", f"{avg_tokens:,.0f}")
        table.add_row("平均费用/次", f"CNY {avg_cost:.4f}")

    console.print(table)

    if s["by_mode"]:
        console.print("\n[bold]按模式:[/]")
        for mode, count in s["by_mode"].items():
            console.print(f"  {mode}: {count} 次")

    if s["by_ticker"]:
        console.print("\n[bold]最常分析:[/]")
        for ticker, count in list(s["by_ticker"].items())[:5]:
            console.print(f"  {ticker}: {count} 次")


if __name__ == "__main__":
    app()
