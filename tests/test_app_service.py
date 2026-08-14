"""Tests for desktop/CLI shared application input handling."""

from types import SimpleNamespace

import pytest

from src.app.service import AnalysisRequest, validate_request, validate_ticker
from src.errors import ConfigError, StockAnalysisError


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("600519", "600519.SH"),
        ("000858", "000858.SZ"),
        ("688981.sh", "688981.SH"),
    ],
)
def test_validate_ticker_normalizes_codes(raw: str, expected: str) -> None:
    assert validate_ticker(raw) == expected


@pytest.mark.parametrize("raw", ["", "60051", "ABCDEF", "600519.BJ"])
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
