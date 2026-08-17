"""历史市场行为数据的时点过滤测试。"""

import pandas as pd

from src.data.market_behavior import normalize_moneyflow_frame


def test_moneyflow_normalization_removes_future_rows_and_deduplicates() -> None:
    frame = pd.DataFrame(
        {
            "trade_date": ["2026-08-14", "2026-08-15", "2026-08-14"],
            "net_mf_amount": [1.0, 2.0, 3.0],
        }
    )

    result = normalize_moneyflow_frame(frame, as_of="2026-08-14")

    assert result["trade_date"].tolist() == [pd.Timestamp("2026-08-14")]
    assert result["net_mf_amount"].tolist() == [3.0]
