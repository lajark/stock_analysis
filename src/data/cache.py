"""数据缓存层 — DuckDB + Parquet 实现。

缓存策略：
- 日线行情：按股票代码 + 年份分 Parquet 文件，交易日 15:30 后不重复请求
- 财务数据：按股票代码 + 报告期分 Parquet 文件
- DuckDB 用于多表 SQL 查询（join、filter、aggregate）
"""

import json
import threading
from datetime import datetime
from pathlib import Path

import pandas as pd
from loguru import logger

from src.config import get_config
from src.data.adjustments import normalize_adjustment_factors
from src.data.financials import filter_financial_as_of, normalize_financial_frame
from src.data.market_behavior import normalize_moneyflow_frame

# Batch analysis may save the same cache file from several worker threads.
# The lock serializes the read-modify-write; atomic tmp+replace keeps every
# reader (and crash of the writer) seeing one complete file.
_CACHE_LOCK = threading.RLock()


def _try_read_parquet(path: Path) -> pd.DataFrame | None:
    """Return the frame if readable; a corrupt file is treated as a cache miss."""
    try:
        return pd.read_parquet(path)
    except Exception as exc:
        logger.warning("Corrupt cache file ignored ({}): {}", type(exc).__name__, path)
        return None


def _atomic_parquet_write(frame: pd.DataFrame, path: Path) -> None:
    """Write a Parquet frame atomically (tmp file + replace)."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    frame.to_parquet(tmp, index=False)
    tmp.replace(path)


def _monotone_date(existing: str, incoming: str, *, min_: bool = False) -> str:
    """Merge ISO date markers: newest by default, oldest when ``min_=True``."""
    if not existing:
        return incoming
    return existing if (min(existing, incoming) == existing) == min_ else incoming


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
                with open(self._meta_path, encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                return {}
        return {}

    def _save_meta(self) -> None:
        """保存缓存元数据（原子写）。"""
        tmp = self._meta_path.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._meta, f, ensure_ascii=False, indent=2)
        tmp.replace(self._meta_path)

    def _reload_meta(self) -> None:
        """Re-read the latest meta snapshot; call inside ``_CACHE_LOCK``."""
        self._meta = self._load_meta()

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
    ) -> pd.DataFrame | None:
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
            df = _try_read_parquet(path)
            if df is None:
                continue  # corrupt file -> cache miss; the gateway will refetch
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

        with _CACHE_LOCK:
            # Refresh the snapshot inside the lock so a concurrent writer's
            # updates to other cache keys are not clobbered on save.
            self._reload_meta()

            df["trade_date_str"] = df["trade_date"].astype(str)
            df["year"] = df["trade_date_str"].str[:4]

            for year, group in df.groupby("year"):
                path = self._daily_path(code, year)
                # 合并已有数据；损坏的既有缓存按 cache miss 全量替换
                existing = _try_read_parquet(path) if path.exists() else None
                if existing is not None:
                    existing["trade_date_str"] = existing["trade_date"].astype(str)
                    combined = pd.concat([existing, group], ignore_index=True)
                    combined = combined.drop_duplicates(subset=["trade_date_str"], keep="last")
                    combined = combined.drop(columns=["trade_date_str", "year"], errors="ignore")
                else:
                    combined = group.drop(columns=["trade_date_str", "year"], errors="ignore")
                _atomic_parquet_write(combined, path)

            # 更新元数据
            cache_key = self._cache_key("daily", code)
            latest_date = df["trade_date"].max()
            new_latest = (
                latest_date.strftime("%Y-%m-%d")
                if hasattr(latest_date, "strftime")
                else str(latest_date)
            )
            # Monotone merge: when several workers write the same code in one
            # batch, keep the newest marker instead of last-writer-wins.
            previous = self._meta.get(cache_key, {})
            self._meta[cache_key] = {
                "updated_at": datetime.now().isoformat(),
                "latest_date": max(str(previous.get("latest_date", "")), new_latest),
            }
            self._save_meta()

    # ------------------------------------------------------------------
    # 复权因子缓存
    # ------------------------------------------------------------------
    def _adjustment_path(self, code: str) -> Path:
        """复权因子缓存路径；单独存储以便独立控制新鲜度。"""
        subdir = self._cache_dir / "adjustments"
        subdir.mkdir(parents=True, exist_ok=True)
        return subdir / f"{code}_adj_factor.parquet"

    def get_adj_factor(
        self, code: str, start_date: str, end_date: str
    ) -> pd.DataFrame | None:
        """读取请求区间内的复权因子；新鲜度由网关依据元数据判断。"""
        if not get_config().cache.enabled:
            return None
        path = self._adjustment_path(code)
        if not path.exists():
            return None
        frame = normalize_adjustment_factors(pd.read_parquet(path))
        if frame.empty:
            return None
        start = pd.to_datetime(start_date, errors="coerce")
        end = pd.to_datetime(end_date, errors="coerce")
        if pd.notna(start):
            frame = frame[frame["trade_date"] >= start]
        if pd.notna(end):
            frame = frame[frame["trade_date"] <= end]
        return frame.reset_index(drop=True) if not frame.empty else None

    def save_adj_factor(
        self, code: str, frame: pd.DataFrame, source: str = ""
    ) -> None:
        """追加保存复权因子，并记录覆盖范围、来源和更新时间。"""
        normalized = normalize_adjustment_factors(frame)
        if normalized.empty:
            return
        with _CACHE_LOCK:
            self._reload_meta()
            path = self._adjustment_path(code)
            if path.exists():
                existing = pd.read_parquet(path)
                normalized = normalize_adjustment_factors(pd.concat([existing, normalized]))
            _atomic_parquet_write(normalized, path)
            cache_key = self._cache_key("adjustments", code)
            previous = self._meta.get(cache_key, {})
            self._meta[cache_key] = {
                "updated_at": datetime.now().isoformat(),
                "earliest_date": _monotone_date(
                    previous.get("earliest_date", ""),
                    normalized["trade_date"].min().strftime("%Y-%m-%d"),
                    min_=True,
                ),
                "latest_date": _monotone_date(
                    previous.get("latest_date", ""),
                    normalized["trade_date"].max().strftime("%Y-%m-%d"),
                ),
                "source": source,
            }
            self._save_meta()

    # ------------------------------------------------------------------
    # 市场行为数据缓存
    # ------------------------------------------------------------------
    def _moneyflow_path(self, code: str) -> Path:
        subdir = self._cache_dir / "market_behavior"
        subdir.mkdir(parents=True, exist_ok=True)
        return subdir / f"{code}_moneyflow.parquet"

    def get_moneyflow(
        self, code: str, start_date: str, end_date: str
    ) -> pd.DataFrame | None:
        """从缓存读取历史资金流，并限制在请求日期范围内。"""
        if not get_config().cache.enabled:
            return None
        path = self._moneyflow_path(code)
        if not path.exists():
            return None
        frame = normalize_moneyflow_frame(pd.read_parquet(path))
        if frame.empty:
            return None
        start = pd.to_datetime(start_date, errors="coerce")
        end = pd.to_datetime(end_date, errors="coerce")
        if pd.notna(start):
            frame = frame[frame["trade_date"] >= start]
        if pd.notna(end):
            frame = frame[frame["trade_date"] <= end]
        return frame.reset_index(drop=True) if not frame.empty else None

    def save_moneyflow(self, code: str, frame: pd.DataFrame) -> None:
        """追加保存资金流历史行，保留日期版本以便审计。"""
        normalized = normalize_moneyflow_frame(frame)
        if normalized.empty:
            return
        with _CACHE_LOCK:
            self._reload_meta()
            path = self._moneyflow_path(code)
            if path.exists():
                existing = pd.read_parquet(path)
                normalized = normalize_moneyflow_frame(pd.concat([existing, normalized]))
            _atomic_parquet_write(normalized, path)
            cache_key = self._cache_key("market_behavior", code, "moneyflow")
            previous = self._meta.get(cache_key, {})
            self._meta[cache_key] = {
                "updated_at": datetime.now().isoformat(),
                "latest_date": _monotone_date(
                    previous.get("latest_date", ""),
                    normalized["trade_date"].max().strftime("%Y-%m-%d"),
                ),
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
        self, code: str, report_type: str, as_of: str | None = None
    ) -> pd.DataFrame | None:
        """从缓存读取财务数据，并按公告/报告期执行 PIT 过滤。"""
        if not get_config().cache.enabled:
            return None

        path = self._financial_path(code, report_type)
        if not path.exists():
            return None
        return normalize_financial_frame(pd.read_parquet(path), as_of=as_of)

    def save_financials(self, code: str, report_type: str, df: pd.DataFrame) -> None:
        """保存财务数据到缓存。"""
        if df.empty:
            return

        with _CACHE_LOCK:
            self._reload_meta()
            path = self._financial_path(code, report_type)
            if path.exists():
                existing = pd.read_parquet(path)
                combined = pd.concat([existing, df], ignore_index=True)
            else:
                combined = df
            # Keep all revisions in storage. ``get_financials`` applies the
            # point-in-time filter and deterministic selection for each request;
            # collapsing here would make a later as-of query impossible.
            combined = filter_financial_as_of(combined)
            _atomic_parquet_write(combined, path)

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

    def get_meta(self, cache_key: str) -> dict | None:
        """获取缓存元数据。"""
        return self._meta.get(cache_key)
