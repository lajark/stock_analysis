"""财务多源对账测试。"""

import pandas as pd

from src.data.reconciliation import reconcile_financial_frames, reconcile_financial_sets


def test_reconciliation_applies_pit_before_comparing_revisions() -> None:
    left = pd.DataFrame(
        {
            "end_date": ["2024-12-31", "2024-12-31", "2025-12-31"],
            "ann_date": ["2025-03-01", "2025-04-01", "2026-03-01"],
            "update_flag": ["0", "1", "0"],
            "revenue": [100.0, 110.0, 120.0],
        }
    )
    right = pd.DataFrame(
        {
            "end_date": ["2024-12-31"],
            "ann_date": ["2025-04-01"],
            "revenue": [110.0],
        }
    )

    result = reconcile_financial_frames(
        left,
        right,
        dataset="income",
        as_of="2025-05-31",
    )

    assert result.status == "pass"
    assert result.left_revision_rows == 1
    assert result.left_future_rows == 1
    assert result.matched_periods == 1


def test_reconciliation_reports_numeric_mismatch_and_missing_periods() -> None:
    left = pd.DataFrame(
        {
            "end_date": ["2024-12-31", "2025-12-31"],
            "revenue": [100.0, 200.0],
        }
    )
    right = pd.DataFrame(
        {
            "end_date": ["2024-12-31", "2026-12-31"],
            "revenue": [101.0, 300.0],
        }
    )

    result = reconcile_financial_frames(
        left,
        right,
        dataset="income",
        as_of="2026-12-31",
    )

    assert result.status == "partial"
    assert result.left_only_periods == ("2025-12-31",)
    assert result.right_only_periods == ("2026-12-31",)
    assert result.mismatch_count == 1
    assert result.mismatches[0]["field"] == "revenue"


def test_reconciliation_aggregate_marks_missing_dataset_insufficient() -> None:
    report = reconcile_financial_sets(
        {"income": pd.DataFrame()},
        {"income": pd.DataFrame()},
        as_of="2025-12-31",
        datasets=("income",),
    )

    assert report["status"] == "insufficient"
    assert report["datasets"]["income"]["status"] == "insufficient"


def test_reconciliation_matches_multi_security_exports_by_security_and_period() -> None:
    left = pd.DataFrame(
        {
            "ts_code": ["000001.SZ", "000002.SZ"],
            "end_date": ["2025-06-30", "2025-06-30"],
            "revenue": [100.0, 200.0],
        }
    )
    right = pd.DataFrame(
        {
            "ts_code": ["000001.SZ", "000002.SZ"],
            "end_date": ["2025-06-30", "2025-06-30"],
            "revenue": [100.0, 201.0],
        }
    )

    result = reconcile_financial_frames(
        left,
        right,
        dataset="income",
        as_of="2025-12-31",
    )

    assert result.matched_periods == 2
    assert result.mismatch_count == 1
    assert result.mismatches[0]["period"] == "000002.SZ|2025-06-30"
