"""数据源抽象基类 — 定义所有数据源必须实现的统一接口。"""

from abc import ABC, abstractmethod
from typing import Any

import pandas as pd


class DataProviderError(Exception):
    """数据源错误基类。"""

    def __init__(self, message: str, provider: str = "", original: Exception | None = None):
        self.provider = provider
        self.original = original
        super().__init__(message)


class DataProvider(ABC):
    """数据源抽象基类。

    所有数据源（Tushare、AkShare 等）必须实现此接口。
    上层调用方不感知具体数据源，通过 Provider Factory 获取实例。
    """

    def __init__(self, name: str):
        self.name = name

    @abstractmethod
    def get_stock_basic(self, code: str) -> dict[str, Any]:
        """获取股票基本信息。

        Returns:
            dict with keys: code, name, industry, area, list_date, market
        """
        ...

    @abstractmethod
    def get_daily(
        self, code: str, start_date: str, end_date: str
    ) -> pd.DataFrame:
        """获取日线行情（未复权）。

        Returns:
            DataFrame columns: trade_date, open, high, low, close, volume, amount
        """
        ...

    @abstractmethod
    def get_adj_factor(
        self, code: str, start_date: str, end_date: str
    ) -> pd.DataFrame:
        """获取复权因子。

        Returns:
            DataFrame columns: trade_date, adj_factor
        """
        ...

    @abstractmethod
    def get_daily_basic(self, code: str, trade_date: str) -> dict[str, Any]:
        """获取每日指标（PE/PB/PS/总市值/流通市值等）。

        Returns:
            dict with keys: pe_ttm, pb, ps_ttm, total_mv, circ_mv
        """
        ...

    def get_moneyflow(
        self, code: str, start_date: str, end_date: str
    ) -> pd.DataFrame:
        """获取历史资金流（可选能力，缺失时返回空表）。

        资金流不是所有降级数据源都提供；使用非抽象默认实现可让网关
        显式记录 ``partial``，而不是破坏行情和基本面主链路。
        """
        return pd.DataFrame()

    @abstractmethod
    def get_income(
        self, code: str, start_date: str, end_date: str
    ) -> pd.DataFrame:
        """获取利润表。

        Returns:
            DataFrame columns include end_date plus optional ann_date, f_ann_date,
            report_type, comp_type and update_flag; business fields are revenue,
            operating_profit, net_profit, optional net_profit_attributable, and eps.
        """
        ...

    @abstractmethod
    def get_balance_sheet(
        self, code: str, start_date: str, end_date: str
    ) -> pd.DataFrame:
        """获取资产负债表。

        Returns:
            DataFrame columns include end_date plus optional ann_date, f_ann_date,
            report_type, comp_type and update_flag; business fields are total_assets,
            total_liabilities and shareholders_equity.
        """
        ...

    @abstractmethod
    def get_cashflow(
        self, code: str, start_date: str, end_date: str
    ) -> pd.DataFrame:
        """获取现金流量表。

        Returns:
            DataFrame columns include end_date plus optional ann_date, f_ann_date,
            report_type, comp_type and update_flag; business fields are operating_cf,
            investing_cf and financing_cf.
        """
        ...

    @abstractmethod
    def get_fina_indicator(
        self, code: str, start_date: str, end_date: str
    ) -> pd.DataFrame:
        """获取财务指标（ROE/ROA/毛利率/净利率/资产负债率/流动比率等）。

        Returns:
            DataFrame columns include end_date, ann_date and update_flag; business
            fields are roe, roa, gross_margin, net_margin, debt_to_assets,
            current_ratio and quick_ratio.
        """
        ...

    @abstractmethod
    def get_trade_cal(
        self, exchange: str, start_date: str, end_date: str
    ) -> pd.DataFrame:
        """获取交易日历。

        Returns:
            DataFrame columns: cal_date, is_open, pretrade_date
        """
        ...

    def health_check(self) -> bool:
        """检查数据源可用性。"""
        try:
            self.get_trade_cal("SSE", "20260101", "20260110")
            return True
        except Exception:
            return False
