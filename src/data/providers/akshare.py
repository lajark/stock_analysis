"""AkShare 数据源实现（降级备选）。

当 Tushare 不可用时自动切换到此数据源。
AkShare 零注册，但数据质量和接口稳定性低于 Tushare。
"""

from typing import Any

import akshare as ak
import pandas as pd

from src.data.providers.base import DataProvider, DataProviderError


class AkShareProvider(DataProvider):
    """AkShare 数据源 — Tushare 的降级备选。"""

    def __init__(self):
        super().__init__(name="akshare")

    # ------------------------------------------------------------------
    # 股票基本信息
    # ------------------------------------------------------------------
    def get_stock_basic(self, code: str) -> dict[str, Any]:
        try:
            df = ak.stock_individual_info_em(symbol=code)
            if df is None or df.empty:
                raise DataProviderError(f"未找到股票 {code}", provider=self.name)
            info = dict(zip(df["item"], df["value"]))
            return {
                "code": code,
                "name": info.get("股票简称", ""),
                "industry": info.get("行业", ""),
                "area": "",
                "list_date": info.get("上市时间", ""),
                "market": "SH" if code.endswith(".SH") else "SZ",
            }
        except DataProviderError:
            raise
        except Exception as e:
            raise DataProviderError(f"获取股票信息失败: {e}", provider=self.name, original=e)

    # ------------------------------------------------------------------
    # 日线行情
    # ------------------------------------------------------------------
    def get_daily(
        self, code: str, start_date: str, end_date: str
    ) -> pd.DataFrame:
        try:
            symbol = code.replace(".SH", "").replace(".SZ", "")
            df = ak.stock_zh_a_hist(
                symbol=symbol,
                period="daily",
                start_date=start_date.replace("-", ""),
                end_date=end_date.replace("-", ""),
                adjust="",
            )
            if df is None or df.empty:
                return pd.DataFrame()
            df = df.rename(
                columns={
                    "日期": "trade_date",
                    "开盘": "open",
                    "最高": "high",
                    "最低": "low",
                    "收盘": "close",
                    "成交量": "volume",
                    "成交额": "amount",
                }
            )
            df["trade_date"] = pd.to_datetime(df["trade_date"])
            cols = ["trade_date", "open", "high", "low", "close", "volume", "amount"]
            return df[cols].sort_values("trade_date").reset_index(drop=True)
        except Exception as e:
            raise DataProviderError(f"获取日线行情失败: {e}", provider=self.name, original=e)

    # ------------------------------------------------------------------
    # 复权因子（AkShare 不直接支持，返回空）
    # ------------------------------------------------------------------
    def get_adj_factor(
        self, code: str, start_date: str, end_date: str
    ) -> pd.DataFrame:
        return pd.DataFrame()

    # ------------------------------------------------------------------
    # 每日指标（AkShare 不直接支持，返回空）
    # ------------------------------------------------------------------
    def get_daily_basic(self, code: str, trade_date: str) -> dict[str, Any]:
        return {}

    # ------------------------------------------------------------------
    # 财务数据（AkShare 财务数据质量较低，返回空指导上层使用 Tushare）
    # ------------------------------------------------------------------
    def get_income(
        self, code: str, start_date: str, end_date: str
    ) -> pd.DataFrame:
        return pd.DataFrame()

    def get_balance_sheet(
        self, code: str, start_date: str, end_date: str
    ) -> pd.DataFrame:
        return pd.DataFrame()

    def get_cashflow(
        self, code: str, start_date: str, end_date: str
    ) -> pd.DataFrame:
        return pd.DataFrame()

    def get_fina_indicator(
        self, code: str, start_date: str, end_date: str
    ) -> pd.DataFrame:
        return pd.DataFrame()

    # ------------------------------------------------------------------
    # 交易日历
    # ------------------------------------------------------------------
    def get_trade_cal(
        self, exchange: str, start_date: str, end_date: str
    ) -> pd.DataFrame:
        try:
            df = ak.tool_trade_date_hist_sina()
            if df is None or df.empty:
                return pd.DataFrame()
            df = df.rename(columns={"trade_date": "cal_date"})
            df["cal_date"] = pd.to_datetime(df["cal_date"])
            df["is_open"] = 1
            mask = (df["cal_date"] >= start_date) & (df["cal_date"] <= end_date)
            return df[mask].sort_values("cal_date").reset_index(drop=True)
        except Exception as e:
            raise DataProviderError(f"获取交易日历失败: {e}", provider=self.name, original=e)