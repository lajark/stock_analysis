"""复权因子与 OHLC 合并测试。"""

import pandas as pd
import pytest

from src.data.adjustments import AdjustmentError, apply_price_adjustment
from src.data.cache import CacheManager


def _daily() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "trade_date": pd.to_datetime(["2026-01-02", "2026-01-05"]),
            "open": [10.0, 20.0],
            "high": [11.0, 21.0],
            "low": [9.0, 19.0],
            "close": [10.5, 20.5],
            "volume": [1000, 1200],
        }
    )


def test_qfq_and_hfq_use_different_deterministic_formulas() -> None:
    factors = pd.DataFrame(
        {
            "trade_date": pd.to_datetime(["2026-01-02", "2026-01-05"]),
            "adj_factor": [2.0, 4.0],
        }
    )

    qfq = apply_price_adjustment(_daily(), factors, "qfq")
    hfq = apply_price_adjustment(_daily(), factors, "hfq")

    assert qfq.loc[0, "close"] == pytest.approx(5.25)
    assert qfq.loc[1, "close"] == pytest.approx(20.5)
    assert hfq.loc[0, "close"] == pytest.approx(21.0)
    assert hfq.loc[1, "close"] == pytest.approx(82.0)
    assert "adjustment_factor" in qfq


def test_adjustment_rejects_missing_factor_instead_of_using_raw_price() -> None:
    factors = pd.DataFrame(
        {
            "trade_date": pd.to_datetime(["2026-01-02"]),
            "adj_factor": [2.0],
        }
    )

    with pytest.raises(AdjustmentError, match="缺少复权因子"):
        apply_price_adjustment(_daily(), factors, "qfq")


def test_adjustment_cache_persists_range_and_provenance(tmp_path, monkeypatch) -> None:
    class _CacheSettings:
        enabled = True

    class _Settings:
        cache = _CacheSettings()
        cache_dir = tmp_path

    monkeypatch.setattr("src.data.cache.get_config", lambda: _Settings())
    manager = CacheManager()
    manager._cache_dir = tmp_path
    manager._meta_path = tmp_path / "meta.json"
    manager._meta = {}
    factors = pd.DataFrame(
        {
            "trade_date": pd.to_datetime(["2026-08-13", "2026-08-14"]),
            "adj_factor": [2.0, 4.0],
        }
    )

    manager.save_adj_factor("600519.SH", factors, "tushare")
    loaded = manager.get_adj_factor("600519.SH", "2026-08-13", "2026-08-14")

    assert loaded is not None
    assert loaded["adj_factor"].tolist() == [2.0, 4.0]
    assert manager.get_meta("adjustments/600519.SH")["source"] == "tushare"
