"""Tests for field-level EvidencePackage comparison."""

from src.analysis.evidence_diff import compare_evidence_packages


def _package(run_id: str, close: float) -> dict:
    return {
        "run_id": run_id,
        "created_at": f"{run_id}-time",
        "meta": {"generated_at": f"{run_id}-generated", "data_date": "2026-08-14"},
        "stock": {"code": "600519.SH", "name": "测试股票"},
        "technical": {"close": close, "trend": "上升"},
        "validation": {"run_id": run_id, "validated_at": f"{run_id}-validated", "status": "pass"},
    }


def test_evidence_diff_ignores_run_metadata_and_reports_structured_change() -> None:
    result = compare_evidence_packages(_package("run-1", 10.0), _package("run-2", 11.0))

    assert result["compatible"] is True
    assert result["changed_count"] == 1
    assert result["changes"][0]["path"] == "technical.close"


def test_evidence_diff_rejects_different_tickers() -> None:
    previous = _package("run-1", 10.0)
    current = _package("run-2", 11.0)
    current["stock"]["code"] = "000858.SZ"

    result = compare_evidence_packages(previous, current)

    assert result["compatible"] is False
    assert result["changes"] == []
