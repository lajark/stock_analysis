"""财务数据 point-in-time 过滤与修订选择测试。"""

import pandas as pd

from src.data.financials import normalize_financial_frame


def test_pit_filter_uses_announcement_date_and_latest_revision() -> None:
    frame = pd.DataFrame(
        {
            "end_date": ["2024-12-31", "2024-12-31", "2025-03-31"],
            "ann_date": ["2025-03-01", "2025-04-01", "2025-05-01"],
            "update_flag": ["0", "1", "0"],
            "report_type": ["1", "1", "1"],
            "revenue": [100.0, 110.0, 120.0],
        }
    )

    before_revision = normalize_financial_frame(frame, as_of="2025-03-15")
    assert before_revision["revenue"].tolist() == [100.0]

    after_revision = normalize_financial_frame(frame, as_of="2025-04-15")
    assert after_revision["revenue"].tolist() == [110.0]
    assert after_revision["ann_date"].iloc[0] == pd.Timestamp("2025-04-01")


def test_pit_filter_rejects_future_reporting_period() -> None:
    frame = pd.DataFrame(
        {
            "end_date": ["2025-12-31"],
            "ann_date": ["2026-03-01"],
            "revenue": [100.0],
        }
    )
    assert normalize_financial_frame(frame, as_of="2025-12-31").empty


def test_normalization_prefers_update_flag_when_announcement_ties() -> None:
    frame = pd.DataFrame(
        {
            "end_date": ["2025-06-30", "2025-06-30"],
            "ann_date": ["2025-08-28", "2025-08-28"],
            "update_flag": ["0", "1"],
            "roe": [14.24, 3.57],
        }
    )

    normalized = normalize_financial_frame(frame, as_of="2025-12-31")

    assert normalized["roe"].tolist() == [3.57]
    assert normalized["update_flag"].tolist() == ["1"]


def test_normalization_keeps_each_security_for_multi_security_exports() -> None:
    frame = pd.DataFrame(
        {
            "ts_code": ["000001.SZ", "000002.SZ"],
            "end_date": ["2025-06-30", "2025-06-30"],
            "ann_date": ["2025-08-20", "2025-08-21"],
            "revenue": [100.0, 200.0],
        }
    )

    normalized = normalize_financial_frame(frame, as_of="2025-12-31")

    assert normalized["ts_code"].tolist() == ["000001.SZ", "000002.SZ"]
    assert normalized["revenue"].tolist() == [100.0, 200.0]
