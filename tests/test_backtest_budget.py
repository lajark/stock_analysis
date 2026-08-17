"""优化器时间/内存预算测试（本地离线，时钟与内存可注入保证确定性）。"""

import pandas as pd
import pytest

from src.analysis import backtest as backtest_module
from src.analysis.backtest import (
    BacktestError,
    CostModel,
    optimize_ma_cross,
    optimize_ma_cross_multi,
    optimize_ma_cross_rolling,
)
from tests.test_backtest import _prices


def _grid() -> dict[str, list[int]]:
    return {"ma_fast": [2, 3], "ma_slow": [4, 6]}


def _big_prices() -> pd.DataFrame:
    """400 unique trading days; enough Python-heap traffic for tracemalloc > 0 MB."""
    base = _prices()["close"].tolist() * 10
    dates = pd.date_range("2020-01-02", periods=400, freq="B")
    return pd.DataFrame(
        {
            "trade_date": dates,
            "open": base,
            "high": [value + 0.5 for value in base],
            "low": [value - 0.5 for value in base],
            "close": base,
            "volume": [1000] * len(base),
        }
    )


def _free_costs() -> CostModel:
    return CostModel(
        commission_rate=0,
        minimum_commission=0,
        stamp_duty_rate=0,
        transfer_fee_rate=0,
        slippage_bps=0,
        lot_size=1,
    )


@pytest.fixture
def slow_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """perf_counter starts at 0 then advances +50s per read.

    With a 60s budget the first boundary check passes (50 < 60) and every
    later read exceeds it, so one trial/window/symbol completes before the
    early stop -- deterministic regardless of interior call counts.
    """
    value = 0.0

    def _frozen_clock() -> float:
        nonlocal value
        result = value
        value += 50.0
        return result

    monkeypatch.setattr(backtest_module.time, "perf_counter", _frozen_clock)


def test_single_time_budget_early_stop_keeps_best(slow_clock) -> None:
    result = optimize_ma_cross(
        _prices(),
        _grid(),
        train_ratio=0.5,
        validation_ratio=0.25,
        costs=_free_costs(),
        time_budget_s=60,
    )
    sensitivity = result.sensitivity
    assert sensitivity["time_budget_status"] == "exceeded"
    assert sensitivity["time_budget_s"] == 60
    assert sensitivity["trial_count"] < 4
    assert result.selected_parameters["ma_fast"] < result.selected_parameters["ma_slow"]
    # 预算超限是软门：即使提前终止，仍保留已选参数完成验证/测试
    assert result.validation is not None
    assert result.test is not None


def test_single_time_budget_within_limit() -> None:
    result = optimize_ma_cross(
        _prices(),
        _grid(),
        train_ratio=0.5,
        validation_ratio=0.25,
        costs=_free_costs(),
        time_budget_s=1_000_000,
    )
    assert result.sensitivity["time_budget_status"] == "within_limit"
    assert result.sensitivity["time_budget_s"] == 1_000_000
    assert result.sensitivity["trial_count"] == 4


def test_single_without_budgets_marks_not_configured() -> None:
    result = optimize_ma_cross(
        _prices(),
        _grid(),
        train_ratio=0.5,
        validation_ratio=0.25,
        costs=_free_costs(),
    )
    sensitivity = result.sensitivity
    assert sensitivity["time_budget_status"] == "not_configured"
    assert sensitivity["time_budget_s"] is None
    assert sensitivity["memory_budget_mb"] is None
    assert sensitivity["memory_peak_mb"] is None
    assert sensitivity["memory_budget_status"] == "not_configured"


def test_memory_budget_within_limit_records_peak() -> None:
    result = optimize_ma_cross(
        _big_prices(),
        _grid(),
        train_ratio=0.5,
        validation_ratio=0.25,
        costs=_free_costs(),
        memory_budget_mb=100_000,
    )
    sensitivity = result.sensitivity
    assert sensitivity["memory_budget_status"] == "within_limit"
    assert sensitivity["memory_budget_mb"] == 100_000
    assert sensitivity["memory_peak_mb"] is not None
    assert sensitivity["memory_peak_mb"] > 0


def test_memory_budget_soft_limit_early_stops(monkeypatch: pytest.MonkeyPatch) -> None:
    # 1 MiB 预算首轮检查即超出，注入 traced 内存保证确定性
    monkeypatch.setattr(
        backtest_module.tracemalloc,
        "get_traced_memory",
        lambda: (110 * 1024 * 1024, 220 * 1024 * 1024),
    )
    result = optimize_ma_cross(
        _prices(),
        _grid(),
        train_ratio=0.5,
        validation_ratio=0.25,
        costs=_free_costs(),
        memory_budget_mb=1,
    )
    sensitivity = result.sensitivity
    assert sensitivity["memory_budget_status"] == "exceeded"
    assert sensitivity["memory_budget_mb"] == 1
    assert sensitivity["memory_peak_mb"] == 220
    assert sensitivity["trial_count"] < 4


def test_rolling_time_budget_stops_new_windows_keeps_completed(slow_clock) -> None:
    result = optimize_ma_cross_rolling(
        _prices(),
        _grid(),
        train_size=10,
        validation_size=5,
        test_size=5,
        costs=_free_costs(),
        min_trades=0,
        time_budget_s=60,
    )
    aggregate = result.aggregate
    assert aggregate["time_budget_status"] == "exceeded"
    assert aggregate["time_budget_s"] == 60
    assert 0 < aggregate["window_count"] < 5
    assert any(window["status"] == "ok" for window in result.windows)


def test_multi_time_budget_stops_after_first_symbol(slow_clock) -> None:
    daily = {"600519.SH": _prices(), "000858.SZ": _prices()}
    result = optimize_ma_cross_multi(
        daily,
        _grid(),
        train_size=10,
        validation_size=5,
        test_size=5,
        costs=_free_costs(),
        time_budget_s=60,
    )
    aggregate = result.aggregate
    assert aggregate["time_budget_status"] == "exceeded"
    assert aggregate["time_budget_s"] == 60
    assert aggregate["symbol_count"] == 2
    assert 0 < aggregate["successful_symbols"] < 2


def test_invalid_budgets_rejected() -> None:
    with pytest.raises(BacktestError, match="time_budget_s"):
        optimize_ma_cross(
            _prices(), _grid(), train_ratio=0.5, validation_ratio=0.25, time_budget_s=0
        )
    with pytest.raises(BacktestError, match="memory_budget_mb"):
        optimize_ma_cross(
            _prices(),
            _grid(),
            train_ratio=0.5,
            validation_ratio=0.25,
            memory_budget_mb=-1,
        )
