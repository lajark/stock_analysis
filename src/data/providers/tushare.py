"""Tushare Pro 数据源实现。"""

from typing import Any

import pandas as pd
import tushare as ts

from src.config import get_config
from src.data.monitoring import monitor_call
from src.data.providers.base import DataProvider, DataProviderError


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
        self._pro = ts.pro_api(self._token)

    # ------------------------------------------------------------------
    # 股票基本信息
    # ------------------------------------------------------------------
    @monitor_call
    def get_stock_basic(self, code: str) -> dict[str, Any]:
        """获取股票基本信息。"""
        try:
            df = self._pro.stock_basic(
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
            df = self._pro.daily(
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
            df = self._pro.adj_factor(
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
            df = self._pro.daily_basic(
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
    # 财务数据
    # ------------------------------------------------------------------
    def get_income(
        self, code: str, start_date: str, end_date: str
    ) -> pd.DataFrame:
        """获取利润表。"""
        try:
            df = self._pro.income(
                ts_code=code,
                start_date=start_date.replace("-", ""),
                end_date=end_date.replace("-", ""),
                fields="end_date,revenue,operate_profit,n_income,eps",
            )
            if df is None or df.empty:
                return pd.DataFrame()
            df = df.rename(columns={"operate_profit": "operating_profit", "n_income": "net_profit"})
            df["end_date"] = pd.to_datetime(df["end_date"], format="%Y%m%d")
            df = df.sort_values("end_date").reset_index(drop=True)
            return df
        except Exception as e:
            raise DataProviderError(f"获取利润表失败: {e}", provider=self.name, original=e)

    def get_balance_sheet(
        self, code: str, start_date: str, end_date: str
    ) -> pd.DataFrame:
        """获取资产负债表。"""
        try:
            df = self._pro.balancesheet(
                ts_code=code,
                start_date=start_date.replace("-", ""),
                end_date=end_date.replace("-", ""),
                fields="end_date,total_assets,total_liab,total_hldr_eqy_exc_min_int",
            )
            if df is None or df.empty:
                return pd.DataFrame()
            df = df.rename(
                columns={
                    "total_liab": "total_liabilities",
                    "total_hldr_eqy_exc_min_int": "shareholders_equity",
                }
            )
            df["end_date"] = pd.to_datetime(df["end_date"], format="%Y%m%d")
            df = df.sort_values("end_date").reset_index(drop=True)
            return df
        except Exception as e:
            raise DataProviderError(f"获取资产负债表失败: {e}", provider=self.name, original=e)

    def get_cashflow(
        self, code: str, start_date: str, end_date: str
    ) -> pd.DataFrame:
        """获取现金流量表。"""
        try:
            df = self._pro.cashflow(
                ts_code=code,
                start_date=start_date.replace("-", ""),
                end_date=end_date.replace("-", ""),
                fields="end_date,n_cashflow_act,n_cashflow_inv_act,n_cashflow_fin_act",
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
            df["end_date"] = pd.to_datetime(df["end_date"], format="%Y%m%d")
            df = df.sort_values("end_date").reset_index(drop=True)
            return df
        except Exception as e:
            raise DataProviderError(f"获取现金流量表失败: {e}", provider=self.name, original=e)

    def get_fina_indicator(
        self, code: str, start_date: str, end_date: str
    ) -> pd.DataFrame:
        """获取财务指标。"""
        try:
            df = self._pro.fina_indicator(
                ts_code=code,
                start_date=start_date.replace("-", ""),
                end_date=end_date.replace("-", ""),
                fields="end_date,roe,roa,grossprofit_margin,netprofit_margin,"
                "debt_to_assets,current_ratio,quick_ratio",
            )
            if df is None or df.empty:
                return pd.DataFrame()
            df = df.rename(
                columns={
                    "grossprofit_margin": "gross_margin",
                    "netprofit_margin": "net_margin",
                }
            )
            df["end_date"] = pd.to_datetime(df["end_date"], format="%Y%m%d")
            df = df.sort_values("end_date").reset_index(drop=True)
            return df
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
            df = self._pro.trade_cal(
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