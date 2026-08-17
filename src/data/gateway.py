"""统一数据访问网关。

网关负责缓存、主数据源和显式降级，不负责指标计算或报告生成。财务数据
没有可靠的 AkShare 等价接口时，保留为空并返回质量告警，不用猜测值填充。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, TypeAlias, cast

import pandas as pd  # type: ignore[import-untyped]

from src.config import get_config
from src.data.adjustments import AdjustmentMode, apply_price_adjustment
from src.data.cache import CacheManager
from src.data.financials import filter_financial_as_of, normalize_financial_frame
from src.data.market_behavior import normalize_moneyflow_frame
from src.data.providers.base import DataProvider, DataProviderError

DatasetValue: TypeAlias = dict[str, Any] | pd.DataFrame
_UNSET = object()


@dataclass
class StockDataBundle:
    """One stock's normalized data and dataset-level provenance."""

    stock_info: dict[str, Any]
    daily: pd.DataFrame
    daily_basic: dict[str, Any]
    income: pd.DataFrame
    balance_sheet: pd.DataFrame
    cashflow: pd.DataFrame
    fina_indicator: pd.DataFrame
    moneyflow: pd.DataFrame = field(default_factory=pd.DataFrame)
    adjustment: AdjustmentMode = "none"
    providers: dict[str, str] = field(default_factory=dict)
    quality: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @property
    def provider_label(self) -> str:
        """Return a compact label for legacy report metadata."""
        names = {
            name
            for name in self.providers.values()
            if name not in {"", "cache", "unavailable"}
        }
        if not names:
            return "cache"
        if len(names) == 1:
            return next(iter(names))
        return "mixed"

    @property
    def data_gaps(self) -> tuple[str, ...]:
        """Return datasets that are empty or degraded."""
        return tuple(
            name
            for name, status in self.quality.items()
            if status in {"partial", "stale", "invalid"}
        )


class DataGateway:
    """Fetch normalized datasets through cache → primary → fallback."""

    _FINANCIAL_DATASETS = {
        "income": "income",
        "balance_sheet": "balance_sheet",
        "cashflow": "cashflow",
        "fina_indicator": "fina_indicator",
    }

    def __init__(
        self,
        *,
        primary: DataProvider | None = None,
        fallback: DataProvider | None | object = _UNSET,
        cache: Any | None = None,
    ) -> None:
        self._primary = primary or self._build_primary()
        # ``None`` explicitly disables fallback; omitted fallback keeps the
        # production default of Tushare → AkShare.
        fallback_provider = (
            self._build_fallback(self._primary)
            if fallback is _UNSET
            else fallback
        )
        self._fallback = cast(DataProvider | None, fallback_provider)
        self._cache = cache if cache is not None else CacheManager()

    def fetch(
        self,
        code: str,
        start_date: str,
        end_date: str,
        *,
        include_financials: bool = True,
        financial_start_date: str | None = None,
        adjustment: AdjustmentMode = "none",
    ) -> StockDataBundle:
        """Fetch the datasets needed by single-stock analysis.

        Stock information and daily prices are critical. If both providers
        fail, ``DataProviderError`` is raised. Financial datasets are allowed
        to be missing and are marked as ``partial`` instead.
        """
        if adjustment not in {"none", "qfq", "hfq"}:
            raise ValueError("adjustment 必须是 none、qfq 或 hfq")
        warnings: list[str] = []
        providers: dict[str, str] = {}
        quality: dict[str, str] = {}

        stock_info, provider, status, messages = self._fetch_critical(
            "stock_info", "get_stock_basic", (code,)
        )
        providers["stock_info"] = provider
        quality["stock_info"] = status
        warnings.extend(messages)

        daily, provider, status, messages = self._fetch_daily(code, start_date, end_date)
        providers["daily"] = provider
        quality["daily"] = status
        warnings.extend(messages)

        if adjustment != "none":
            factors, factor_provider, factor_status, factor_messages = (
                self._fetch_adjustment_factors(code, start_date, end_date)
            )
            daily = apply_price_adjustment(daily, factors, adjustment)
            providers["adj_factor"] = factor_provider
            quality["adj_factor"] = factor_status
            warnings.extend(factor_messages)

        daily_basic, provider, status, messages = self._fetch_optional(
            "daily_basic", "get_daily_basic", (code, end_date), default={}
        )
        providers["daily_basic"] = provider
        quality["daily_basic"] = status
        warnings.extend(messages)

        moneyflow, provider, status, messages = self._fetch_moneyflow(
            code, start_date, end_date
        )
        providers["moneyflow"] = provider
        quality["moneyflow"] = status
        warnings.extend(messages)

        financial_values: dict[str, pd.DataFrame] = {
            name: pd.DataFrame() for name in self._FINANCIAL_DATASETS
        }
        if include_financials:
            financial_start = financial_start_date or start_date
            for dataset, method_name in self._FINANCIAL_DATASETS.items():
                value, provider, status, messages = self._fetch_financial(
                    dataset,
                    method_name,
                    code,
                    financial_start,
                    end_date,
                )
                financial_values[dataset] = value
                providers[dataset] = provider
                quality[dataset] = status
                warnings.extend(messages)

        return StockDataBundle(
            stock_info=stock_info,
            daily=daily,
            daily_basic=daily_basic,
            income=financial_values["income"],
            balance_sheet=financial_values["balance_sheet"],
            cashflow=financial_values["cashflow"],
            fina_indicator=financial_values["fina_indicator"],
            moneyflow=moneyflow,
            adjustment=adjustment,
            providers=providers,
            quality=quality,
            warnings=warnings,
        )

    def fetch_market_data(
        self,
        code: str,
        start_date: str,
        end_date: str,
        *,
        adjustment: AdjustmentMode = "none",
    ) -> StockDataBundle:
        """Fetch only the datasets required by CLI comparison views."""
        return self.fetch(
            code,
            start_date,
            end_date,
            include_financials=False,
            adjustment=adjustment,
        )

    def _fetch_critical(
        self,
        dataset: str,
        method_name: str,
        args: tuple[Any, ...],
    ) -> tuple[Any, str, str, list[str]]:
        warnings: list[str] = []
        primary_error: Exception | None = None
        try:
            value = getattr(self._primary, method_name)(*args)
            if self._has_value(value):
                return value, self._primary.name, "ok", warnings
            raise DataProviderError(f"{dataset} returned empty data", provider=self._primary.name)
        except Exception as error:
            primary_error = error
            warnings.append(self._warning(dataset, self._primary.name, primary_error))

        if self._fallback is not None:
            try:
                value = getattr(self._fallback, method_name)(*args)
                if self._has_value(value):
                    warnings.append(f"{dataset}: 已降级到 {self._fallback.name}")
                    return value, self._fallback.name, "partial", warnings
                raise DataProviderError(
                    f"{dataset} returned empty data", provider=self._fallback.name
                )
            except Exception as fallback_error:
                warnings.append(self._warning(dataset, self._fallback.name, fallback_error))

        raise DataProviderError(
            f"{dataset} 主数据源和降级数据源均不可用",
            provider=self._primary.name,
            original=primary_error,
        )

    def _fetch_daily(
        self,
        code: str,
        start_date: str,
        end_date: str,
    ) -> tuple[pd.DataFrame, str, str, list[str]]:
        warnings: list[str] = []
        cached: pd.DataFrame | None = None
        try:
            cached = self._cache.get_daily(code, start_date, end_date)
            if cached is not None and not cached.empty:
                if self._cache.is_daily_fresh(code, end_date):
                    return cached, "cache", "ok", warnings
                warnings.append("daily: 缓存数据未覆盖请求日期，将尝试刷新")
        except Exception as cache_error:
            warnings.append(self._warning("daily cache", "cache", cache_error))

        try:
            value = self._primary.get_daily(code, start_date, end_date)
            if value is None or value.empty:
                raise DataProviderError("daily returned empty data", provider=self._primary.name)
            self._save_daily(code, value, warnings)
            return value, self._primary.name, "ok", warnings
        except Exception as primary_error:
            warnings.append(self._warning("daily", self._primary.name, primary_error))

        if self._fallback is not None:
            try:
                value = self._fallback.get_daily(code, start_date, end_date)
                if value is not None and not value.empty:
                    warnings.append(f"daily: 已降级到 {self._fallback.name}")
                    self._save_daily(code, value, warnings)
                    return value, self._fallback.name, "partial", warnings
            except Exception as fallback_error:
                warnings.append(self._warning("daily", self._fallback.name, fallback_error))

        if cached is not None and not cached.empty:
            warnings.append("daily: 使用过期缓存，结果标记为 stale")
            return cached, "cache", "stale", warnings

        raise DataProviderError(
            "daily 主数据源、降级数据源和缓存均不可用", provider=self._primary.name
        )

    def _fetch_optional(
        self,
        dataset: str,
        method_name: str,
        args: tuple[Any, ...],
        *,
        default: DatasetValue,
    ) -> tuple[DatasetValue, str, str, list[str]]:
        warnings: list[str] = []
        try:
            value = getattr(self._primary, method_name)(*args)
            if self._has_value(value):
                return value, self._primary.name, "ok", warnings
            warnings.append(f"{dataset}: 主数据源返回空数据")
        except Exception as primary_error:
            warnings.append(self._warning(dataset, self._primary.name, primary_error))
        return default, "unavailable", "partial", warnings

    def _fetch_financial(
        self,
        dataset: str,
        method_name: str,
        code: str,
        start_date: str,
        end_date: str,
    ) -> tuple[pd.DataFrame, str, str, list[str]]:
        warnings: list[str] = []
        cached = self._get_financial_cache(code, dataset, end_date, warnings)
        if cached is not None and not cached.empty:
            return cached, "cache", "ok", warnings

        try:
            value = getattr(self._primary, method_name)(code, start_date, end_date)
            if value is None or value.empty:
                warnings.append(f"{dataset}: 主数据源返回空数据")
                return pd.DataFrame(), "unavailable", "partial", warnings
            # Persist every version that was public by the requested date;
            # canonical revision selection happens only in the returned view.
            value = filter_financial_as_of(value, as_of=end_date)
            if value.empty:
                warnings.append(f"{dataset}: 过滤公告期后无可用数据")
                return pd.DataFrame(), "unavailable", "partial", warnings
            self._save_financial_cache(code, dataset, value, warnings)
            return (
                normalize_financial_frame(value, as_of=end_date),
                self._primary.name,
                "ok",
                warnings,
            )
        except Exception as primary_error:
            warnings.append(self._warning(dataset, self._primary.name, primary_error))
            warnings.append(f"{dataset}: AkShare 不提供等价财务数据，保留为空")
            return pd.DataFrame(), "unavailable", "partial", warnings

    def _fetch_adjustment_factors(
        self,
        code: str,
        start_date: str,
        end_date: str,
    ) -> tuple[pd.DataFrame, str, str, list[str]]:
        """Fetch factors as a required dependency for qfq/hfq requests."""
        warnings: list[str] = []
        cached: pd.DataFrame | None = None
        cache_covered = False
        get_cached = getattr(self._cache, "get_adj_factor", None)
        get_meta = getattr(self._cache, "get_meta", None)
        if callable(get_cached):
            try:
                cached = get_cached(code, start_date, end_date)
                if cached is not None and not cached.empty:
                    cached_dates = pd.to_datetime(cached["trade_date"], errors="coerce")
                    start = pd.to_datetime(start_date, errors="coerce")
                    end = pd.to_datetime(end_date, errors="coerce")
                    cache_covered = (
                        pd.notna(start)
                        and pd.notna(end)
                        and cached_dates.notna().all()
                        and cached_dates.min() <= start
                        and cached_dates.max() >= end
                    )
                    meta = (
                        get_meta(f"adjustments/{code}")
                        if callable(get_meta)
                        else None
                    ) or {}
                    updated_at = meta.get("updated_at")
                    fresh = False
                    if updated_at:
                        updated = datetime.fromisoformat(str(updated_at))
                        now = datetime.now(updated.tzinfo) if updated.tzinfo else datetime.now()
                        fresh = (
                            now - updated
                        ).total_seconds() <= get_config().cache.ttl_adjustment_factors
                    if cache_covered and fresh:
                        return cached, "cache", "ok", warnings
                    if not cache_covered:
                        warnings.append("adj_factor: 缓存未覆盖请求区间，将尝试刷新")
                    else:
                        warnings.append("adj_factor: 缓存已超过复权因子 TTL，将尝试刷新")
            except Exception as cache_error:
                warnings.append(self._warning("adj_factor cache", "cache", cache_error))

        def save_cached(value: pd.DataFrame, source: str) -> None:
            save = getattr(self._cache, "save_adj_factor", None)
            if not callable(save):
                return
            try:
                save(code, value.copy(), source)
            except TypeError:
                # 兼容旧的轻量缓存实现，只接受 code 和 frame。
                try:
                    save(code, value.copy())
                except Exception as cache_error:
                    warnings.append(self._warning("adj_factor cache", "cache", cache_error))
            except Exception as cache_error:
                warnings.append(self._warning("adj_factor cache", "cache", cache_error))

        try:
            value = self._primary.get_adj_factor(code, start_date, end_date)
            if value is not None and not value.empty:
                save_cached(value, self._primary.name)
                return value, self._primary.name, "ok", warnings
            warnings.append("adj_factor: 主数据源返回空数据")
        except Exception as primary_error:
            warnings.append(self._warning("adj_factor", self._primary.name, primary_error))
        if self._fallback is not None:
            try:
                value = self._fallback.get_adj_factor(code, start_date, end_date)
                if value is not None and not value.empty:
                    warnings.append(f"adj_factor: 已降级到 {self._fallback.name}")
                    save_cached(value, self._fallback.name)
                    return value, self._fallback.name, "partial", warnings
            except Exception as fallback_error:
                warnings.append(self._warning("adj_factor", self._fallback.name, fallback_error))
        if cached is not None and not cached.empty and cache_covered:
            warnings.append("adj_factor: 主源不可用，使用覆盖完整但过期的缓存，结果标记为 stale")
            return cached, "cache", "stale", warnings
        raise DataProviderError(
            "复权请求缺少可用的复权因子，拒绝回退到未复权行情",
            provider=self._primary.name,
        )

    def _fetch_moneyflow(
        self,
        code: str,
        start_date: str,
        end_date: str,
    ) -> tuple[pd.DataFrame, str, str, list[str]]:
        """Fetch historical money flow without silently using proxy data."""
        warnings: list[str] = []
        get_cached = getattr(self._cache, "get_moneyflow", None)
        if callable(get_cached):
            try:
                cached = get_cached(code, start_date, end_date)
                if cached is not None and not cached.empty:
                    return (
                        normalize_moneyflow_frame(cached, as_of=end_date),
                        "cache",
                        "ok",
                        warnings,
                    )
            except Exception as cache_error:
                warnings.append(self._warning("moneyflow cache", "cache", cache_error))
        try:
            value = self._primary.get_moneyflow(code, start_date, end_date)
            normalized = normalize_moneyflow_frame(value, as_of=end_date)
            if normalized.empty:
                warnings.append("moneyflow: 主数据源返回空数据")
                return pd.DataFrame(), "unavailable", "partial", warnings
            save_cached = getattr(self._cache, "save_moneyflow", None)
            if callable(save_cached):
                try:
                    save_cached(code, normalized.copy())
                except Exception as cache_error:
                    warnings.append(self._warning("moneyflow cache", "cache", cache_error))
            return normalized, self._primary.name, "ok", warnings
        except Exception as primary_error:
            warnings.append(self._warning("moneyflow", self._primary.name, primary_error))
            warnings.append(
                "moneyflow: 没有可用的独立历史资金流，情绪分析保留价量代理或缺失"
            )
            return pd.DataFrame(), "unavailable", "partial", warnings

    def _get_financial_cache(
        self,
        code: str,
        dataset: str,
        as_of: str,
        warnings: list[str],
    ) -> pd.DataFrame | None:
        try:
            try:
                value = self._cache.get_financials(code, dataset, as_of=as_of)
            except TypeError:
                # Keep compatibility with lightweight cache implementations
                # used by integrations while production CacheManager supports
                # point-in-time filtering natively.
                value = self._cache.get_financials(code, dataset)
                value = normalize_financial_frame(value, as_of=as_of) if value is not None else None
            if value is None or value.empty:
                return None
            get_meta = getattr(self._cache, "get_meta", None)
            if callable(get_meta):
                meta = get_meta(f"financials/{code}/{dataset}") or {}
                updated_at = meta.get("updated_at")
                if updated_at:
                    updated = datetime.fromisoformat(str(updated_at))
                    now = datetime.now(updated.tzinfo) if updated.tzinfo else datetime.now()
                    age_seconds = (now - updated).total_seconds()
                    if age_seconds > get_config().cache.ttl_financials:
                        warnings.append(f"{dataset}: 缓存已超过财务数据 TTL，将尝试刷新")
                        return None
            return value
        except Exception as cache_error:
            warnings.append(self._warning(f"{dataset} cache", "cache", cache_error))
            return None

    def _save_daily(self, code: str, value: pd.DataFrame, warnings: list[str]) -> None:
        try:
            # CacheManager adds partition helper columns while persisting;
            # keep those implementation details out of the analysis frame.
            self._cache.save_daily(code, value.copy())
        except Exception as cache_error:
            warnings.append(self._warning("daily cache", "cache", cache_error))

    def _save_financial_cache(
        self,
        code: str,
        dataset: str,
        value: pd.DataFrame,
        warnings: list[str],
    ) -> None:
        try:
            self._cache.save_financials(code, dataset, value.copy())
        except Exception as cache_error:
            warnings.append(self._warning(f"{dataset} cache", "cache", cache_error))

    @staticmethod
    def _has_value(value: Any) -> bool:
        if value is None:
            return False
        if isinstance(value, pd.DataFrame):
            return not value.empty
        return bool(value)

    @staticmethod
    def _warning(dataset: str, provider: str, error: Exception) -> str:
        return f"{dataset}: {provider} 失败（{type(error).__name__}）"

    @staticmethod
    def _build_primary() -> DataProvider:
        from src.data.providers.tushare import TushareProvider

        return TushareProvider()

    @staticmethod
    def _build_fallback(primary: DataProvider) -> DataProvider | None:
        if primary.name == "akshare":
            return None
        from src.data.providers.akshare import AkShareProvider

        return AkShareProvider()
