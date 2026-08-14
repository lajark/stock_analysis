"""Integration checks for the service validation gate and run records."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pandas as pd
import pytest

from src.app.run_records import RunRecordStore
from src.app.service import AnalysisRequest, analyze_stock
from src.data.gateway import StockDataBundle
from src.errors import DataValidationError


def _daily(*, invalid: bool = False) -> pd.DataFrame:
    high = [11.0, float("nan")] if invalid else [11.0, 11.5]
    return pd.DataFrame(
        {
            "trade_date": pd.to_datetime(["2026-08-13", "2026-08-14"]),
            "open": [10.0, 10.5],
            "high": high,
            "low": [9.5, 10.0],
            "close": [10.5, 11.0],
            "volume": [100.0, 120.0],
        }
    )


def _bundle(*, invalid: bool = False) -> StockDataBundle:
    return StockDataBundle(
        stock_info={"code": "600519.SH", "name": "测试股票"},
        daily=_daily(invalid=invalid),
        daily_basic={},
        income=pd.DataFrame(),
        balance_sheet=pd.DataFrame(),
        cashflow=pd.DataFrame(),
        fina_indicator=pd.DataFrame(),
        providers={"stock_info": "tushare", "daily": "tushare"},
        quality={"stock_info": "ok", "daily": "ok"},
    )


def _patch_common(monkeypatch, tmp_path, bundle: StockDataBundle) -> RunRecordStore:
    store = RunRecordStore(tmp_path / "run_records.jsonl")
    monkeypatch.setattr("src.app.service.RunRecordStore", lambda: store)
    monkeypatch.setattr(
        "src.app.service.validate_request",
        lambda request: AnalysisRequest(
            ticker="600519.SH",
            mode=request.mode,
            date="2026-08-14",
            use_llm=request.use_llm,
            chart=False,
        ),
    )
    monkeypatch.setattr(
        "src.app.service.get_config",
        lambda: SimpleNamespace(
            json_dir=tmp_path / "json",
            output_dir=tmp_path / "output",
            llm_model="test-model",
        ),
    )

    class FakeGateway:
        def fetch(self, *args, **kwargs):
            return bundle

    monkeypatch.setattr("src.data.gateway.DataGateway", FakeGateway)
    return store


def test_service_blocks_llm_when_validation_fails(monkeypatch, tmp_path) -> None:
    store = _patch_common(monkeypatch, tmp_path, _bundle(invalid=True))
    called = False

    def fail_if_called(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("LLM must not be called after a validation block")

    monkeypatch.setattr("src.app.service._create_llm_report", fail_if_called)

    with pytest.raises(DataValidationError, match="已阻止报告生成"):
        analyze_stock(AnalysisRequest(ticker="600519", use_llm=True))

    assert called is False
    records = store.list()
    assert records[0]["outcome"]["status"] == "failed"
    assert records[0]["stages"]["validate_evidence"]["allow_llm"] is False


def test_service_persists_successful_no_llm_run(monkeypatch, tmp_path) -> None:
    store = _patch_common(monkeypatch, tmp_path, _bundle())
    monkeypatch.setattr(
        "src.analysis.package.build_analysis_package",
        lambda **kwargs: {
            "schema_version": "1.0",
            "quality": "degraded",
            "data_gaps": ["income"],
            "technical": {},
        },
    )

    result = analyze_stock(AnalysisRequest(ticker="600519", use_llm=False))

    assert result.output_kind == "json"
    assert result.output_path.exists()
    assert json.loads(result.output_path.read_text(encoding="utf-8"))["quality"] == "degraded"
    records = store.list()
    assert records[0]["outcome"]["status"] == "success"
    assert records[0]["stages"]["generate_report"]["llm_used"] is False


def test_service_records_context_router_metadata_for_llm(monkeypatch, tmp_path) -> None:
    store = _patch_common(monkeypatch, tmp_path, _bundle())
    monkeypatch.setattr(
        "src.analysis.package.build_analysis_package",
        lambda **kwargs: {
            "schema_version": "1.0",
            "run_id": "run-package",
            "stock": {"code": "600519.SH"},
            "quality": "ok",
            "data_gaps": [],
            "technical": {"trend": "上升"},
            "risk": {"risk_level": {"label": "中等风险"}},
            "valuation": {"percentiles": {"level": "中等"}},
            "price_levels": {},
        },
    )
    report_path = tmp_path / "report.md"
    captured: dict = {}

    def fake_report(package, mode, *, context):
        captured["context"] = context
        report_path.write_text("# report", encoding="utf-8")
        return report_path, {"model": "test-model", "total_tokens": 3}

    monkeypatch.setattr("src.app.service._create_llm_report", fake_report)

    result = analyze_stock(AnalysisRequest(ticker="600519", mode="trade", use_llm=True))

    assert result.output_kind == "report"
    record = store.list()[0]
    stage = record["stages"]["generate_report"]
    assert stage["context_router_version"] == "context-router-v1"
    expected_ids = [fragment["id"] for fragment in captured["context"]["fragments"]]
    assert stage["context_fragment_ids"] == expected_ids
    assert stage["context_hash"] == captured["context"]["content_hash"]
    assert stage["context_chars"] == captured["context"]["char_count"]
