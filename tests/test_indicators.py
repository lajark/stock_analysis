"""技术指标单元测试。"""

import numpy as np
import pandas as pd
import pytest

from src.analysis.indicators import (
    calc_all_indicators,
    calc_bollinger,
    calc_cci,
    calc_ema,
    calc_kdj,
    calc_ma,
    calc_macd,
    calc_obv,
    calc_rsi,
    calc_volume_ratio,
    calc_williams_r,
    summarize_indicators,
)


class TestMA:
    def test_default_periods(self, sample_ohlc):
        result = calc_ma(sample_ohlc)
        assert "ma_5" in result.columns
        assert "ma_10" in result.columns
        assert "ma_20" in result.columns
        assert "ma_60" in result.columns

    def test_custom_periods(self, sample_ohlc):
        result = calc_ma(sample_ohlc, periods=[3, 7])
        assert "ma_3" in result.columns
        assert "ma_7" in result.columns
        assert "ma_5" not in result.columns

    def test_ma_values(self, sample_ohlc):
        result = calc_ma(sample_ohlc)
        # 最后一个值应该接近最近 5 天的平均值
        manual_ma5 = sample_ohlc["close"].iloc[-5:].mean()
        assert abs(result["ma_5"].iloc[-1] - manual_ma5) < 0.01

    def test_ma_nan_at_start(self, sample_ohlc):
        result = calc_ma(sample_ohlc)
        assert pd.isna(result["ma_20"].iloc[0])
        assert pd.isna(result["ma_20"].iloc[18])
        assert not pd.isna(result["ma_20"].iloc[19])


class TestRSI:
    def test_basic(self, sample_ohlc):
        result = calc_rsi(sample_ohlc)
        assert "rsi" in result.columns
        assert 0 <= result["rsi"].iloc[-1] <= 100

    def test_range(self, sample_ohlc):
        result = calc_rsi(sample_ohlc)
        valid = result["rsi"].dropna()
        assert valid.min() >= 0
        assert valid.max() <= 100


class TestMACD:
    def test_columns(self, sample_ohlc):
        result = calc_macd(sample_ohlc)
        assert "macd" in result.columns
        assert "macd_signal" in result.columns
        assert "macd_histogram" in result.columns

    def test_histogram(self, sample_ohlc):
        result = calc_macd(sample_ohlc)
        last = result.iloc[-1]
        diff = abs(last["macd_histogram"] - (last["macd"] - last["macd_signal"]))
        assert diff < 0.01


class TestKDJ:
    def test_range(self, sample_ohlc):
        result = calc_kdj(sample_ohlc)
        valid = result[["kdj_k", "kdj_d"]].dropna()
        assert valid["kdj_k"].min() >= 0
        assert valid["kdj_k"].max() <= 100
        assert valid["kdj_d"].min() >= 0
        assert valid["kdj_d"].max() <= 100

    def test_columns(self, sample_ohlc):
        result = calc_kdj(sample_ohlc)
        assert "kdj_k" in result.columns
        assert "kdj_d" in result.columns
        assert "kdj_j" in result.columns


class TestBollinger:
    def test_bands(self, sample_ohlc):
        result = calc_bollinger(sample_ohlc)
        last = result.iloc[-1]
        assert last["bb_lower"] < last["bb_middle"] < last["bb_upper"]
        assert last["bb_percent"] >= 0

    def test_width(self, sample_ohlc):
        result = calc_bollinger(sample_ohlc)
        assert "bb_width" in result.columns
        assert result["bb_width"].dropna().min() >= 0


class TestOBV:
    def test_basic(self, sample_ohlc):
        result = calc_obv(sample_ohlc)
        assert "obv" in result.columns
        assert "obv_ma" in result.columns
        assert not pd.isna(result["obv"].iloc[0])


class TestSummarize:
    def test_returns_dict(self, sample_ohlc):
        df = calc_all_indicators(sample_ohlc)
        summary = summarize_indicators(df)
        assert isinstance(summary, dict)
        assert "close" in summary
        assert "trend" in summary
        assert "macd_status" in summary
        assert "rsi" in summary
        assert "rsi_status" in summary

    def test_empty_df(self):
        result = summarize_indicators(pd.DataFrame())
        assert result == {}

    def test_trend_detection(self):
        """验证趋势检测对不同趋势有不同判断。"""
        # 确定性上升：每天 +1
        dates_up = pd.date_range("2026-01-01", periods=60, freq="B")
        up = pd.DataFrame({
            "trade_date": dates_up,
            "open": np.arange(100, 160),
            "high": np.arange(101, 161),
            "low": np.arange(99, 159),
            "close": np.arange(100.5, 160.5),
            "volume": np.full(60, 100000),
            "amount": np.full(60, 10000000),
        })
        # 确定性下降：每天 -1
        down = pd.DataFrame({
            "trade_date": dates_up,
            "open": np.arange(160, 100, -1),
            "high": np.arange(161, 101, -1),
            "low": np.arange(159, 99, -1),
            "close": np.arange(160.5, 100.5, -1),
            "volume": np.full(60, 100000),
            "amount": np.full(60, 10000000),
        })

        df_up = calc_all_indicators(up)
        summary_up = summarize_indicators(df_up)
        df_down = calc_all_indicators(down)
        summary_down = summarize_indicators(df_down)

        assert summary_up["trend"] == "上升"
        assert summary_down["trend"] == "下降"
        assert summary_up["close"] > summary_down["close"]