"""Reusable application service for single-stock analysis."""

import json
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, TypedDict

from loguru import logger

from src.analysis.contracts import RunRecord
from src.analysis.validation_gate import validate_analysis_inputs
from src.app.run_records import RunRecordStore
from src.config import get_config
from src.errors import ConfigError, DataValidationError, StockAnalysisError
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
    run = RunRecord.start(
        {
            "ticker": request.ticker,
            "mode": request.mode,
            "analysis_date": request.date or "",
            "use_llm": request.use_llm,
            "chart": request.chart,
        }
    )
    run_store = RunRecordStore()
    started_at = time.monotonic()

    try:
        stage_started = time.monotonic()
        validated = validate_request(request)
        run.complete_stage(
            "validate_request",
            elapsed_ms=_elapsed_ms(stage_started),
            details={
                "ticker": validated.ticker,
                "analysis_date": validated.date,
                "use_llm": validated.use_llm,
            },
        )
        config = get_config()

        _notify(progress, "正在获取股票与行情数据…")
        stage_started = time.monotonic()
        from src.data.gateway import DataGateway

        data = DataGateway().fetch(
            validated.ticker,
            "20240101",
            validated.date or "",
            financial_start_date="20220101",
        )
        if data.warnings:
            for warning in data.warnings:
                logger.warning(warning)
        run.complete_stage(
            "acquire_data",
            elapsed_ms=_elapsed_ms(stage_started),
            details={
                "providers": data.providers,
                "quality": data.quality,
                "cache_datasets": [
                    name for name, provider in data.providers.items() if provider == "cache"
                ],
                "warning_count": len(data.warnings),
            },
        )
        stock_info = data.stock_info
        daily = data.daily
        daily_basic = data.daily_basic
        income = data.income
        balance_sheet = data.balance_sheet
        cashflow = data.cashflow
        fina_indicator = data.fina_indicator

        stage_started = time.monotonic()
        validation = validate_analysis_inputs(
            run_id=run.run_id,
            ticker=validated.ticker,
            requested_date=validated.date or "",
            daily=daily,
            datasets={
                "daily_basic": daily_basic,
                "income": income,
                "balance_sheet": balance_sheet,
                "cashflow": cashflow,
                "fina_indicator": fina_indicator,
            },
            data_quality=data.quality,
            data_gaps=data.data_gaps,
        )
        run.complete_stage(
            "validate_evidence",
            elapsed_ms=_elapsed_ms(stage_started),
            details={
                "status": validation.status,
                "allow_llm": validation.allow_llm,
                "confidence_cap": validation.confidence_cap,
                "blocking_reasons": validation.blocking_reasons,
            },
        )
        if not validation.allow_llm:
            reason = "；".join(validation.blocking_reasons) or "关键数据未通过校验"
            raise DataValidationError(
                f"数据质量校验未通过，已阻止报告生成：{reason}",
                code="validation_block",
            )

        _notify(progress, "正在计算本地分析指标…")
        from src.analysis.package import build_analysis_package, package_to_json

        stage_started = time.monotonic()
        previous_package = _load_previous_package(config.json_dir, validated.ticker)
        package = build_analysis_package(
            stock_info=stock_info,
            daily=daily,
            daily_basic=daily_basic,
            income=income,
            balance_sheet=balance_sheet,
            cashflow=cashflow,
            fina_indicator=fina_indicator,
            analysis_date=validated.date or "",
            run_id=run.run_id,
            provider_name=data.provider_label,
            dataset_providers=data.providers,
            dataset_quality=data.quality,
            data_warnings=data.warnings,
            validation=validation.to_dict(),
            previous_package=previous_package,
        )
        run.complete_stage(
            "build_evidence",
            elapsed_ms=_elapsed_ms(stage_started),
            details={
                "schema_version": package.get("schema_version"),
                "quality": package.get("quality"),
                "data_gaps": package.get("data_gaps", []),
            },
        )

        tokens: dict[str, Any] = {}
        evidence_path: Path | None = None
        stage_started = time.monotonic()
        if validated.use_llm:
            _notify(progress, "正在生成 AI 分析报告…")
            context = _route_context(validated.mode, package)
            output_path, tokens = _create_llm_report(
                package,
                validated.mode,
                context=context,
            )
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
            run.complete_stage(
                "generate_report",
                elapsed_ms=_elapsed_ms(stage_started),
                details={
                    "llm_used": True,
                    "model": tokens.get("model", config.llm_model),
                    "prompt_version": f"{validated.mode}-v1",
                    "tokens": tokens,
                    "artifact": str(output_path),
                    "context_router_version": context["router_version"],
                    "context_dimensions": context["dimensions"],
                    "context_fragment_ids": [
                        fragment["id"] for fragment in context["fragments"]
                    ],
                    "context_hash": context["content_hash"],
                    "context_chars": context["char_count"],
                },
            )
            evidence_path = _save_evidence_package(
                config.json_dir,
                validated.ticker,
                run.run_id,
                package,
            )
        else:
            _notify(progress, "正在保存本地分析结果…")
            config.json_dir.mkdir(parents=True, exist_ok=True)
            output_path = config.json_dir / (
                f"{validated.ticker}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            )
            output_path.write_text(package_to_json(package), encoding="utf-8")
            output_kind = "json"
            run.complete_stage(
                "generate_report",
                elapsed_ms=_elapsed_ms(stage_started),
                details={
                    "llm_used": False,
                    "model": None,
                    "prompt_version": None,
                    "tokens": {},
                    "artifact": str(output_path),
                },
            )

        if evidence_path is not None:
            run.stages["generate_report"]["evidence_artifact"] = str(evidence_path)

        chart_path = None
        if validated.chart:
            stage_started = time.monotonic()
            _notify(progress, "正在生成 K 线图…")
            chart_path = _create_chart(
                validated.ticker,
                stock_info,
                daily,
                validated.date or "",
            )
            run.complete_stage(
                "render_chart",
                elapsed_ms=_elapsed_ms(stage_started),
                details={"artifact": str(chart_path)},
            )

        _notify(progress, "分析完成")
        run.finish()
        _persist_run_record(run_store, run)
        return AnalysisResult(
            ticker=validated.ticker,
            stock_name=str(stock_info.get("name", "")),
            output_path=Path(output_path),
            output_kind=output_kind,
            elapsed_seconds=time.monotonic() - started_at,
            chart_path=chart_path,
            tokens=tokens,
        )
    except StockAnalysisError as exc:
        if run.outcome == "running":
            run.fail(type(exc).__name__, _safe_error_message(exc))
        _persist_run_record(run_store, run)
        raise
    except Exception as exc:
        logger.exception("Single-stock analysis failed")
        wrapped = StockAnalysisError(f"分析失败：{exc}", original=exc)
        run.fail(type(exc).__name__, _safe_error_message(wrapped))
        _persist_run_record(run_store, run)
        raise wrapped from exc


def estimate_cost(usage: dict[str, Any]) -> float:
    """Estimate cost using the project's current reference pricing."""
    input_price = 2.0 / 1_000_000
    output_price = 8.0 / 1_000_000
    return round(
        usage.get("input_tokens", 0) * input_price
        + usage.get("output_tokens", 0) * output_price,
        4,
    )


def _create_llm_report(
    package: dict[str, Any],
    mode: str,
    *,
    context: Mapping[str, Any] | None = None,
) -> tuple[Path, dict[str, Any]]:
    from src.reports.llm_client import LLMClient
    from src.reports.renderer import render_report

    context = context or _route_context(mode, package)
    llm = LLMClient()
    system_prompt = _load_system_prompt(mode)
    if context.get("prompt_text"):
        system_prompt += str(context["prompt_text"])
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


def _route_context(mode: str, package: Mapping[str, Any]) -> dict[str, Any]:
    from src.reports.context_router import route_context

    return route_context(mode, package)


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


def _elapsed_ms(started_at: float) -> int:
    return int((time.monotonic() - started_at) * 1000)


def _safe_error_message(error: Exception) -> str:
    """Keep credentials out of the local run record while retaining context."""
    message = str(error)
    message = re.sub(
        r"(?i)(api[_-]?key|token|password|secret)\s*[:=]\s*[^\s,;]+",
        r"\1=<redacted>",
        message,
    )
    return message[:500]


def _persist_run_record(store: RunRecordStore, run: RunRecord) -> None:
    try:
        store.save(run)
    except Exception as exc:
        logger.warning("Unable to persist run record ({})", type(exc).__name__)


def _load_previous_package(json_dir: Path, ticker: str) -> dict[str, Any] | None:
    """Load the latest structured package, never a rendered LLM report."""
    if not json_dir.exists():
        return None
    paths = sorted(
        json_dir.glob(f"{ticker}_*.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for path in paths:
        try:
            package = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        stock = package.get("stock") if isinstance(package, dict) else None
        if (
            isinstance(package, dict)
            and package.get("schema_version")
            and package.get("run_id")
            and isinstance(stock, dict)
            and stock.get("code") == ticker
        ):
            return package
    return None


def _save_evidence_package(
    json_dir: Path,
    ticker: str,
    run_id: str,
    package: dict[str, Any],
) -> Path | None:
    """Persist the structured input used by an LLM report for future diffs."""
    try:
        json_dir.mkdir(parents=True, exist_ok=True)
        path = json_dir / (
            f"{ticker}_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{run_id[:8]}_evidence.json"
        )
        path.write_text(json.dumps(package, ensure_ascii=False, indent=2), encoding="utf-8")
        return path
    except (OSError, TypeError, ValueError) as exc:
        logger.warning("Unable to persist evidence package ({})", type(exc).__name__)
        return None
