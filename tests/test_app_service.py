"""Tests for desktop/CLI shared application input handling."""

from types import SimpleNamespace

import pytest

from src.app.service import AnalysisRequest, validate_request, validate_ticker
from src.errors import ConfigError, StockAnalysisError


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # --- 沪市 ---
        ("600519", "600519.SH"),
        ("688981.sh", "688981.SH"),
        ("689009", "689009.SH"),  # 科创板 CDR
        ("510300", "510300.SH"),
        ("900901", "900901.SH"),  # 沪市 B 股
        ("919999", "919999.SH"),  # 9 开头与 920 段分界（非 920）
        # --- 深市 ---
        ("000858", "000858.SZ"),
        ("002001", "002001.SZ"),
        ("003816", "003816.SZ"),
        ("300750", "300750.SZ"),
        ("159919", "159919.SZ"),
        ("200596", "200596.SZ"),  # 深市 B 股
        # --- 北交所：4 / 8 / 920 三个代码段 ---
        ("430047", "430047.BJ"),
        ("430759", "430759.BJ"),
        ("400001", "400001.BJ"),
        ("832566", "832566.BJ"),
        ("871981", "871981.BJ"),
        ("920001", "920001.BJ"),
        ("920000", "920000.BJ"),  # 920 段下界
        ("920999", "920999.BJ"),  # 920 段上界
        # --- 北交所：显式后缀与大小写/空白归一 ---
        ("430047.BJ", "430047.BJ"),
        ("832566.bj", "832566.BJ"),
        ("920001.Bj", "920001.BJ"),
        (" 430047 ", "430047.BJ"),
        (" 920001.BJ ", "920001.BJ"),
    ],
)
def test_validate_ticker_normalizes_codes(raw: str, expected: str) -> None:
    assert validate_ticker(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        # --- 基本格式非法 ---
        "",
        "60051",
        "ABCDEF",
        "600519.XX",
        # --- 沪市/深市代码误标北交所后缀 ---
        "600519.BJ",  # 600xxx 属于沪市
        "002001.BJ",  # 002xxx 属于深市
        "300750.BJ",  # 创业板属于深市
        # --- 北交所代码误标其他交易所后缀 ---
        "430047.SH",
        "430047.SZ",
        "832566.SH",
        "832566.SZ",
        "920001.SH",
        "920001.SZ",
        # --- 北交所代码的残缺/畸形输入 ---
        "43004",  # 5 位
        "0430047",  # 7 位（前导零超长）
        "430047X",  # 非纯数字
        "430047.BB",  # 未知后缀
        "430047 .BJ",  # 数字与后缀间有空格
        "4.30047",  # 分隔符错误
    ],
)
def test_validate_ticker_rejects_invalid_codes(raw: str) -> None:
    with pytest.raises(StockAnalysisError):
        validate_ticker(raw)


def test_validate_request_allows_local_analysis_without_llm_key(monkeypatch) -> None:
    config = SimpleNamespace(
        tushare_token="valid-token",
        llm_api_key="",
        llm_base_url="https://example.com/v1",
        llm_model="model",
    )
    monkeypatch.setattr("src.app.service.get_config", lambda: config)

    result = validate_request(AnalysisRequest(ticker="600519", use_llm=False))

    assert result.ticker == "600519.SH"
    assert result.date is not None


def test_validate_request_requires_llm_key_when_enabled(monkeypatch) -> None:
    config = SimpleNamespace(
        tushare_token="valid-token",
        llm_api_key="",
        llm_base_url="https://example.com/v1",
        llm_model="model",
    )
    monkeypatch.setattr("src.app.service.get_config", lambda: config)

    with pytest.raises(ConfigError, match="LLM API Key"):
        validate_request(AnalysisRequest(ticker="600519", use_llm=True))
