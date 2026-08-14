"""Tests for cache/provider routing and explicit fallback behavior."""

from __future__ import annotations

import pandas as pd
import pytest

from src.data.gateway import DataGateway
from src.data.providers.base import DataProviderError


def _daily() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "trade_date": [pd.Timestamp("2026-08-13"), pd.Timestamp("2026-08-14")],
            "open": [10.0, 10.5],
            "high": [11.0, 11.5],
            "low": [9.5, 10.0],
            "close": [10.5, 11.0],
            "volume": [100, 120],
            "amount": [1000, 1200],
        }
    )


class FakeProvider:
    def __init__(self, name: str, *, fail: set[str] | None = None) -> None:
        self.name = name
        self.fail = fail or set()
        self.calls: list[str] = []

    def _call(self, method: str, value):
        self.calls.append(method)
        if method in self.fail:
            raise RuntimeError(f"{self.name} {method} failed")
        return value

    def get_stock_basic(self, code: str):
        return self._call("get_stock_basic", {"code": code, "name": self.name})

    def get_daily(self, code: str, start_date: str, end_date: str):
        return self._call("get_daily", _daily())

    def get_daily_basic(self, code: str, trade_date: str):
        return self._call("get_daily_basic", {"pe_ttm": 20.0})

    def get_income(self, code: str, start_date: str, end_date: str):
        return self._call("get_income", pd.DataFrame({"end_date": [pd.Timestamp("2025-12-31")]}))

    def get_balance_sheet(self, code: str, start_date: str, end_date: str):
        return self._call("get_balance_sheet", pd.DataFrame())

    def get_cashflow(self, code: str, start_date: str, end_date: str):
        return self._call("get_cashflow", pd.DataFrame())

    def get_fina_indicator(self, code: str, start_date: str, end_date: str):
        return self._call("get_fina_indicator", pd.DataFrame())


class FakeCache:
    def __init__(self, daily: pd.DataFrame | None = None, fresh: bool = True) -> None:
        self.daily = daily
        self.fresh = fresh
        self.saved_daily: list[pd.DataFrame] = []
        self.financials: dict[str, pd.DataFrame] = {}
        self.meta: dict[str, dict[str, str]] = {}

    def get_daily(self, code: str, start_date: str, end_date: str):
        return self.daily

    def is_daily_fresh(self, code: str, trade_date: str) -> bool:
        return self.fresh

    def save_daily(self, code: str, value: pd.DataFrame) -> None:
        self.saved_daily.append(value)

    def get_financials(self, code: str, report_type: str):
        return self.financials.get(report_type)

    def save_financials(self, code: str, report_type: str, value: pd.DataFrame) -> None:
        self.financials[report_type] = value

    def get_meta(self, cache_key: str):
        return self.meta.get(cache_key)


def test_gateway_uses_fresh_cache_before_primary_daily_call() -> None:
    primary = FakeProvider("tushare")
    cache = FakeCache(_daily(), fresh=True)
    gateway = DataGateway(primary=primary, fallback=None, cache=cache)

    result = gateway.fetch_market_data("600519.SH", "20240101", "2026-08-14")

    assert result.providers["daily"] == "cache"
    assert result.quality["daily"] == "ok"
    assert "get_daily" not in primary.calls
    assert result.daily.equals(cache.daily)


def test_gateway_falls_back_for_critical_market_data() -> None:
    primary = FakeProvider("tushare", fail={"get_stock_basic", "get_daily"})
    fallback = FakeProvider("akshare")
    gateway = DataGateway(primary=primary, fallback=fallback, cache=FakeCache())

    result = gateway.fetch_market_data("600519.SH", "20240101", "2026-08-14")

    assert result.providers["stock_info"] == "akshare"
    assert result.providers["daily"] == "akshare"
    assert result.quality["daily"] == "partial"
    assert any("已降级到 akshare" in warning for warning in result.warnings)


def test_gateway_keeps_unsupported_financial_fallback_explicitly_missing() -> None:
    primary = FakeProvider("tushare", fail={"get_income"})
    fallback = FakeProvider("akshare")
    gateway = DataGateway(primary=primary, fallback=fallback, cache=FakeCache())

    result = gateway.fetch("600519.SH", "20240101", "2026-08-14")

    assert result.income.empty
    assert result.providers["income"] == "unavailable"
    assert result.quality["income"] == "partial"
    assert "get_income" not in fallback.calls
    assert any("AkShare 不提供等价财务数据" in warning for warning in result.warnings)


def test_gateway_raises_when_critical_data_has_no_provider_or_cache() -> None:
    primary = FakeProvider("tushare", fail={"get_stock_basic"})
    fallback = FakeProvider("akshare", fail={"get_stock_basic"})
    gateway = DataGateway(primary=primary, fallback=fallback, cache=FakeCache())

    with pytest.raises(DataProviderError, match="主数据源和降级数据源均不可用"):
        gateway.fetch_market_data("600519.SH", "20240101", "2026-08-14")


def test_gateway_honors_explicitly_disabled_fallback() -> None:
    primary = FakeProvider("tushare", fail={"get_stock_basic"})
    fallback = FakeProvider("akshare")
    gateway = DataGateway(primary=primary, fallback=None, cache=FakeCache())

    with pytest.raises(DataProviderError, match="主数据源和降级数据源均不可用"):
        gateway.fetch_market_data("600519.SH", "20240101", "2026-08-14")
    assert "get_stock_basic" not in fallback.calls


def test_gateway_uses_fresh_financial_cache() -> None:
    primary = FakeProvider("tushare")
    cache = FakeCache()
    cached_income = pd.DataFrame({"end_date": [pd.Timestamp("2025-12-31")]})
    cache.financials["income"] = cached_income
    cache.meta["financials/600519.SH/income"] = {
        "updated_at": "2026-08-13T10:00:00",
    }
    gateway = DataGateway(primary=primary, fallback=None, cache=cache)

    result = gateway.fetch("600519.SH", "20240101", "2026-08-14")

    assert result.providers["income"] == "cache"
    assert result.income.equals(cached_income)
    assert "get_income" not in primary.calls
