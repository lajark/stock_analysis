"""交易日历 — 基于 Tushare trade_cal 接口。

提供交易日判断、最近交易日查询、交易日区间生成等功能。
"""

from datetime import datetime, timedelta

import pandas as pd

from src.data.providers.tushare import TushareProvider


class TradingCalendar:
    """A 股交易日历。

    数据来源：Tushare trade_cal 接口。
    自动缓存已查询的交易日历数据。
    """

    def __init__(self):
        self._provider = TushareProvider()
        self._cache: dict[str, pd.DataFrame] = {}  # key: exchange:start:end

    def _get_calendar(
        self, exchange: str, start_date: str, end_date: str
    ) -> pd.DataFrame:
        """获取交易日历（带缓存）。"""
        cache_key = f"{exchange}:{start_date}:{end_date}"
        if cache_key not in self._cache:
            self._cache[cache_key] = self._provider.get_trade_cal(
                exchange, start_date, end_date
            )
        return self._cache[cache_key]

    def is_trading_day(self, date: str, exchange: str = "SSE") -> bool:
        """判断是否为交易日。"""
        cal = self._get_calendar(exchange, date, date)
        if cal.empty:
            return False
        row = cal[cal["cal_date"] == pd.Timestamp(date)]
        if row.empty:
            return False
        return bool(row.iloc[0]["is_open"])

    def next_trading_day(
        self, date: str, exchange: str = "SSE", n: int = 1
    ) -> str | None:
        """获取后续第 n 个交易日。

        Args:
            date: 基准日期
            exchange: 交易所
            n: 向前第 n 个交易日（>0）

        Returns:
            日期字符串 YYYY-MM-DD，或 None
        """
        end = datetime.strptime(date, "%Y-%m-%d") + timedelta(days=30 + n * 7)
        cal = self._get_calendar(exchange, date, end.strftime("%Y-%m-%d"))
        open_days = cal[cal["is_open"] == 1].copy()
        open_days = open_days[open_days["cal_date"] > pd.Timestamp(date)]
        open_days = open_days.sort_values("cal_date")

        if len(open_days) < n:
            return None
        return open_days.iloc[n - 1]["cal_date"].strftime("%Y-%m-%d")

    def prev_trading_day(
        self, date: str, exchange: str = "SSE", n: int = 1
    ) -> str | None:
        """获取之前第 n 个交易日。"""
        start = datetime.strptime(date, "%Y-%m-%d") - timedelta(days=30 + n * 7)
        cal = self._get_calendar(exchange, start.strftime("%Y-%m-%d"), date)
        open_days = cal[cal["is_open"] == 1].copy()
        open_days = open_days[open_days["cal_date"] < pd.Timestamp(date)]
        open_days = open_days.sort_values("cal_date", ascending=False)

        if len(open_days) < n:
            return None
        return open_days.iloc[n - 1]["cal_date"].strftime("%Y-%m-%d")

    def latest_trading_day(
        self, exchange: str = "SSE"
    ) -> str:
        """获取最近一个交易日。"""
        today = datetime.now().strftime("%Y-%m-%d")
        if self.is_trading_day(today, exchange):
            return today
        prev = self.prev_trading_day(today, exchange, n=1)
        return prev or today

    def trading_days_between(
        self, start_date: str, end_date: str, exchange: str = "SSE"
    ) -> list[str]:
        """获取区间内的所有交易日。"""
        cal = self._get_calendar(exchange, start_date, end_date)
        open_days = cal[cal["is_open"] == 1]
        open_days = open_days[
            (open_days["cal_date"] >= pd.Timestamp(start_date))
            & (open_days["cal_date"] <= pd.Timestamp(end_date))
        ]
        return [d.strftime("%Y-%m-%d") for d in open_days["cal_date"].sort_values()]

    def need_update(
        self, last_data_date: str, exchange: str = "SSE"
    ) -> bool:
        """判断数据是否需要更新（与最近交易日比较）。"""
        latest = self.latest_trading_day(exchange)
        return latest > last_data_date
