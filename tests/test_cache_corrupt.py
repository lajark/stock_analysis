"""缓存损坏失败恢复测试 — 本地磁盘，无网络。

覆盖：meta.json 损坏优雅降级（既有契约回归）、损坏 parquet 视为 cache miss、
save_daily 对损坏既有缓存全量覆盖、meta 原子写后保持有效 JSON。
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from src.data.cache import CacheManager


@pytest.fixture
def manager(tmp_path: Path, monkeypatch) -> CacheManager:
    cache_dir = tmp_path / "cache"
    monkeypatch.setattr(
        "src.data.cache.get_config",
        lambda: SimpleNamespace(cache_dir=cache_dir, cache=SimpleNamespace(enabled=True)),
    )
    CacheManager()  # create the directory (and initialize cache_dir)
    return CacheManager()


def _daily_frame(dates: list[str]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "trade_date": pd.to_datetime(dates),
            "open": [10.0] * len(dates),
            "high": [11.0] * len(dates),
            "low": [9.0] * len(dates),
            "close": [10.5] * len(dates),
            "volume": [100.0] * len(dates),
        }
    )


def test_corrupt_meta_json_degrades_to_empty(tmp_path, monkeypatch) -> None:
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True)
    (cache_dir / "meta.json").write_text("{ this is not json", encoding="utf-8")
    monkeypatch.setattr(
        "src.data.cache.get_config",
        lambda: SimpleNamespace(cache_dir=cache_dir, cache=SimpleNamespace(enabled=True)),
    )
    manager = CacheManager()
    assert manager._meta == {}


def test_corrupt_daily_parquet_is_a_cache_miss(manager, tmp_path) -> None:
    path = tmp_path / "cache" / "daily" / "600519.SH_2026.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"not a parquet file")
    assert manager.get_daily("600519.SH", "2026-01-01", "2026-01-31") is None


def test_save_daily_overwrites_corrupt_existing_cache(manager, tmp_path) -> None:
    path = tmp_path / "cache" / "daily" / "600519.SH_2026.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"not a parquet file")
    manager.save_daily("600519.SH", _daily_frame(["2026-01-05", "2026-01-06"]))
    frame = pd.read_parquet(path)
    assert len(frame) == 2
    assert frame["trade_date"].nunique() == 2
    meta = json.loads((tmp_path / "cache" / "meta.json").read_text(encoding="utf-8"))
    assert meta["daily/600519.SH"]["latest_date"] == "2026-01-06"


def test_save_meta_after_corruption_writes_valid_json(manager, tmp_path) -> None:
    meta_path = tmp_path / "cache" / "meta.json"
    meta_path.write_text("{ broken", encoding="utf-8")
    manager.save_daily("600519.SH", _daily_frame(["2026-01-05"]))
    loaded = json.loads(meta_path.read_text(encoding="utf-8"))
    assert loaded["daily/600519.SH"]["latest_date"] == "2026-01-05"
