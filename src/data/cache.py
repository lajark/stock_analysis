"""数据缓存层 — DuckDB + Parquet 实现。

缓存策略：
- 日线行情：按股票代码 + 年份分 Parquet 文件，交易日 15:30 后不重复请求
- 财务数据：按股票代码 + 报告期分 Parquet 文件
- DuckDB 用于多表 SQL 查询（join、filter、aggregate）
"""

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd

from src.config import get_config


class CacheManager:
    """数据缓存管理器。

    支持：
    - Parquet 文件存储（按股票代码和日期分区）
    - 元数据追踪（缓存时间、数据源、参数）
    - 基于交易日历的缓存有效性判断
    """

    def __init__(self):
        config = get_config()
        self._cache_dir = config.cache_dir
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._meta_path = self._cache_dir / "meta.json"
        self._meta = self._load_meta()

    # ------------------------------------------------------------------
    # 元数据管理
    # ------------------------------------------------------------------
    def _load_meta(self) -> dict:
        """加载缓存元数据。"""
        if self._meta_path.exists():
            try:
                with open(self._meta_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                return {}
        return {}

    def _save_meta(self) -> None:
        """保存缓存元数据。"""
        with open(self._meta_path, "w", encoding="utf-8") as f:
            json.dump(self._meta, f, ensure_ascii=False, indent=2)

    def _cache_key(self, category: str, code: str, *args: str) -> str:
        """生成缓存键。"""
        parts = [category, code] + list(args)
        return "/".join(parts)

    # ------------------------------------------------------------------
    # 日线行情缓存
    # ------------------------------------------------------------------
    def _daily_path(self, code: str, year: str) -> Path:
        """日线行情 Parquet 文件路径。"""
        subdir = self._cache_dir / "daily"
        subdir.mkdir(parents=True, exist_ok=True)
        return subdir / f"{code}_{year}.parquet"

    def get_daily(
        self, code: str, start_date: str, end_date: str
    ) -> Optional[pd.DataFrame]:
        """从缓存读取日线行情。"""
        if not get_config().cache.enabled:
            return None

        start_year = start_date[:4]
        end_year = end_date[:4]

        frames = []
        for year in range(int(start_year), int(end_year) + 1):
            path = self._daily_path(code, str(year))
            if not path.exists():
                continue
            df = pd.read_parquet(path)
            mask = (df["trade_date"] >= start_date) & (df["trade_date"] <= end_date)
            frames.append(df[mask])

        if not frames:
            return None
        result = pd.concat(frames, ignore_index=True)
        return result.sort_values("trade_date").reset_index(drop=True)

    def save_daily(self, code: str, df: pd.DataFrame) -> None:
        """保存日线行情到缓存。"""
        if df.empty:
            return

        df["trade_date_str"] = df["trade_date"].astype(str)
        df["year"] = df["trade_date_str"].str[:4]

        for year, group in df.groupby("year"):
            path = self._daily_path(code, year)
            # 合并已有数据
            if path.exists():
                existing = pd.read_parquet(path)
                existing["trade_date_str"] = existing["trade_date"].astype(str)
                combined = pd.concat([existing, group], ignore_index=True)
                combined = combined.drop_duplicates(subset=["trade_date_str"], keep="last")
                combined = combined.drop(columns=["trade_date_str", "year"], errors="ignore")
            else:
                combined = group.drop(columns=["trade_date_str", "year"], errors="ignore")
            combined.to_parquet(path, index=False)

        # 更新元数据
        cache_key = self._cache_key("daily", code)
        self._meta[cache_key] = {
            "updated_at": datetime.now().isoformat(),
            "latest_date": df["trade_date"].max().strftime("%Y-%m-%d") if hasattr(df["trade_date"].max(), "strftime") else str(df["trade_date"].max()),
        }
        self._save_meta()

    # ------------------------------------------------------------------
    # 财务数据缓存
    # ------------------------------------------------------------------
    def _financial_path(self, code: str, report_type: str) -> Path:
        """财务数据 Parquet 文件路径。"""
        subdir = self._cache_dir / "financials"
        subdir.mkdir(parents=True, exist_ok=True)
        return subdir / f"{code}_{report_type}.parquet"

    def get_financials(
        self, code: str, report_type: str
    ) -> Optional[pd.DataFrame]:
        """从缓存读取财务数据。"""
        if not get_config().cache.enabled:
            return None

        path = self._financial_path(code, report_type)
        if not path.exists():
            return None
        return pd.read_parquet(path)

    def save_financials(self, code: str, report_type: str, df: pd.DataFrame) -> None:
        """保存财务数据到缓存。"""
        if df.empty:
            return

        path = self._financial_path(code, report_type)
        if path.exists():
            existing = pd.read_parquet(path)
            if "end_date" in df.columns:
                existing["end_date_str"] = existing["end_date"].astype(str)
                df["end_date_str"] = df["end_date"].astype(str)
                combined = pd.concat([existing, df], ignore_index=True)
                combined = combined.drop_duplicates(subset=["end_date_str"], keep="last")
                combined = combined.drop(columns=["end_date_str"], errors="ignore")
            else:
                combined = pd.concat([existing, df], ignore_index=True)
        else:
            combined = df
        combined.to_parquet(path, index=False)

        # 更新元数据
        cache_key = self._cache_key("financials", code, report_type)
        self._meta[cache_key] = {
            "updated_at": datetime.now().isoformat(),
        }
        self._save_meta()

    # ------------------------------------------------------------------
    # 缓存有效性
    # ------------------------------------------------------------------
    def is_daily_fresh(self, code: str, trade_date: str) -> bool:
        """判断日线数据是否已缓存且为最新。

        注意：此处不校验交易日，由调用方结合交易日历判断。
        """
        cache_key = self._cache_key("daily", code)
        if cache_key not in self._meta:
            return False

        meta = self._meta[cache_key]
        cached_date = meta.get("latest_date", "")
        return cached_date >= trade_date

    def get_meta(self, cache_key: str) -> Optional[dict]:
        """获取缓存元数据。"""
        return self._meta.get(cache_key)