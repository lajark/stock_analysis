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
from src.data.cache import CacheManager
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
    ) -> StockDataBundle:
        """Fetch the datasets needed by single-stock analysis.

        Stock information and daily prices are critical. If both providers
        fail, ``DataProviderError`` is raised. Financial datasets are allowed
        to be missing and are marked as ``partial`` instead.
        """
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

        daily_basic, provider, status, messages = self._fetch_optional(
            "daily_basic", "get_daily_basic", (code, end_date), default={}
        )
        providers["daily_basic"] = provider
        quality["daily_basic"] = status
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
            providers=providers,
            quality=quality,
            warnings=warnings,
        )

    def fetch_market_data(self, code: str, start_date: str, end_date: str) -> StockDataBundle:
        """Fetch only the datasets required by CLI comparison views."""
        return self.fetch(code, start_date, end_date, include_financials=False)

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
        cached = self._get_financial_cache(code, dataset, warnings)
        if cached is not None and not cached.empty:
            return cached, "cache", "ok", warnings

        try:
            value = getattr(self._primary, method_name)(code, start_date, end_date)
            if value is None or value.empty:
                warnings.append(f"{dataset}: 主数据源返回空数据")
                return pd.DataFrame(), "unavailable", "partial", warnings
            self._save_financial_cache(code, dataset, value, warnings)
            return value, self._primary.name, "ok", warnings
        except Exception as primary_error:
            warnings.append(self._warning(dataset, self._primary.name, primary_error))
            warnings.append(f"{dataset}: AkShare 不提供等价财务数据，保留为空")
            return pd.DataFrame(), "unavailable", "partial", warnings

    def _get_financial_cache(
        self,
        code: str,
        dataset: str,
        warnings: list[str],
    ) -> pd.DataFrame | None:
        try:
            value = self._cache.get_financials(code, dataset)
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
