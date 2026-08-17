"""Reusable application service for single-stock analysis."""

import json
import re
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timedelta
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
# stage, status ("running"|"done"), message — the machine-readable progress feed.
StageProgressCallback = Callable[[str, str, str], None]
# Canonical pipeline stages in execution order (used by the GUI for a
# deterministic stage list; also the names used for RunRecord.complete_stage).
STAGES = (
    "validate_request",
    "acquire_data",
    "validate_evidence",
    "build_evidence",
    "generate_report",
    "render_chart",
    "finish",
)


class AnalysisCancelledError(StockAnalysisError):
    """Raised at a safe checkpoint when the user cancels; no partial artifacts emitted."""


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


@dataclass
class BatchItem:
    """Outcome of one request in a batch run (failure-isolated)."""

    request: AnalysisRequest
    result: AnalysisResult | None = None
    error: str | None = None


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


def _fetch_cninfo_events(
    ticker: str,
    analysis_date: str,
    config: Any,
) -> tuple[list[dict[str, Any]], str | None]:
    """Fetch official disclosure events without making them a hard dependency.

    The primary financial data path remains Tushare. CNINFO failures only add a
    quality warning because the evidence layer explicitly supports an
    ``insufficient`` official-event dimension.
    """
    cninfo_config = getattr(config, "cninfo", None)
    if cninfo_config is None or not bool(getattr(cninfo_config, "enabled", False)):
        return [], None
    try:
        from src.data.cninfo import CninfoAnnouncementClient

        end = datetime.strptime(analysis_date, "%Y-%m-%d")
        lookback_days = max(1, int(getattr(cninfo_config, "lookback_days", 365)))
        start = end - timedelta(days=lookback_days)
        client = CninfoAnnouncementClient(
            base_url=str(
                getattr(cninfo_config, "base_url", "https://www.cninfo.com.cn")
                or "https://www.cninfo.com.cn"
            ),
            static_base_url=str(
                getattr(cninfo_config, "static_base_url", "https://static.cninfo.com.cn")
            ),
            timeout=int(getattr(cninfo_config, "timeout", 30)),
            page_size=int(getattr(cninfo_config, "page_size", 30)),
            max_pages=int(getattr(cninfo_config, "max_pages", 20)),
            include_hk=bool(getattr(cninfo_config, "include_hk", False)),
        )
        records = client.fetch_event_records(ticker, start, end)
        logger.info("CNINFO 公告事件获取完成：{}，{} 条", ticker, len(records))
        return records, None
    except Exception as error:
        warning = f"official events: CNINFO 获取失败，已保留其他证据（{error}）"
        logger.warning(warning)
        return [], warning


def analyze_stock(
    request: AnalysisRequest,
    progress: ProgressCallback | None = None,
    *,
    cancel_event: threading.Event | None = None,
    stage_progress: StageProgressCallback | None = None,
    token_callback: ProgressCallback | None = None,
    gateway: Any | None = None,
    llm_factory: Callable[[], Any] | None = None,
) -> AnalysisResult:
    """Run the existing analysis pipeline and return its generated files.

    ``token_callback`` is optional GUI-streaming wiring: when provided, the LLM
    report is generated through the streaming path and each text delta is
    forwarded before the full report is rendered to disk. When omitted (CLI,
    batch), behavior is byte-for-byte identical to the non-streaming path.
    """
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
        _report_stage(stage_progress, "validate_request", "running", "正在校验输入…")
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
        _report_stage(stage_progress, "validate_request", "done", "输入校验完成")
        config = get_config()

        _notify(progress, "正在获取股票与行情数据…")
        _report_stage(stage_progress, "acquire_data", "running", "正在获取股票与行情数据…")
        stage_started = time.monotonic()
        if gateway is None:
            from src.data.gateway import DataGateway

            gateway = DataGateway()

        data = gateway.fetch(
            validated.ticker,
            "20240101",
            validated.date or "",
            financial_start_date="20220101",
        )
        official_event_records, official_event_warning = _fetch_cninfo_events(
            validated.ticker,
            validated.date or "",
            config,
        )
        data_warnings = list(data.warnings)
        if official_event_warning:
            data_warnings.append(official_event_warning)
        if data.warnings:
            for warning in data.warnings:
                logger.warning(warning)
        if official_event_warning:
            logger.warning(official_event_warning)
        run.complete_stage(
            "acquire_data",
            elapsed_ms=_elapsed_ms(stage_started),
            details={
                "providers": data.providers,
                "quality": data.quality,
                "cache_datasets": [
                    name for name, provider in data.providers.items() if provider == "cache"
                ],
                "warning_count": len(data_warnings),
                "official_event_count": len(official_event_records),
            },
        )
        _report_stage(stage_progress, "acquire_data", "done", "数据获取完成")
        _check_cancel(cancel_event)
        stock_info = data.stock_info
        daily = data.daily
        daily_basic = data.daily_basic
        income = data.income
        balance_sheet = data.balance_sheet
        cashflow = data.cashflow
        fina_indicator = data.fina_indicator
        moneyflow = data.moneyflow

        stage_started = time.monotonic()
        _report_stage(stage_progress, "validate_evidence", "running", "正在校验数据质量…")
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
                "moneyflow": moneyflow,
            },
            data_quality=data.quality,
            data_gaps=data.data_gaps,
            adjustment=data.adjustment,
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
        _report_stage(stage_progress, "validate_evidence", "done", "数据质量校验完成")
        if not validation.allow_llm:
            reason = "；".join(validation.blocking_reasons) or "关键数据未通过校验"
            raise DataValidationError(
                f"数据质量校验未通过，已阻止报告生成：{reason}",
                code="validation_block",
            )

        _notify(progress, "正在计算本地分析指标…")
        _report_stage(stage_progress, "build_evidence", "running", "正在计算本地分析指标…")
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
            moneyflow=moneyflow,
            official_event_records=official_event_records,
            adjustment=data.adjustment,
            run_id=run.run_id,
            provider_name=data.provider_label,
            dataset_providers=data.providers,
            dataset_quality=data.quality,
            data_warnings=data_warnings,
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
        _report_stage(stage_progress, "build_evidence", "done", "分析指标计算完成")

        tokens: dict[str, Any] = {}
        evidence_path: Path | None = None
        stage_started = time.monotonic()
        _check_cancel(cancel_event)
        _report_stage(stage_progress, "generate_report", "running", "正在生成分析报告…")
        if validated.use_llm:
            _notify(progress, "正在生成 AI 分析报告…")
            context = _route_context(validated.mode, package)
            output_path, tokens = _create_llm_report(
                package,
                validated.mode,
                context=context,
                run_id=run.run_id,
                token_callback=token_callback,
                cancel_event=cancel_event,
                llm_factory=llm_factory,
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
            _report_stage(stage_progress, "generate_report", "done", "分析报告生成完成")
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
                f"{validated.ticker}_"
                f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{run.run_id[:8]}.json"
            )
            # run_id suffix guarantees uniqueness between concurrent batch
            # workers on one ticker; atomic tmp+replace prevents a reader from
            # seeing a half-written JSON document.
            tmp = output_path.with_suffix(".json.tmp")
            tmp.write_text(package_to_json(package), encoding="utf-8")
            tmp.replace(output_path)
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
            _report_stage(stage_progress, "generate_report", "done", "分析报告生成完成")

        if evidence_path is not None:
            run.stages["generate_report"]["evidence_artifact"] = str(evidence_path)

        chart_path = None
        if validated.chart:
            _check_cancel(cancel_event)
            stage_started = time.monotonic()
            _notify(progress, "正在生成 K 线图…")
            _report_stage(stage_progress, "render_chart", "running", "正在生成 K 线图…")
            chart_path = _create_chart(
                validated.ticker,
                stock_info,
                daily,
                validated.date or "",
                run.run_id,
            )
            run.complete_stage(
                "render_chart",
                elapsed_ms=_elapsed_ms(stage_started),
                details={"artifact": str(chart_path)},
            )
            _report_stage(stage_progress, "render_chart", "done", "K 线图生成完成")

        _notify(progress, "分析完成")
        _report_stage(stage_progress, "finish", "running", "分析完成")
        run.finish()
        _report_stage(stage_progress, "finish", "done", "完成")
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
    except AnalysisCancelledError:
        if run.outcome == "running":
            run.finish("cancelled")
        _persist_run_record(run_store, run)
        raise
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


def analyze_batch(
    requests: Sequence[AnalysisRequest],
    *,
    max_workers: int = 1,
    cancel_event: threading.Event | None = None,
    progress: ProgressCallback | None = None,
    stage_progress: StageProgressCallback | None = None,
    item_prefix: Callable[[int, int], str] | None = None,
    gateway: Any | None = None,
    llm_factory: Callable[[], Any] | None = None,
) -> list[BatchItem]:
    """Analyze several stocks through a bounded thread pool.

    Each request is failure-isolated: one failing/cancelled item never aborts
    the rest of the batch. Results are returned in input order. Data requests
    are additionally throttled by the provider rate limiter and LLM calls by
    their own semaphore, so batch concurrency stays within configured bounds.

    ``progress``/``stage_progress``/``item_prefix`` are optional GUI wiring for
    batch progress display: stage messages are prefixed with
    ``item_prefix(item_index, total)`` (e.g. ``[3/8]``). CLI callers omit them
    and behavior is byte-for-byte unchanged.

    ``gateway``/``llm_factory`` are lightweight dependency-injection hooks
    (ROADMAP L226): when omitted the pipeline constructs its production
    ``DataGateway``/``LLMClient`` exactly as before; when provided they are
    forwarded to every :func:`analyze_stock` item (and only injected into the
    item kwargs when not None, preserving the default kwargs contract).
    """
    items = [BatchItem(request=req) for req in requests]
    workers = max(1, max_workers)

    def prefixed(index: int, total: int) -> StageProgressCallback | None:
        if stage_progress is None:
            return None
        if item_prefix is None:
            return stage_progress
        prefix = item_prefix(index, total)

        def forward(stage: str, status: str, message: str) -> None:
            stage_progress(stage, status, f"{prefix} {message}")

        return forward

    def item_kwargs(index: int, total: int) -> dict[str, Any]:
        kwargs: dict[str, Any] = {"cancel_event": cancel_event}
        if progress is not None:
            kwargs["progress"] = progress
        cb = prefixed(index, total)
        if cb is not None:
            kwargs["stage_progress"] = cb
        if gateway is not None:
            kwargs["gateway"] = gateway
        if llm_factory is not None:
            kwargs["llm_factory"] = llm_factory
        return kwargs

    if workers == 1:
        # Serial path: identical to the historic loop, still failure-isolated.
        for index, item in enumerate(items):
            try:
                item.result = analyze_stock(
                    item.request, **item_kwargs(index, len(items))
                )
            except Exception as exc:
                item.error = _safe_error_message(exc)
        return items

    with ThreadPoolExecutor(max_workers=workers) as executor:
        future_to_index = {
            executor.submit(
                analyze_stock, req, **item_kwargs(index, len(requests))
            ): index
            for index, req in enumerate(requests)
        }
        for future in as_completed(future_to_index):
            item = items[future_to_index[future]]
            try:
                item.result = future.result()
            except Exception as exc:
                item.error = _safe_error_message(exc)

    return items


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
    run_id: str | None = None,
    token_callback: ProgressCallback | None = None,
    cancel_event: threading.Event | None = None,
    llm_factory: Callable[[], Any] | None = None,
) -> tuple[Path, dict[str, Any]]:
    from src.reports.llm_client import LLMClient, LLMStreamCancelledError
    from src.reports.renderer import render_report

    context = context or _route_context(mode, package)
    llm = (llm_factory or LLMClient)()
    system_prompt = _load_system_prompt(mode)
    if context.get("prompt_text"):
        system_prompt += str(context["prompt_text"])
    user_prompt = json.dumps(package, ensure_ascii=False, indent=2)
    if token_callback is not None:
        # GUI streaming path: forward deltas as they arrive, but only render
        # the final report after the full text is available (preview never
        # persists partial output). Cancellation is honored between chunks by
        # the client and translated into the app-layer error at the checkpoint.
        collected: list[str] = []
        try:
            for delta in llm.generate_stream(
                system_prompt,
                user_prompt,
                deep=bool(MODES[mode]["deep"]),
                cancel_event=cancel_event,
            ):
                token_callback(delta)
                collected.append(delta)
        except LLMStreamCancelledError as exc:
            raise AnalysisCancelledError(str(exc)) from None
        llm_output = "".join(collected)
    else:
        llm_output = llm.generate(
            system_prompt,
            user_prompt,
            deep=bool(MODES[mode]["deep"]),
        )
    usage = llm.last_usage or {}
    config = get_config()
    output_path = render_report(
        package=package,
        llm_output=llm_output,
        llm_model=str(usage.get("model", config.llm_model)),
        tokens=usage,
        run_id=run_id,
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
    run_id: str,
) -> Path:
    from src.analysis.indicators import calc_all_indicators
    from src.analysis.parameters import AnalysisParameters
    from src.reports.charts import create_kline_chart

    config = get_config()
    chart_dir = config.output_dir / "charts"
    chart_dir.mkdir(parents=True, exist_ok=True)
    chart_path = chart_dir / (
        f"{code}_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{run_id[:8]}_kline.html"
    )
    create_kline_chart(
        calc_all_indicators(daily, AnalysisParameters.from_config(config.analysis)),
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


def _report_stage(
    stage_progress: StageProgressCallback | None,
    stage: str,
    status: str,
    message: str,
) -> None:
    if stage_progress:
        stage_progress(stage, status, message)


def _check_cancel(cancel_event: threading.Event | None) -> None:
    """Raise at a safe checkpoint when cancellation was requested.

    Checkpoints sit at stage boundaries *before* any artifact (report/chart/
    evidence package) is written, so a cancelled run emits no partial output.
    The GUI polls this event from its worker thread; Tk event-loop callbacks
    only set it (threading.Event is thread-safe).
    """
    if cancel_event is not None and cancel_event.is_set():
        raise AnalysisCancelledError("分析已由用户取消")


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
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(package, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
        return path
    except (OSError, TypeError, ValueError) as exc:
        logger.warning("Unable to persist evidence package ({})", type(exc).__name__)
        return None
