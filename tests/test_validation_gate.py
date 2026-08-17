"""Tests for deterministic pre-LLM validation."""

from __future__ import annotations

import pandas as pd

from src.analysis.validation_gate import validate_analysis_inputs


def _daily() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "trade_date": pd.to_datetime(["2026-08-13", "2026-08-14"]),
            "open": [10.0, 10.5],
            "high": [11.0, 11.5],
            "low": [9.5, 10.0],
            "close": [10.5, 11.0],
            "volume": [100.0, 120.0],
        }
    )


def _financial() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "end_date": pd.to_datetime(["2025-12-31"]),
            "revenue": [100.0],
        }
    )


def test_validation_gate_passes_complete_consistent_inputs() -> None:
    dataset_names = ("income", "balance_sheet", "cashflow", "fina_indicator")
    datasets = {name: _financial() for name in dataset_names}
    result = validate_analysis_inputs(
        run_id="run-pass",
        ticker="600519.SH",
        requested_date="2026-08-14",
        daily=_daily(),
        datasets=datasets,
        data_quality={name: "ok" for name in datasets},
    )

    assert result.status == "pass"
    assert result.allow_llm is True
    assert result.confidence_cap == 80


def test_validation_gate_degrades_for_missing_optional_financials() -> None:
    result = validate_analysis_inputs(
        run_id="run-degraded",
        ticker="600519.SH",
        requested_date="2026-08-14",
        daily=_daily(),
        data_quality={"income": "partial"},
        data_gaps=["income"],
    )

    assert result.status == "degraded"
    assert result.allow_llm is True
    assert result.confidence_cap == 60


def test_validation_gate_blocks_invalid_market_values() -> None:
    daily = _daily()
    daily.loc[1, "high"] = float("nan")
    result = validate_analysis_inputs(
        run_id="run-block",
        ticker="600519.SH",
        requested_date="2026-08-14",
        daily=daily,
    )

    assert result.status == "block"
    assert result.allow_llm is False
    assert result.confidence_cap == 0
    assert result.blocking_reasons


def test_validation_gate_blocks_financial_data_from_the_future() -> None:
    future = pd.DataFrame({"end_date": pd.to_datetime(["2026-12-31"])})
    result = validate_analysis_inputs(
        run_id="run-future",
        ticker="600519.SH",
        requested_date="2026-08-14",
        daily=_daily(),
        datasets={"income": future},
    )

    assert result.status == "block"
    assert result.allow_llm is False
    assert any("未来数据" in reason for reason in result.blocking_reasons)


def test_validation_gate_blocks_future_announcement_revision() -> None:
    future = pd.DataFrame(
        {
            "end_date": pd.to_datetime(["2025-12-31"]),
            "ann_date": pd.to_datetime(["2026-09-01"]),
        }
    )
    result = validate_analysis_inputs(
        run_id="run-future-announcement",
        ticker="600519.SH",
        requested_date="2026-08-14",
        daily=_daily(),
        datasets={"income": future},
    )

    assert result.status == "block"
    assert any("未来信息泄漏" in reason for reason in result.blocking_reasons)
