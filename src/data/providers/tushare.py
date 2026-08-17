"""Tushare Pro 数据源实现。"""

import threading
import time
from typing import Any

import pandas as pd
import tushare as ts

from src.config import get_config
from src.data.financials import filter_financial_as_of
from src.data.market_behavior import normalize_moneyflow_frame
from src.data.monitoring import monitor_call
from src.data.providers.base import DataProvider, DataProviderError
from src.data.rate_limit import RateLimiter

# 进程级共享限频器（惰性单例，兼容测试中 reset_config 后重建）。
_rate_limiter: RateLimiter | None = None
_rate_limiter_lock = threading.Lock()

# Retry decision: network/timeout and rate-limit hiccups are transient, while
# permission/integral errors are permanent and must fail fast (Tushare raises
# a plain Exception whose message carries the reason).
_RETRYABLE_MARKERS = (
    "timeout", "timed out", "connection", "connect", "network",
    "remotedisconnected", "winerror", "sslerror", "limit", "too many",
    "限流", "每分钟", "频繁", "访问次数",
)
_PERMANENT_MARKERS = ("权限", "积分", "没有", "无权限", "无法获取")


def _provider_gate() -> RateLimiter:
    """Return the process-wide provider rate limiter, building it on first use."""
    global _rate_limiter
    if _rate_limiter is None:
        with _rate_limiter_lock:
            if _rate_limiter is None:
                _rate_limiter = RateLimiter(
                    get_config().batch.request_min_interval_s
                )
    return _rate_limiter


class TushareProvider(DataProvider):
    """Tushare Pro 数据源。

    封装 Tushare Pro API，提供股票基本信息、日线行情、财务数据、
    复权因子、每日指标、交易日历等数据。
    """

    def __init__(self):
        super().__init__(name="tushare")
        config = get_config()
        self._token = config.tushare_token
        self._timeout = config.tushare.timeout
        self._retry_count = config.tushare.retry_count
        self._retry_delay = config.tushare.retry_delay
        self._pro = ts.pro_api(self._token)

    def _request(self, fn_name: str, *args: Any, **kwargs: Any) -> Any:
        """Rate-limit and retry one Tushare SDK call with exponential backoff.

        Retries are limited to transient API/network failures; a genuinely
        empty result is the caller's responsibility (some endpoints legitimately
        return no rows, e.g. a newly listed stock).
        """
        _provider_gate().acquire()
        attempts = max(1, self._retry_count)
        last_exc: Exception | None = None
        for attempt in range(attempts):
            try:
                return getattr(self._pro, fn_name)(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001 - SDK raises generic Exception
                last_exc = exc
                if attempt < attempts - 1 and self._is_retryable(exc):
                    time.sleep(self._retry_delay * (2**attempt))
                else:
                    break
        raise DataProviderError(
            f"{fn_name} 调用失败: {last_exc}", provider=self.name, original=last_exc
        )

    @staticmethod
    def _is_retryable(exc: Exception) -> bool:
        """Distinguish transient network/rate-limit failures from permanent ones."""
        if isinstance(exc, (OSError, TimeoutError, ConnectionError)):
            return True
        text = str(exc)
        if any(marker in text for marker in _PERMANENT_MARKERS):
            return False
        lowered = text.lower()
        return any(marker in lowered for marker in _RETRYABLE_MARKERS)

    # ------------------------------------------------------------------
    # 股票基本信息
    # ------------------------------------------------------------------
    @monitor_call
    def get_stock_basic(self, code: str) -> dict[str, Any]:
        """获取股票基本信息。"""
        try:
            df = self._request(
                "stock_basic",
                ts_code=code,
                fields="ts_code,name,industry,area,list_date,market",
            )
            if df is None or df.empty:
                raise DataProviderError(f"未找到股票 {code}", provider=self.name)
            row = df.iloc[0]
            return {
                "code": row["ts_code"],
                "name": row["name"],
                "industry": row.get("industry", ""),
                "area": row.get("area", ""),
                "list_date": row.get("list_date", ""),
                "market": row.get("market", ""),
            }
        except DataProviderError:
            raise
        except Exception as e:
            raise DataProviderError(f"获取股票信息失败: {e}", provider=self.name, original=e)

    # ------------------------------------------------------------------
    # 日线行情
    # ------------------------------------------------------------------
    @monitor_call
    def get_daily(
        self, code: str, start_date: str, end_date: str
    ) -> pd.DataFrame:
        """获取日线行情（未复权）。"""
        try:
            df = self._request(
                "daily",
                ts_code=code,
                start_date=start_date.replace("-", ""),
                end_date=end_date.replace("-", ""),
                fields="trade_date,open,high,low,close,vol,amount",
            )
            if df is None or df.empty:
                return pd.DataFrame()
            df = df.rename(columns={"vol": "volume"})
            df["trade_date"] = pd.to_datetime(df["trade_date"], format="%Y%m%d")
            df = df.sort_values("trade_date").reset_index(drop=True)
            return df
        except Exception as e:
            raise DataProviderError(f"获取日线行情失败: {e}", provider=self.name, original=e)

    # ------------------------------------------------------------------
    # 复权因子
    # ------------------------------------------------------------------
    def get_adj_factor(
        self, code: str, start_date: str, end_date: str
    ) -> pd.DataFrame:
        """获取复权因子。"""
        try:
            df = self._request(
                "adj_factor",
                ts_code=code,
                start_date=start_date.replace("-", ""),
                end_date=end_date.replace("-", ""),
            )
            if df is None or df.empty:
                return pd.DataFrame()
            df["trade_date"] = pd.to_datetime(df["trade_date"], format="%Y%m%d")
            df = df.sort_values("trade_date").reset_index(drop=True)
            return df
        except Exception as e:
            raise DataProviderError(f"获取复权因子失败: {e}", provider=self.name, original=e)

    # ------------------------------------------------------------------
    # 每日指标
    # ------------------------------------------------------------------
    def get_daily_basic(self, code: str, trade_date: str) -> dict[str, Any]:
        """获取每日指标。"""
        try:
            df = self._request(
                "daily_basic",
                ts_code=code,
                trade_date=trade_date.replace("-", ""),
                fields="ts_code,trade_date,pe_ttm,pb,ps_ttm,total_mv,circ_mv",
            )
            if df is None or df.empty:
                return {}
            row = df.iloc[0]
            return {
                "pe_ttm": row.get("pe_ttm"),
                "pb": row.get("pb"),
                "ps_ttm": row.get("ps_ttm"),
                "total_mv": row.get("total_mv"),
                "circ_mv": row.get("circ_mv"),
            }
        except Exception as e:
            raise DataProviderError(f"获取每日指标失败: {e}", provider=self.name, original=e)

    # ------------------------------------------------------------------
    # 资金流
    # ------------------------------------------------------------------
    def get_moneyflow(
        self, code: str, start_date: str, end_date: str
    ) -> pd.DataFrame:
        """获取历史资金流，用于独立于价量代理的市场行为证据。"""
        try:
            df = self._request(
                "moneyflow",
                ts_code=code,
                start_date=start_date.replace("-", ""),
                end_date=end_date.replace("-", ""),
                fields="ts_code,trade_date,buy_sm_vol,sell_sm_vol,buy_md_vol,"
                "sell_md_vol,buy_lg_vol,sell_lg_vol,buy_elg_vol,sell_elg_vol,"
                "net_mf_vol,net_mf_amount",
            )
            return normalize_moneyflow_frame(df, as_of=end_date)
        except Exception as e:
            raise DataProviderError(f"获取资金流失败: {e}", provider=self.name, original=e)

    # ------------------------------------------------------------------
    # 财务数据
    # ------------------------------------------------------------------
    def get_income(
        self, code: str, start_date: str, end_date: str
    ) -> pd.DataFrame:
        """获取利润表。"""
        try:
            df = self._request(
                "income",
                ts_code=code,
                start_date=start_date.replace("-", ""),
                end_date=end_date.replace("-", ""),
                fields="ann_date,f_ann_date,end_date,report_type,comp_type,update_flag,"
                "revenue,operate_profit,n_income,n_income_attr_p,eps",
            )
            if df is None or df.empty:
                return pd.DataFrame()
            df = df.rename(
                columns={
                    "operate_profit": "operating_profit",
                    "n_income": "net_profit",
                    "n_income_attr_p": "net_profit_attributable",
                }
            )
            return filter_financial_as_of(df)
        except Exception as e:
            raise DataProviderError(f"获取利润表失败: {e}", provider=self.name, original=e)

    def get_balance_sheet(
        self, code: str, start_date: str, end_date: str
    ) -> pd.DataFrame:
        """获取资产负债表。"""
        try:
            df = self._request(
                "balancesheet",
                ts_code=code,
                start_date=start_date.replace("-", ""),
                end_date=end_date.replace("-", ""),
                fields="ann_date,f_ann_date,end_date,report_type,comp_type,update_flag,"
                "total_assets,total_liab,total_hldr_eqy_exc_min_int",
            )
            if df is None or df.empty:
                return pd.DataFrame()
            df = df.rename(
                columns={
                    "total_liab": "total_liabilities",
                    "total_hldr_eqy_exc_min_int": "shareholders_equity",
                }
            )
            return filter_financial_as_of(df)
        except Exception as e:
            raise DataProviderError(f"获取资产负债表失败: {e}", provider=self.name, original=e)

    def get_cashflow(
        self, code: str, start_date: str, end_date: str
    ) -> pd.DataFrame:
        """获取现金流量表。"""
        try:
            df = self._request(
                "cashflow",
                ts_code=code,
                start_date=start_date.replace("-", ""),
                end_date=end_date.replace("-", ""),
                fields="ann_date,f_ann_date,end_date,report_type,comp_type,update_flag,"
                "n_cashflow_act,n_cashflow_inv_act,n_cashflow_fin_act",
            )
            if df is None or df.empty:
                return pd.DataFrame()
            df = df.rename(
                columns={
                    "n_cashflow_act": "operating_cf",
                    "n_cashflow_inv_act": "investing_cf",
                    "n_cashflow_fin_act": "financing_cf",
                }
            )
            return filter_financial_as_of(df)
        except Exception as e:
            raise DataProviderError(f"获取现金流量表失败: {e}", provider=self.name, original=e)

    def get_fina_indicator(
        self, code: str, start_date: str, end_date: str
    ) -> pd.DataFrame:
        """获取财务指标。"""
        try:
            df = self._request(
                "fina_indicator",
                ts_code=code,
                start_date=start_date.replace("-", ""),
                end_date=end_date.replace("-", ""),
                fields="ann_date,end_date,update_flag,roe,roa,grossprofit_margin,"
                "netprofit_margin,debt_to_assets,current_ratio,quick_ratio,"
                "or_yoy,netprofit_yoy,q_sales_yoy,q_netprofit_yoy,profit_dedt,extra_item",
            )
            if df is None or df.empty:
                return pd.DataFrame()
            df = df.rename(
                columns={
                    "netprofit_margin": "net_margin",
                    # Tushare's ``gross_margin`` is an absolute gross-profit
                    # amount; ``grossprofit_margin`` is the percentage field
                    # expected by the normalized analysis contract.
                    "grossprofit_margin": "gross_margin",
                    "or_yoy": "revenue_yoy_pct",
                    "netprofit_yoy": "net_profit_yoy_pct",
                    "q_sales_yoy": "quarter_revenue_yoy_pct",
                    "q_netprofit_yoy": "quarter_net_profit_yoy_pct",
                    "profit_dedt": "deducted_net_profit",
                    "extra_item": "nonrecurring_profit",
                }
            )
            return filter_financial_as_of(df)
        except Exception as e:
            raise DataProviderError(f"获取财务指标失败: {e}", provider=self.name, original=e)

    # ------------------------------------------------------------------
    # 交易日历
    # ------------------------------------------------------------------
    def get_trade_cal(
        self, exchange: str, start_date: str, end_date: str
    ) -> pd.DataFrame:
        """获取交易日历。"""
        try:
            df = self._request(
                "trade_cal",
                exchange=exchange,
                start_date=start_date.replace("-", ""),
                end_date=end_date.replace("-", ""),
                fields="cal_date,is_open,pretrade_date",
            )
            if df is None or df.empty:
                return pd.DataFrame()
            df["cal_date"] = pd.to_datetime(df["cal_date"], format="%Y%m%d")
            return df.sort_values("cal_date").reset_index(drop=True)
        except Exception as e:
            raise DataProviderError(f"获取交易日历失败: {e}", provider=self.name, original=e)
