"""测试 fixtures 和共享配置。"""

import numpy as np
import pandas as pd
import pytest


@pytest.fixture
def sample_ohlc() -> pd.DataFrame:
    """生成模拟 OHLC 数据（60 个交易日，上升趋势）。"""
    np.random.seed(42)
    dates = pd.date_range("2026-01-01", periods=60, freq="B")
    close = 100 + np.cumsum(np.random.randn(60) * 2)
    close = np.maximum(close, 50)

    df = pd.DataFrame({
        "trade_date": dates,
        "open": close - np.random.rand(60) * 2,
        "high": close + np.random.rand(60) * 3,
        "low": close - np.random.rand(60) * 3,
        "close": close,
        "volume": np.random.randint(10000, 100000, 60) * 100,
        "amount": np.random.randint(50000, 500000, 60) * 1000,
    })
    return df


@pytest.fixture
def sample_ohlc_downtrend() -> pd.DataFrame:
    """生成模拟 OHLC 数据（下降趋势）。"""
    np.random.seed(99)
    dates = pd.date_range("2026-01-01", periods=60, freq="B")
    close = 100 - np.cumsum(np.random.randn(60) * 2)
    close = np.maximum(close, 30)

    df = pd.DataFrame({
        "trade_date": dates,
        "open": close + np.random.rand(60) * 2,
        "high": close + np.random.rand(60) * 3,
        "low": close - np.random.rand(60) * 3,
        "close": close,
        "volume": np.random.randint(10000, 100000, 60) * 100,
        "amount": np.random.randint(50000, 500000, 60) * 1000,
    })
    return df


@pytest.fixture
def sample_income() -> pd.DataFrame:
    """生成模拟利润表数据。"""
    return pd.DataFrame({
        "end_date": pd.to_datetime([
            "2024-03-31", "2024-06-30", "2024-09-30", "2024-12-31",
            "2025-03-31", "2025-06-30", "2025-09-30", "2025-12-31",
        ]),
        "revenue": [500, 600, 550, 700, 550, 680, 620, 780],
        "operating_profit": [200, 250, 220, 280, 210, 260, 240, 300],
        "net_profit": [150, 180, 160, 200, 155, 190, 170, 210],
        "eps": [1.5, 1.8, 1.6, 2.0, 1.55, 1.9, 1.7, 2.1],
    })


@pytest.fixture
def sample_balance() -> pd.DataFrame:
    """生成模拟资产负债表。"""
    return pd.DataFrame({
        "end_date": pd.to_datetime(["2024-12-31", "2025-12-31"]),
        "total_assets": [5000, 5600],
        "total_liabilities": [1500, 1600],
        "shareholders_equity": [3500, 4000],
    })


@pytest.fixture
def sample_cashflow() -> pd.DataFrame:
    """生成模拟现金流量表。"""
    return pd.DataFrame({
        "end_date": pd.to_datetime(["2024-12-31", "2025-12-31"]),
        "operating_cf": [180, 200],
        "investing_cf": [-80, -100],
        "financing_cf": [-30, -20],
    })


@pytest.fixture
def sample_fina_indicator() -> pd.DataFrame:
    """生成模拟财务指标。"""
    return pd.DataFrame({
        "end_date": pd.to_datetime(["2024-12-31", "2025-12-31"]),
        "roe": [22.5, 24.0],
        "roa": [12.0, 13.5],
        "gross_margin": [65.0, 66.5],
        "net_margin": [28.0, 27.0],
        "debt_to_assets": [30.0, 28.5],
        "current_ratio": [2.5, 2.8],
        "quick_ratio": [1.8, 2.0],
    })


@pytest.fixture
def sample_stock_info() -> dict:
    return {
        "code": "600519.SH",
        "name": "测试股票",
        "industry": "白酒",
        "area": "贵州",
        "list_date": "20010101",
        "market": "主板",
    }