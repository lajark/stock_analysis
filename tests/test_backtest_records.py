"""回测运行记录持久化测试。"""

import pytest

from src.analysis.backtest import (
    BacktestSpec,
    CostModel,
    optimize_ma_cross_multi,
    optimize_ma_cross_rolling,
    run_backtest,
)
from src.app.backtest_records import BacktestRunRecord, persist_backtest_run
from src.app.run_records import RunRecordStore


def test_persist_backtest_run_stores_audit_metadata_without_curve(sample_ohlc, tmp_path) -> None:
    result = run_backtest(
        sample_ohlc,
        spec=BacktestSpec(ma_fast=3, ma_slow=8, initial_cash=10_000),
    )
    store = RunRecordStore(tmp_path / "backtest_records.jsonl")

    record = persist_backtest_run(
        {
            "ticker": "600519.SH",
            "start_date": "2026-01-01",
            "end_date": "2026-03-25",
        },
        result,
        store=store,
    )

    restored = store.list()
    assert restored[0]["run_id"] == record.run_id
    assert restored[0]["request"]["audit_schema_version"] == "backtest-run-v1"
    assert restored[0]["request"]["application_version"] == "1.2.1"
    assert restored[0]["request"]["determinism"] == "deterministic_no_random_state"
    assert restored[0]["request"]["random_seed"] is None
    details = restored[0]["stages"]["backtest"]
    assert details["data_hash"] == result.data_hash
    assert details["audit_schema_version"] == "backtest-run-v1"
    assert details["application_version"] == "1.2.1"
    assert details["determinism"] == "deterministic_no_random_state"
    contract = details["backtest_record"]
    assert contract["run_id"] == record.run_id
    assert contract["data_hashes"] == [result.data_hash]
    assert contract["adjustment_versions"] == ["tushare-factor-v1"]
    assert "equity_curve" not in contract
    restored_contract = BacktestRunRecord.from_dict(contract)
    assert restored_contract.result_digest == contract["result_digest"]


def test_backtest_record_rejects_unknown_schema() -> None:
    with pytest.raises(ValueError, match="不支持的回测审计记录版本"):
        BacktestRunRecord.from_dict({"schema_version": "unknown"})


def test_persist_rolling_optimization_keeps_window_audit(tmp_path, sample_ohlc) -> None:
    result = optimize_ma_cross_rolling(
        sample_ohlc,
        {"ma_fast": [2], "ma_slow": [4]},
        train_size=8,
        validation_size=4,
        test_size=4,
        min_trades=0,
        costs=CostModel(
            commission_rate=0,
            minimum_commission=0,
            stamp_duty_rate=0,
            transfer_fee_rate=0,
            slippage_bps=0,
            lot_size=1,
        ),
    )
    store = RunRecordStore(tmp_path / "rolling_records.jsonl")

    persist_backtest_run({"ticker": "600519.SH"}, result, store=store)

    details = store.list()[0]["stages"]["optimize"]
    assert details["aggregate"]["window_count"] >= 1
    assert "parameter_stability" in details


def test_persist_multi_optimization_keeps_symbol_groups(tmp_path, sample_ohlc) -> None:
    result = optimize_ma_cross_multi(
        {"AAA": sample_ohlc, "BBB": sample_ohlc},
        {"ma_fast": [2], "ma_slow": [4]},
        train_size=8,
        validation_size=4,
        test_size=4,
        min_trades=0,
        market_state_by_symbol={"AAA": "bull", "BBB": "bear"},
        min_successful_symbols=2,
        costs=CostModel(
            commission_rate=0,
            minimum_commission=0,
            stamp_duty_rate=0,
            transfer_fee_rate=0,
            slippage_bps=0,
            lot_size=1,
        ),
    )
    store = RunRecordStore(tmp_path / "multi_records.jsonl")

    persist_backtest_run({"tickers": ["AAA", "BBB"]}, result, store=store)

    details = store.list()[0]["stages"]["optimize"]
    assert set(details["groups"]) == {"bear", "bull"}
