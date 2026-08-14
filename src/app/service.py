"""Reusable application service for single-stock analysis."""

import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, TypedDict

from loguru import logger

from src.config import get_config
from src.errors import ConfigError, StockAnalysisError
from src.runtime_paths import resource_root

ProgressCallback = Callable[[str], None]

class ModeInfo(TypedDict):
    """Display and LLM behavior for an existing analysis mode."""

    desc: str
    model: str
    deep: bool
    kb: bool


MODES: dict[str, ModeInfo] = {
    "quick": {"desc": "快速扫描", "model": "deepseek-v4-flash", "deep": False, "kb": False},
    "deep": {"desc": "深度分析", "model": "deepseek-v4-pro", "deep": True, "kb": True},
    "value": {"desc": "价值评估", "model": "deepseek-v4-flash", "deep": False, "kb": True},
    "trade": {"desc": "交易决策", "model": "deepseek-v4-flash", "deep": False, "kb": True},
}


@dataclass(frozen=True)
class AnalysisRequest:
    """User-selected options for a single-stock analysis."""

    ticker: str
    mode: str = "quick"
    date: str | None = None
    use_llm: bool = True
    chart: bool = False


@dataclass(frozen=True)
class AnalysisResult:
    """Files and metadata created by an analysis run."""

    ticker: str
    stock_name: str
    output_path: Path
    output_kind: str
    elapsed_seconds: float
    chart_path: Path | None = None
    tokens: dict[str, Any] = field(default_factory=dict)


def validate_ticker(ticker: str) -> str:
    """Validate and normalize a mainland stock code."""
    code = ticker.strip().upper()
    if not re.fullmatch(r"\d{6}(?:\.(?:SH|SZ))?", code):
        raise StockAnalysisError("股票代码应为 6 位数字，例如 600519 或 000858.SZ")
    if "." not in code:
        exchange = "SH" if code.startswith(("5", "6", "9")) else "SZ"
        code = f"{code}.{exchange}"
    return code


def validate_request(request: AnalysisRequest) -> AnalysisRequest:
    """Validate UI/CLI input and required configuration before network access."""
    code = validate_ticker(request.ticker)
    if request.mode not in MODES:
        raise StockAnalysisError(f"未知分析模式：{request.mode}")

    analysis_date = request.date or datetime.now().strftime("%Y-%m-%d")
    try:
        datetime.strptime(analysis_date, "%Y-%m-%d")
    except ValueError as exc:
        raise StockAnalysisError("分析日期格式应为 YYYY-MM-DD", original=exc) from exc

    config = get_config()
    if not _is_configured_secret(config.tushare_token):
        raise ConfigError("请先在“API 设置”中填写并保存 Tushare Token", key="TUSHARE_TOKEN")
    if request.use_llm and not _is_configured_secret(config.llm_api_key):
        raise ConfigError("使用 AI 报告前，请先填写并保存 LLM API Key", key="LLM_API_KEY")
    if request.use_llm and (not config.llm_base_url.strip() or not config.llm_model.strip()):
        raise ConfigError("LLM 接口地址和模型名称不能为空", key="LLM_BASE_URL")

    return AnalysisRequest(
        ticker=code,
        mode=request.mode,
        date=analysis_date,
        use_llm=request.use_llm,
        chart=request.chart,
    )


def analyze_stock(
    request: AnalysisRequest,
    progress: ProgressCallback | None = None,
) -> AnalysisResult:
    """Run the existing analysis pipeline and return its generated files."""
    validated = validate_request(request)
    config = get_config()
    started_at = time.monotonic()

    try:
        _notify(progress, "正在获取股票与行情数据…")
        from src.data.providers.tushare import TushareProvider

        provider = TushareProvider()
        stock_info = provider.get_stock_basic(validated.ticker)
        daily = provider.get_daily(validated.ticker, "20240101", validated.date or "")
        if daily.empty:
            raise StockAnalysisError(f"{validated.ticker} 没有可用的日线数据")
        daily_basic = provider.get_daily_basic(validated.ticker, validated.date or "")
        income = provider.get_income(validated.ticker, "20220101", validated.date or "")
        balance_sheet = provider.get_balance_sheet(
            validated.ticker, "20220101", validated.date or ""
        )
        cashflow = provider.get_cashflow(validated.ticker, "20220101", validated.date or "")
        fina_indicator = provider.get_fina_indicator(
            validated.ticker, "20220101", validated.date or ""
        )

        _notify(progress, "正在计算本地分析指标…")
        from src.analysis.package import build_analysis_package, package_to_json

        package = build_analysis_package(
            stock_info=stock_info,
            daily=daily,
            daily_basic=daily_basic,
            income=income,
            balance_sheet=balance_sheet,
            cashflow=cashflow,
            fina_indicator=fina_indicator,
            analysis_date=validated.date or "",
        )

        tokens: dict[str, Any] = {}
        if validated.use_llm:
            _notify(progress, "正在生成 AI 分析报告…")
            output_path, tokens = _create_llm_report(package, validated.mode)
            output_kind = "report"
            from src.app.history import AnalysisHistory

            AnalysisHistory().add(
                ticker=validated.ticker,
                name=stock_info["name"],
                mode=validated.mode,
                report_path=str(output_path),
                tokens=tokens,
                cost=estimate_cost(tokens),
                date=validated.date or "",
            )
        else:
            _notify(progress, "正在保存本地分析结果…")
            config.json_dir.mkdir(parents=True, exist_ok=True)
            output_path = config.json_dir / (
                f"{validated.ticker}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            )
            output_path.write_text(package_to_json(package), encoding="utf-8")
            output_kind = "json"

        chart_path = None
        if validated.chart:
            _notify(progress, "正在生成 K 线图…")
            chart_path = _create_chart(
                validated.ticker,
                stock_info,
                daily,
                validated.date or "",
            )

        _notify(progress, "分析完成")
        return AnalysisResult(
            ticker=validated.ticker,
            stock_name=str(stock_info.get("name", "")),
            output_path=Path(output_path),
            output_kind=output_kind,
            elapsed_seconds=time.monotonic() - started_at,
            chart_path=chart_path,
            tokens=tokens,
        )
    except StockAnalysisError:
        raise
    except Exception as exc:
        logger.exception("Single-stock analysis failed")
        raise StockAnalysisError(f"分析失败：{exc}", original=exc) from exc


def estimate_cost(usage: dict[str, Any]) -> float:
    """Estimate cost using the project's current reference pricing."""
    input_price = 2.0 / 1_000_000
    output_price = 8.0 / 1_000_000
    return round(
        usage.get("input_tokens", 0) * input_price
        + usage.get("output_tokens", 0) * output_price,
        4,
    )


def _create_llm_report(package: dict[str, Any], mode: str) -> tuple[Path, dict[str, Any]]:
    from src.reports.knowledge_retriever import get_knowledge_context
    from src.reports.llm_client import LLMClient
    from src.reports.renderer import render_report

    llm = LLMClient()
    system_prompt = _load_system_prompt(mode)
    kb_context = get_knowledge_context(mode)
    if kb_context:
        system_prompt += kb_context
    llm_output = llm.generate(
        system_prompt,
        json.dumps(package, ensure_ascii=False, indent=2),
        deep=bool(MODES[mode]["deep"]),
    )
    usage = llm.last_usage or {}
    config = get_config()
    output_path = render_report(
        package=package,
        llm_output=llm_output,
        llm_model=str(usage.get("model", config.llm_model)),
        tokens=usage,
    )
    return Path(output_path), usage


def _load_system_prompt(mode: str) -> str:
    filenames = {
        "quick": "quick_scan.md",
        "deep": "deep_analysis.md",
        "value": "value_assessment.md",
        "trade": "trade_plan.md",
    }
    prompt_path = resource_root() / "src" / "reports" / "prompts" / filenames[mode]
    return prompt_path.read_text(encoding="utf-8")


def _create_chart(
    code: str,
    stock_info: dict[str, Any],
    daily: Any,
    analysis_date: str,
) -> Path:
    from src.analysis.indicators import calc_all_indicators
    from src.reports.charts import create_kline_chart

    config = get_config()
    chart_dir = config.output_dir / "charts"
    chart_dir.mkdir(parents=True, exist_ok=True)
    chart_path = chart_dir / f"{code}_{datetime.now().strftime('%Y%m%d_%H%M%S')}_kline.html"
    create_kline_chart(
        calc_all_indicators(daily),
        title=f"{stock_info['name']} ({code}) - {analysis_date}",
        output_path=str(chart_path),
    )
    return chart_path


def _is_configured_secret(value: str) -> bool:
    normalized = value.strip().lower()
    return bool(normalized and not normalized.startswith("your_") and normalized != "changeme")


def _notify(callback: ProgressCallback | None, message: str) -> None:
    if callback:
        callback(message)
