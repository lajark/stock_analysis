"""CLI 入口 — 股票分析命令行工具。

用法:
    python -m src.app.cli --ticker 600519.SH --mode trade
    python -m src.app.cli --ticker 600519.SH --mode quick --no-llm
    python -m src.app.cli --tickers 600519.SH,000858.SZ --mode quick
    python -m src.app.cli --history
    python -m src.app.cli --history --ticker 600519.SH
    python -m src.app.cli --stats
"""

import sys
from datetime import datetime

import typer
from loguru import logger
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from src.app.service import MODES, AnalysisRequest, analyze_stock, validate_ticker
from src.config import get_config
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
    console.print(
        Panel.fit(
            f"[bold cyan]{mode_info['desc']}[/] | {len(codes)} 只股票 | "
            f"日期: {date} | 模型: {mode_info['model']}",
            title="stock_analysis",
        )
    )

    for i, code in enumerate(codes):
        if len(codes) > 1:
            console.print(f"\n[bold]--- [{i+1}/{len(codes)}] {code} ---[/]")
        _analyze_single(code, date, mode, no_llm, chart)


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
