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


def test_evidence_diff_ignores_stale_changes_key_and_list_tuple_noise() -> None:
    """Regression: a stale ``changes`` key in the JSON-loaded previous package
    must not be recorded as a whole-structure diff (recursive nesting), and
    list→tuple differences from JSON round-trips must be suppressed."""
    previous = _package("run-1", 10.0)
    previous["changes"] = {
        "compatible": True,
        "changed_count": 99,
        "changes": [{"path": "old", "before": "x", "after": "y"}],
    }
    previous["decision"] = {
        "status": "conditional_positive",
        "conditions": ["cond A"],
        "unresolved_conflicts": ["old conflict"],
        "supporting_evidence": [],
        "opposing_evidence": [],
        "invalidation_conditions": [],
    }
    current = _package("run-2", 10.0)
    current["decision"] = {
        "status": "conditional_positive",
        "conditions": ("cond A",),  # tuple vs list in previous
        "unresolved_conflicts": ["new conflict"],
        "supporting_evidence": [],
        "opposing_evidence": [],
        "invalidation_conditions": [],
    }

    result = compare_evidence_packages(previous, current)

    # The stale "changes" key in previous must not be recorded.
    changes_paths = [c["path"] for c in result["changes"]]
    assert "changes" not in changes_paths, "stale changes key leaked into diff"
    # list-vs-tuple for unchanged "conditions" must be suppressed.
    assert "decision.conditions" not in changes_paths, "list-vs-tuple noise leaked"
    # Only the real change (unresolved_conflicts: old vs new) should remain.
    assert "decision.unresolved_conflicts" in changes_paths
    assert result["changed_count"] == 1
