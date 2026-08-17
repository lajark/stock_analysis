"""真实 provider 契约测试（opt-in，默认跳过）。

验证 Tushare/AkShare 公开接口的实际返回契约，与
``src/data/providers/tushare.py`` / akshare provider 的字段假设对齐。

门控：全部标记 ``integration`` + ``network``；Tushare 用例在未配置
``TUSHARE_TOKEN``（经生产 ``get_config()`` 从 .env 读取，不回显）时跳过。
AkShare 本无需 token，但沿用同一开关作为"运行真实 provider 测试"的统一
opt-in 开关（默认套件与 CI 均不执行，避免不稳定网络破坏离线基线）。
"""

from __future__ import annotations

import pandas as pd
import pytest
import tushare as ts

from src.config import get_config

pytestmark = [
    pytest.mark.integration,
    pytest.mark.network,
    pytest.mark.skipif(
        not get_config().tushare_token,
        reason="需要配置 TUSHARE_TOKEN（.env，经生产 get_config 读取）以运行真实 provider 契约测试",
    ),
]


def test_tushare_daily_contract_matches_provider_assumptions() -> None:
    """get_daily 的 rename/parse 假设：vol 列、%Y%m%d 日期、必需列。"""
    pro = ts.pro_api(get_config().tushare_token)
    df = pro.daily(ts_code="600519.SH", start_date="20260101", end_date="20260131")
    assert not df.empty
    required = {"ts_code", "trade_date", "open", "high", "low", "close", "vol", "amount"}
    assert required <= set(df.columns)
    # provider 侧 df["trade_date"].astype 后 to_datetime(format="%Y%m%d")
    parsed = pd.to_datetime(df["trade_date"], format="%Y%m%d")
    assert parsed.notna().all()


def test_tushare_stock_basic_contract_matches_provider_request() -> None:
    """get_stock_basic 请求的字段集必须全部返回且 name 非空。"""
    pro = ts.pro_api(get_config().tushare_token)
    df = pro.stock_basic(
        ts_code="600519.SH",
        fields="ts_code,name,industry,area,list_date,market",
    )
    assert not df.empty
    assert {"ts_code", "name", "industry", "area", "list_date", "market"} <= set(df.columns)
    assert df.iloc[0]["name"]


def test_akshare_hist_contract_matches_provider_columns() -> None:
    """AkShare 中文列名假设：日期/开盘/收盘/最高/最低/成交量。"""
    ak = pytest.importorskip("akshare")
    df = ak.stock_zh_a_hist(
        symbol="600519",
        period="daily",
        start_date="20260101",
        end_date="20260131",
        adjust="",
    )
    assert not df.empty
    for column in ("日期", "开盘", "收盘", "最高", "最低", "成交量"):
        assert column in df.columns
