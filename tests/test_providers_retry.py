"""Provider 失败恢复测试 — 重试/退避分类，无网络。

用 ``object.__new__(TushareProvider)`` 绕过 ``__init__``（避免 token/ts.pro_api），
注入 FakePro 记录调用并可配置抛出异常；patch time.sleep 到列表以验退避序列。
覆盖 ``TushareProvider._request`` / ``_is_retryable`` 的契约：网络/限频类瞬时
故障退避重试，权限/积分类永久失败快速失败。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.data.providers.base import DataProviderError
from src.data.providers.tushare import TushareProvider


class FakePro:
    """Tushare SDK 替身：记录调用，按列表依次抛错，None 表示成功。"""

    def __init__(self, errors: list[Exception | None]) -> None:
        self._errors = list(errors)
        self.calls: list[tuple[tuple, dict]] = []

    def daily(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        err = self._errors.pop(0) if self._errors else None
        if err is not None:
            raise err
        return "ok"


def _make_provider(pro: FakePro, retry_count: int = 3, retry_delay: float = 1.0):
    provider = object.__new__(TushareProvider)
    provider.name = "tushare"
    provider._pro = pro
    provider._retry_count = retry_count
    provider._retry_delay = retry_delay
    return provider


@pytest.fixture
def no_rate_limit(monkeypatch) -> None:
    """Keep the module-level rate limiter out of the retry math."""
    monkeypatch.setattr(
        "src.data.providers.tushare.get_config",
        lambda: SimpleNamespace(batch=SimpleNamespace(request_min_interval_s=0.0)),
    )
    monkeypatch.setattr("src.data.providers.tushare._rate_limiter", None)


@pytest.fixture
def sleeps(monkeypatch) -> list[float]:
    recorded: list[float] = []
    monkeypatch.setattr(
        "src.data.providers.tushare.time.sleep", recorded.append
    )
    return recorded


def test_retryable_network_classes_are_retryable() -> None:
    for exc in (OSError("connection reset"), TimeoutError("timed out"),
                ConnectionError("refused")):
        assert TushareProvider._is_retryable(exc)


def test_permanent_permission_errors_are_not_retryable() -> None:
    assert not TushareProvider._is_retryable(Exception("积分不足"))
    assert not TushareProvider._is_retryable(Exception("没有权限"))
    assert not TushareProvider._is_retryable(Exception("该接口没有公开权限"))


def test_message_markers_are_match_case_insensitively() -> None:
    assert TushareProvider._is_retryable(Exception("GET https://api timed out"))
    assert TushareProvider._is_retryable(Exception("Too many requests"))
    assert TushareProvider._is_retryable(Exception("connection reset by peer"))


def test_constant_timeout_retries_then_raises(
    no_rate_limit, sleeps, monkeypatch
) -> None:
    pro = FakePro([TimeoutError("boom")] * 10)
    provider = _make_provider(pro, retry_count=3)
    with pytest.raises(DataProviderError) as exc_info:
        provider._request("daily")
    assert len(pro.calls) == 3
    assert sleeps == [1.0, 2.0]  # 最后一次尝试不 sleep
    assert exc_info.value.provider == "tushare"
    assert "daily" in str(exc_info.value)


def test_backoff_sequence_is_exponential(no_rate_limit, sleeps, monkeypatch) -> None:
    pro = FakePro([TimeoutError("boom")] * 10)
    provider = _make_provider(pro, retry_count=4, retry_delay=1.0)
    with pytest.raises(DataProviderError):
        provider._request("daily")
    assert len(pro.calls) == 4
    assert sleeps == [1.0, 2.0, 4.0]


def test_permanent_error_fails_fast_without_retry(no_rate_limit, sleeps) -> None:
    pro = FakePro([Exception("积分不足")] * 10)
    provider = _make_provider(pro, retry_count=3)
    with pytest.raises(DataProviderError):
        provider._request("daily")
    assert len(pro.calls) == 1
    assert sleeps == []


def test_transient_then_success_stops_retrying(no_rate_limit, sleeps) -> None:
    pro = FakePro([TimeoutError("boom"), None])
    provider = _make_provider(pro, retry_count=3)
    assert provider._request("daily") == "ok"
    assert len(pro.calls) == 2
    assert sleeps == [1.0]
