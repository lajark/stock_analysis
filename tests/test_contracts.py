"""Tests for the versioned analysis contract layer."""

import pandas as pd

from src.analysis.contracts import (
    DatasetDescriptor,
    DataSnapshot,
    EvidencePackage,
    RunRecord,
    ValidationCheck,
    ValidationResult,
)


def test_build_analysis_package_adds_contract_metadata_without_breaking_shape(monkeypatch) -> None:
    import src.analysis.package as package_module

    monkeypatch.setattr(package_module, "calc_all_indicators", lambda frame: frame)
    monkeypatch.setattr(package_module, "summarize_indicators", lambda frame: {"trend": "上升"})
    monkeypatch.setattr(package_module, "analyze_fundamentals", lambda *args: {"score": 70})
    monkeypatch.setattr(package_module, "analyze_valuation", lambda *args: {"level": "合理"})
    monkeypatch.setattr(package_module, "analyze_risk", lambda frame: {"risk_level": {"score": 30}})
    monkeypatch.setattr(package_module, "analyze_price_levels", lambda frame: {})

    daily = pd.DataFrame({"trade_date": [pd.Timestamp("2026-08-14")]})
    result = package_module.build_analysis_package(
        stock_info={"code": "600519.SH", "name": "示例"},
        daily=daily,
        daily_basic={},
        income=pd.DataFrame(),
        balance_sheet=pd.DataFrame(),
        cashflow=pd.DataFrame(),
        fina_indicator=pd.DataFrame(),
        analysis_date="2026-08-14",
        run_id="run-package",
        dataset_quality={"daily": "stale"},
        data_warnings=["income unavailable"],
        validation={"status": "degraded", "confidence_cap": 60},
    )

    assert result["run_id"] == "run-package"
    assert result["meta"]["data_provider"] == "tushare"
    assert result["technical"] == {"trend": "上升"}
    assert result["quality"] == "partial"
    assert "income" in result["data_gaps"]
    assert "daily" in result["data_gaps"]
    assert result["data_warnings"] == ["income unavailable"]
    assert result["validation"]["confidence_cap"] == 60
    assert "scenarios" in result
    assert "invalidation_conditions" in result

    second = package_module.build_analysis_package(
        stock_info={"code": "600519.SH", "name": "示例"},
        daily=daily,
        daily_basic={},
        income=pd.DataFrame(),
        balance_sheet=pd.DataFrame(),
        cashflow=pd.DataFrame(),
        fina_indicator=pd.DataFrame(),
        analysis_date="2026-08-14",
        run_id="run-package-2",
        dataset_quality={"daily": "stale"},
        data_warnings=["income unavailable"],
        validation={"status": "degraded", "confidence_cap": 60},
        previous_package=result,
    )
    assert second["changes"]["compatible"] is True
    assert second["changes"]["changed_count"] == 0


def test_data_snapshot_serializes_metadata_without_raw_data() -> None:
    snapshot = DataSnapshot(
        run_id="run-1",
        ticker="600519.SH",
        requested_date="2026-08-14",
        effective_trade_date="2026-08-14",
        stock={"code": "600519.SH", "name": "示例"},
        datasets={
            "daily": DatasetDescriptor(
                name="daily",
                provider="tushare",
                as_of="2026-08-14",
                row_count=10,
            )
        },
    )

    serialized = snapshot.to_dict()

    assert serialized["schema_version"] == "1.0"
    assert serialized["datasets"]["daily"]["row_count"] == 10
    assert "close" not in serialized
    assert snapshot.reference()["descriptor_hash"] == snapshot.descriptor_hash


def test_evidence_package_preserves_legacy_report_shape() -> None:
    legacy = {"meta": {"data_provider": "tushare"}, "technical": {"trend": "上升"}}
    package = EvidencePackage.from_legacy(
        legacy,
        run_id="run-2",
        snapshot_ref={"effective_trade_date": "2026-08-14"},
        quality="partial",
        data_gaps=("income",),
        data_warnings=("income: primary unavailable",),
    )

    serialized = package.to_dict()

    assert serialized["meta"] == legacy["meta"]
    assert serialized["technical"] == legacy["technical"]
    assert serialized["run_id"] == "run-2"
    assert serialized["quality"] == "partial"
    assert serialized["data_gaps"] == ["income"]
    assert serialized["data_warnings"] == ["income: primary unavailable"]


def test_evidence_package_round_trip_keeps_metadata() -> None:
    original = EvidencePackage.from_legacy(
        {"risk": {"score": 20}},
        run_id="run-3",
        snapshot_ref={"descriptor_hash": "abc"},
    )

    restored = EvidencePackage.from_dict(original.to_dict())

    assert restored.to_dict() == original.to_dict()


def test_validation_result_and_run_record_are_secret_free_contracts() -> None:
    validation = ValidationResult(
        run_id="run-4",
        status="degraded",
        allow_llm=True,
        confidence_cap=60,
        checks=(ValidationCheck("daily.freshness", "warn", "数据日期较旧"),),
        warnings=("income missing",),
    )
    run = RunRecord.start(
        {"ticker": "600519.SH", "mode": "quick", "LLM_API_KEY": "secret-value"},
        run_id="run-4",
    )
    run.complete_stage(
        "validate_request",
        elapsed_ms=3,
        details={"model": "test-model", "API_TOKEN": "secret-value"},
    )
    failed_run = RunRecord.start({"ticker": "600519.SH"}, run_id="run-failed")
    failed_run.fail("RuntimeError", "request token=secret-value")
    assert failed_run.to_dict()["outcome"]["safe_message"] == "request token=<redacted>"
    run.finish()

    assert validation.to_dict()["allow_llm"] is True
    assert run.to_dict()["outcome"]["status"] == "success"
    assert "api_key" not in run.to_dict()
    assert run.to_dict()["request"]["LLM_API_KEY"] == "<redacted>"
    assert run.to_dict()["stages"]["validate_request"]["API_TOKEN"] == "<redacted>"
    assert "secret-value" not in str(run.to_dict())
