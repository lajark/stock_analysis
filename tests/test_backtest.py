"""最小回测与参数优化测试。"""

import pandas as pd
import pytest

from src.analysis.backtest import (
    BacktestError,
    BacktestSpec,
    CostModel,
    optimize_ma_cross,
    optimize_ma_cross_multi,
    optimize_ma_cross_rolling,
    run_backtest,
)
from src.analysis.indicators import calc_all_indicators
from src.analysis.parameters import AnalysisParameters


def _prices() -> pd.DataFrame:
    close = [
        10, 9, 8, 9, 10, 11, 12, 13, 14, 15,
        14, 13, 12, 11, 10, 9, 8, 7, 8, 9,
        10, 11, 12, 13, 14, 13, 12, 11, 10, 9,
        8, 7, 8, 9, 10, 11, 12, 11, 10, 9,
    ]
    dates = pd.date_range("2024-01-02", periods=len(close), freq="B")
    return pd.DataFrame(
        {
            "trade_date": dates,
            "open": close,
            "high": [value + 0.5 for value in close],
            "low": [value - 0.5 for value in close],
            "close": close,
            "volume": [1000] * len(close),
        }
    )


def test_analysis_parameters_are_explicit_and_reach_indicators() -> None:
    parameters = AnalysisParameters(ma_periods=(3, 7), rsi_period=5)
    result = calc_all_indicators(_prices(), parameters)

    assert "ma_3" in result
    assert "ma_7" in result
    assert parameters.fingerprint == AnalysisParameters(
        ma_periods=(3, 7), rsi_period=5
    ).fingerprint


def test_backtest_executes_on_next_day_open_and_records_costs() -> None:
    free = CostModel(
        commission_rate=0,
        minimum_commission=0,
        stamp_duty_rate=0,
        transfer_fee_rate=0,
        slippage_bps=0,
        lot_size=1,
    )
    result = run_backtest(
        _prices(),
        spec=BacktestSpec(ma_fast=2, ma_slow=4, initial_cash=10_000, costs=free),
    )

    assert result.data_hash
    assert result.trade_count > 0
    first_signal = (
        _prices()["close"].rolling(2).mean() > _prices()["close"].rolling(4).mean()
    ).fillna(False)
    signal_index = first_signal[first_signal].index[0]
    assert result.trades[0].entry_date == _prices().iloc[signal_index + 1]["trade_date"].strftime(
        "%Y-%m-%d"
    )

    costly = run_backtest(
        _prices(),
        spec=BacktestSpec(
            ma_fast=2,
            ma_slow=4,
            initial_cash=10_000,
            costs=CostModel(lot_size=1, slippage_bps=50),
        ),
    )
    assert costly.final_equity <= result.final_equity


def test_optimizer_selects_on_train_and_reports_later_windows() -> None:
    result = optimize_ma_cross(
        _prices(),
        {"ma_fast": [2, 3], "ma_slow": [4, 6]},
        train_ratio=0.5,
        validation_ratio=0.25,
        costs=CostModel(
            commission_rate=0,
            minimum_commission=0,
            stamp_duty_rate=0,
            transfer_fee_rate=0,
            slippage_bps=0,
            lot_size=1,
        ),
    )

    assert result.selected_parameters["ma_fast"] < result.selected_parameters["ma_slow"]
    assert result.split_dates["test_start"] > result.split_dates["validation_end"]
    assert len(result.candidates) == 4
    assert sorted(
        candidate["rank"] for candidate in result.candidates if candidate["eligible"]
    ) == [1, 2, 3, 4]
    assert result.sensitivity["trial_count"] == 4
    assert result.sensitivity["elapsed_ms"] >= 0
    assert result.sensitivity["parameter_sensitivity"]["ma_fast"]
    assert result.sensitivity["trial_budget_status"] == "not_configured"


def test_optimizer_rejects_grid_that_exceeds_deterministic_trial_budget() -> None:
    with pytest.raises(BacktestError, match="max_trials"):
        optimize_ma_cross(
            _prices(),
            {"ma_fast": [2, 3], "ma_slow": [4, 6]},
            train_ratio=0.5,
            validation_ratio=0.25,
            max_trials=3,
        )

    result = optimize_ma_cross(
        _prices(),
        {"ma_fast": [2, 3], "ma_slow": [4, 6]},
        train_ratio=0.5,
        validation_ratio=0.25,
        max_trials=4,
    )
    assert result.sensitivity["trial_budget"] == 4
    assert result.sensitivity["trial_budget_status"] == "within_limit"


def test_optimizer_robust_objective_exposes_score_components_and_sensitivity() -> None:
    result = optimize_ma_cross(
        _prices(),
        {"ma_fast": [2, 3], "ma_slow": [4, 6]},
        objective="robust",
        train_ratio=0.5,
        validation_ratio=0.25,
        min_trades=0,
        max_trials=4,
    )

    assert result.objective == "robust"
    assert result.sensitivity["method_version"] == "robust-v1"
    assert result.sensitivity["candidate_count"] == 4
    assert "score_components" in result.candidates[0]
    assert result.to_dict()["sensitivity"]["eligible_count"] == 4
    assert result.to_dict()["candidates"][0]["rank"] is not None


def test_rolling_optimizer_reports_window_metrics_and_stability() -> None:
    result = optimize_ma_cross_rolling(
        _prices(),
        {"ma_fast": [2, 3], "ma_slow": [4, 6]},
        train_size=16,
        validation_size=8,
        test_size=8,
        min_trades=0,
        max_trials=4,
        costs=CostModel(
            commission_rate=0,
            minimum_commission=0,
            stamp_duty_rate=0,
            transfer_fee_rate=0,
            slippage_bps=0,
            lot_size=1,
        ),
    )

    assert len(result.windows) == 2
    assert result.aggregate["successful_windows"] == 2
    assert result.aggregate["trial_count"] == 8
    assert result.aggregate["trial_budget"] == 4
    assert result.aggregate["elapsed_ms"] >= 0
    assert all(window["elapsed_ms"] >= 0 for window in result.windows)
    assert "selection_counts" in result.parameter_stability
    assert result.to_dict()["windows"][0]["status"] in {"ok", "degraded"}


def test_multi_optimizer_uses_equal_symbol_weight_and_groups_market_state() -> None:
    result = optimize_ma_cross_multi(
        {"AAA": _prices(), "BBB": _prices()},
        {"ma_fast": [2, 3], "ma_slow": [4, 6]},
        train_size=16,
        validation_size=8,
        test_size=8,
        objective="robust",
        min_trades=0,
        max_trials=4,
        market_state_by_symbol={"AAA": "bull", "BBB": "bear"},
        costs=CostModel(
            commission_rate=0,
            minimum_commission=0,
            stamp_duty_rate=0,
            transfer_fee_rate=0,
            slippage_bps=0,
            lot_size=1,
        ),
    )

    assert result.selected_parameters is not None
    assert result.aggregate["successful_symbols"] == 2
    assert set(result.groups) == {"bear", "bull"}
    assert result.parameter_stability["weighted_selection_frequency"]
    assert result.to_dict()["per_symbol"]["AAA"]["status"] == "ok"
    assert result.aggregate["trial_count"] == 16
    assert result.aggregate["trial_budget"] == 4
    assert result.aggregate["elapsed_ms"] >= 0


def test_optimizer_rejects_parameter_sets_below_minimum_trade_count() -> None:
    with pytest.raises(BacktestError, match="最小交易次数"):
        optimize_ma_cross(
            _prices(),
            {"ma_fast": [2], "ma_slow": [4]},
            train_ratio=0.5,
            validation_ratio=0.25,
            min_trades=99,
        )


def test_backtest_respects_suspension_and_limit_fields() -> None:
    frame = _prices()
    frame["is_suspended"] = False
    frame["up_limit"] = frame["open"] * 100
    frame["down_limit"] = frame["open"] * 0.01
    frame.loc[5, "is_suspended"] = True
    frame.loc[6, "up_limit"] = frame.loc[6, "open"]
    result = run_backtest(
        frame,
        spec=BacktestSpec(
            ma_fast=2,
            ma_slow=4,
            initial_cash=10_000,
            costs=CostModel(
                commission_rate=0,
                minimum_commission=0,
                stamp_duty_rate=0,
                transfer_fee_rate=0,
                slippage_bps=0,
                lot_size=1,
            ),
        ),
    )

    assert not any("未提供" in warning for warning in result.warnings)
    assert result.adjustment == "none"
