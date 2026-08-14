"""调用 LLM 前的最小确定性质量门。"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any, Literal

import numpy as np
import pandas as pd  # type: ignore[import-untyped]

from src.analysis.contracts import ValidationCheck, ValidationResult

_FINANCIAL_DATASETS = ("income", "balance_sheet", "cashflow", "fina_indicator")
_QUALITY_STATUSES = {"ok", "partial", "stale", "invalid"}


def validate_analysis_inputs(
    *,
    run_id: str,
    ticker: str,
    requested_date: str,
    daily: pd.DataFrame,
    datasets: Mapping[str, Any] | None = None,
    data_quality: Mapping[str, str] | None = None,
    data_gaps: tuple[str, ...] | list[str] = (),
    adjustment: str = "none",
) -> ValidationResult:
    """Validate the small set of invariants required before report generation.

    Missing optional financial data degrades confidence. Empty or structurally
    invalid critical market data blocks the LLM call. The gate never repairs
    values and never calls a network service.
    """
    checks: list[ValidationCheck] = []
    warnings: list[str] = []
    blocking_reasons: list[str] = []
    datasets = datasets or {}
    data_quality = data_quality or {}

    def add_check(
        check_id: str,
        status: Literal["pass", "warn", "fail"],
        message: str,
        dimensions: tuple[str, ...],
    ) -> None:
        checks.append(ValidationCheck(check_id, status, message, dimensions))
        if status == "warn":
            warnings.append(message)
        elif status == "fail":
            blocking_reasons.append(message)

    try:
        requested = datetime.strptime(requested_date, "%Y-%m-%d")
    except ValueError:
        add_check(
            "request.date",
            "fail",
            "请求日期不是 YYYY-MM-DD 格式",
            ("request",),
        )
        requested = None

    required_columns = ("trade_date", "open", "high", "low", "close", "volume")
    missing_columns = [column for column in required_columns if column not in daily.columns]
    if daily.empty:
        add_check("daily.required", "fail", "日线数据为空，无法进行分析", ("daily",))
    elif missing_columns:
        add_check(
            "daily.required",
            "fail",
            f"日线缺少必要字段：{', '.join(missing_columns)}",
            ("daily",),
        )
    else:
        add_check("daily.required", "pass", "日线必要字段完整", ("daily",))

        trade_dates = pd.to_datetime(daily["trade_date"], errors="coerce")
        invalid_dates = int(trade_dates.isna().sum())
        if invalid_dates:
            add_check(
                "daily.date",
                "fail",
                f"日线包含 {invalid_dates} 个无效交易日期",
                ("daily",),
            )
        else:
            latest = trade_dates.max()
            if requested is not None and latest > requested:
                add_check(
                    "daily.date",
                    "fail",
                    "日线最新日期晚于请求日期，疑似混入未来数据",
                    ("daily", "request"),
                )
            elif requested is not None and latest < requested:
                add_check(
                    "daily.date",
                    "warn",
                    f"日线最新日期为 {latest.strftime('%Y-%m-%d')}，早于请求日期",
                    ("daily",),
                )
            else:
                add_check("daily.date", "pass", "日线日期范围可用", ("daily",))

            if requested is not None and requested.weekday() >= 5:
                add_check(
                    "calendar.request_date",
                    "warn",
                    "请求日期为周末，结果使用最近可用行情日期",
                    ("calendar", "daily"),
                )

        invalid_value_columns: list[str] = []
        for column in required_columns[1:]:
            numeric = pd.to_numeric(daily[column], errors="coerce")
            values = numeric.to_numpy(dtype=float)
            if numeric.isna().any() or not np.isfinite(values).all():
                invalid_value_columns.append(column)
        if invalid_value_columns:
            add_check(
                "daily.finite",
                "fail",
                f"日线包含空值或非有限数值：{', '.join(invalid_value_columns)}",
                ("daily",),
            )
        else:
            add_check("daily.finite", "pass", "日线数值均为有限值", ("daily",))

            high = daily["high"].astype(float)
            low = daily["low"].astype(float)
            opening = daily["open"].astype(float)
            close = daily["close"].astype(float)
            volume = daily["volume"].astype(float)
            if (
                (high < pd.concat([opening, close], axis=1).max(axis=1)).any()
                or (low > pd.concat([opening, close], axis=1).min(axis=1)).any()
                or (opening <= 0).any()
                or (high <= 0).any()
                or (low <= 0).any()
                or (close <= 0).any()
                or (volume < 0).any()
            ):
                add_check(
                    "daily.range",
                    "fail",
                    "日线 OHLC 或成交量存在越界值",
                    ("daily",),
                )
            else:
                add_check("daily.range", "pass", "日线 OHLC 逻辑和范围正常", ("daily",))

    if adjustment not in {"none", "qfq", "hfq"}:
        add_check(
            "daily.adjustment",
            "fail",
            f"不支持的复权口径：{adjustment}",
            ("daily",),
        )
    else:
        add_check(
            "daily.adjustment",
            "pass",
            f"行情复权口径为 {adjustment}",
            ("daily",),
        )

    for dataset, quality in data_quality.items():
        if quality not in _QUALITY_STATUSES:
            add_check(
                f"quality.{dataset}",
                "fail",
                f"{dataset} 返回未知质量状态：{quality}",
                (dataset,),
            )
        elif quality == "invalid":
            add_check(
                f"quality.{dataset}",
                "fail",
                f"{dataset} 数据被标记为 invalid",
                (dataset,),
            )
        elif quality in {"partial", "stale"}:
            add_check(
                f"quality.{dataset}",
                "warn",
                f"{dataset} 数据质量为 {quality}，将降低结论置信度",
                (dataset,),
            )

    if data_gaps:
        warnings.append(f"存在数据缺口：{', '.join(data_gaps)}")

    for dataset in _FINANCIAL_DATASETS:
        frame = datasets.get(dataset)
        if frame is None or not isinstance(frame, pd.DataFrame) or frame.empty:
            if data_quality.get(dataset) != "invalid":
                warnings.append(f"{dataset} 数据缺失，基本面结论将降级")
            continue
        if "end_date" not in frame.columns or requested is None:
            continue
        report_dates = pd.to_datetime(frame["end_date"], errors="coerce")
        if report_dates.isna().any():
            add_check(
                f"financial.{dataset}.period",
                "fail",
                f"{dataset} 包含无效报告期",
                (dataset, "financial_period"),
            )
        elif report_dates.max() > requested:
            add_check(
                f"financial.{dataset}.period",
                "fail",
                f"{dataset} 报告期晚于请求日期，疑似混入未来数据",
                (dataset, "financial_period"),
            )

    if blocking_reasons:
        gate_status: Literal["pass", "degraded", "block"] = "block"
        confidence_cap = 0
    elif warnings:
        gate_status = "degraded"
        confidence_cap = 60
    else:
        gate_status = "pass"
        confidence_cap = 80

    return ValidationResult(
        run_id=run_id,
        status=gate_status,
        allow_llm=not blocking_reasons,
        confidence_cap=confidence_cap,
        checks=tuple(checks),
        warnings=tuple(warnings),
        blocking_reasons=tuple(blocking_reasons),
    )
