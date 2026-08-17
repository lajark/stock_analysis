"""T-7.11 Phase 6-GUI 流式预览契约测试：LLMClient.generate_stream 与服务层接线。

覆盖：流式增量顺序产出与 usage 捕获、逐 chunk 取消（流关闭）、BadRequest
兜底（无 stream_options 重试）、无 usage 时诚实置 None；服务层 token_callback
流式路径、流中取消（AnalysisCancelledError + 无产物 + cancelled 记录）以及
无 token_callback 时仍走非流式 generate（CLI/批量路径保护）。全程本地假数据，
无真实网络/LLM。

客户端 stub 采用 ``object.__new__`` 注入假 OpenAI SDK 对象；服务层接线通过
轻量 DI 注入 ``gateway`` / ``llm_factory`` 参数（ROADMAP L226），不再依赖
import-site monkeypatch。
"""

from __future__ import annotations

import threading
from types import SimpleNamespace

import httpx
import pandas as pd
import pytest
from openai import BadRequestError

from src.app.run_records import RunRecordStore
from src.app.service import (
    AnalysisCancelledError,
    AnalysisRequest,
    analyze_stock,
)
from src.data.gateway import StockDataBundle
from src.reports.llm_client import LLMClient, LLMStreamCancelledError


# ---------------------------------------------------------------------------
# Fake OpenAI SDK objects (stream chunk shape mirrors the openai client).
# ---------------------------------------------------------------------------
class _Delta:
    def __init__(self, content: str) -> None:
        self.content = content


class _Choice:
    def __init__(self, content: str) -> None:
        self.delta = _Delta(content)


class _Usage:
    def __init__(self, prompt: int, completion: int, total: int) -> None:
        self.prompt_tokens = prompt
        self.completion_tokens = completion
        self.total_tokens = total


class _Chunk:
    def __init__(self, content: str = "", usage: _Usage | None = None) -> None:
        self.choices = ([_Choice(content)] if content else [])
        self.usage = usage


class _Stream:
    """Iterable stand-in for openai's Stream; records close()."""

    def __init__(self, chunks: list[_Chunk]) -> None:
        self._chunks = chunks
        self.closed = False

    def __iter__(self):
        return iter(self._chunks)

    def close(self) -> None:
        self.closed = True


class _Completions:
    def __init__(self, stream: _Stream, *, fail_once: bool = False) -> None:
        self._stream = stream
        self._fail_once = fail_once
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self._fail_once and "stream_options" in kwargs:
            self._fail_once = False
            response = httpx.Response(
                400,
                request=httpx.Request("POST", "https://api.example.test/v1"),
            )
            raise BadRequestError("stream_options not supported", response=response, body=None)
        return self._stream


class _Chat:
    def __init__(self, completions: _Completions) -> None:
        self.completions = completions


class _ApiClient:
    def __init__(self, completions: _Completions) -> None:
        self.chat = _Chat(completions)


def _make_client(api: _ApiClient) -> LLMClient:
    client = object.__new__(LLMClient)
    client._client = api
    client._model = "test-model"
    client._model_deep = "test-deep"
    client._max_tokens = 100
    client._temperature = 0.2
    client._last_usage = None
    return client


# ---------------------------------------------------------------------------
# LLMClient.generate_stream unit tests (unchanged — no DI needed here).
# ---------------------------------------------------------------------------
def test_generate_stream_yields_deltas_and_captures_usage() -> None:
    stream = _Stream(
        [
            _Chunk(content="你好"),
            _Chunk(content="，世界"),
            _Chunk(usage=_Usage(10, 20, 30)),
            _Chunk(content=""),
        ]
    )
    api = _ApiClient(_Completions(stream))
    client = _make_client(api)

    parts = list(client.generate_stream("sys", "user"))
    assert parts == ["你好", "，世界"]
    assert client.last_usage == {
        "model": "test-model",
        "input_tokens": 10,
        "output_tokens": 20,
        "total_tokens": 30,
    }
    assert "stream_options" in api.chat.completions.calls[0]
    assert stream.closed is True


def test_generate_stream_cancel_raises_and_closes_stream() -> None:
    stream = _Stream([_Chunk(content="A"), _Chunk(content="B"), _Chunk(content="C")])
    api = _ApiClient(_Completions(stream))
    client = _make_client(api)
    cancel = threading.Event()

    gen = client.generate_stream("sys", "user", cancel_event=cancel)
    assert next(gen) == "A"
    cancel.set()
    with pytest.raises(LLMStreamCancelledError):
        next(gen)
    assert stream.closed is True


def test_generate_stream_badrequest_falls_back_to_plain_stream() -> None:
    stream = _Stream([_Chunk(content="A"), _Chunk(content="B")])
    api = _ApiClient(_Completions(stream, fail_once=True))
    client = _make_client(api)

    assert list(client.generate_stream("sys", "user")) == ["A", "B"]
    # The retry keeps streaming alive but drops stream_options entirely.
    assert len(api.chat.completions.calls) == 2
    assert "stream_options" in api.chat.completions.calls[0]
    assert "stream_options" not in api.chat.completions.calls[1]
    assert stream.closed is True


def test_generate_stream_without_provider_usage_stays_none() -> None:
    stream = _Stream([_Chunk(content="A"), _Chunk(content="B")])
    api = _ApiClient(_Completions(stream))
    client = _make_client(api)

    assert list(client.generate_stream("sys", "user")) == ["A", "B"]
    assert client.last_usage is None


# ---------------------------------------------------------------------------
# Service-layer streaming wiring (real _create_llm_report, stubbed LLM/render,
# gateway injected via lightweight DI).
# ---------------------------------------------------------------------------
def _daily() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "trade_date": pd.to_datetime(["2026-08-13", "2026-08-14"]),
            "open": [10.0, 10.5],
            "high": [11.0, 11.5],
            "low": [9.5, 10.0],
            "close": [10.5, 11.0],
            "volume": [100.0, 120.0],
        }
    )


def _bundle() -> StockDataBundle:
    return StockDataBundle(
        stock_info={"code": "600519.SH", "name": "测试股票"},
        daily=_daily(),
        daily_basic={},
        income=pd.DataFrame(),
        balance_sheet=pd.DataFrame(),
        cashflow=pd.DataFrame(),
        fina_indicator=pd.DataFrame(),
        providers={"stock_info": "tushare", "daily": "tushare"},
        quality={"stock_info": "ok", "daily": "ok"},
    )


class _FakeGateway:
    """Drop-in DataGateway returning a fixed bundle."""

    def __init__(self, bundle: StockDataBundle) -> None:
        self._bundle = bundle

    def fetch(self, *args, **kwargs) -> StockDataBundle:
        return self._bundle


def _patch_harness(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    bundle: StockDataBundle,
) -> tuple[RunRecordStore, _FakeGateway]:
    store = RunRecordStore(tmp_path / "run_records.jsonl")
    monkeypatch.setattr("src.app.service.RunRecordStore", lambda: store)
    monkeypatch.setattr(
        "src.app.service.validate_request",
        lambda request: AnalysisRequest(
            ticker="600519.SH",
            mode=request.mode,
            date="2026-08-14",
            use_llm=request.use_llm,
            chart=False,
        ),
    )
    monkeypatch.setattr(
        "src.app.service.get_config",
        lambda: SimpleNamespace(
            json_dir=tmp_path / "json",
            output_dir=tmp_path / "output",
            llm_model="test-model",
        ),
    )
    monkeypatch.setattr(
        "src.analysis.package.build_analysis_package",
        lambda **kwargs: {
            "schema_version": "1.0",
            "quality": "ok",
            "data_gaps": [],
        },
    )
    monkeypatch.setattr(
        "src.app.service._route_context",
        lambda mode, package: {
            "router_version": "test-router",
            "dimensions": [],
            "fragments": [],
            "content_hash": "h",
            "char_count": 0,
            "prompt_text": None,
        },
    )
    monkeypatch.setattr("src.app.service._load_system_prompt", lambda mode: "系统提示")
    # Return a (store, gateway) pair; tests inject the gateway via the
    # lightweight-DI ``gateway=`` parameter on ``analyze_stock``.
    return store, _FakeGateway(bundle)


class _FakeLLM:
    """Drop-in LLMClient replacement: streams text, honours cancel_event."""

    def __init__(self, chunks: list[str], *, auto_cancel_after: int | None = None) -> None:
        self._chunks = chunks
        self._auto_cancel_after = auto_cancel_after
        self.last_usage = {
            "model": "fake-model",
            "input_tokens": 1,
            "output_tokens": 2,
            "total_tokens": 3,
        }
        self.stream_calls = 0
        self.generate_calls = 0

    def generate(self, system_prompt: str, user_prompt: str, *, deep: bool = False) -> str:
        self.generate_calls += 1
        return "".join(self._chunks)

    def generate_stream(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        deep: bool = False,
        cancel_event: threading.Event | None = None,
    ):
        self.stream_calls += 1
        for index, chunk in enumerate(self._chunks):
            if cancel_event is not None and cancel_event.is_set():
                raise LLMStreamCancelledError("LLM 流式生成已由用户取消")
            if self._auto_cancel_after == index:
                assert cancel_event is not None
                cancel_event.set()
            yield chunk


def _patch_render(
    monkeypatch: pytest.MonkeyPatch, tmp_path, captured: dict
) -> None:
    def fake_render(package, llm_output, llm_model, tokens, output_path=None, run_id=None):
        captured["llm_output"] = llm_output
        captured["tokens"] = tokens
        report_path = tmp_path / "report.md"
        report_path.write_text(llm_output, encoding="utf-8")
        return str(report_path)

    monkeypatch.setattr("src.reports.renderer.render_report", fake_render)


def test_service_streaming_delivers_all_deltas_and_full_report(
    monkeypatch: pytest.MonkeyPatch, tmp_path,
) -> None:
    _, gateway = _patch_harness(monkeypatch, tmp_path, _bundle())
    captured: dict = {}
    _patch_render(monkeypatch, tmp_path, captured)
    received: list[str] = []

    result = analyze_stock(
        AnalysisRequest(ticker="600519", use_llm=True),
        token_callback=received.append,
        llm_factory=lambda: _FakeLLM(["第一部分", "第二部分", "第三部分"]),
        gateway=gateway,
    )

    assert received == ["第一部分", "第二部分", "第三部分"]
    assert result.output_kind == "report"
    assert captured["llm_output"] == "第一部分第二部分第三部分"
    assert (tmp_path / "report.md").read_text(encoding="utf-8") == captured["llm_output"]
    assert captured["tokens"]["model"] == "fake-model"
    record = _store_list(tmp_path)[0]
    assert record["outcome"]["status"] == "success"
    assert record["stages"]["generate_report"]["llm_used"] is True


def test_service_streaming_cancel_mid_stream_leaves_no_artifacts(
    monkeypatch: pytest.MonkeyPatch, tmp_path,
) -> None:
    _, gateway = _patch_harness(monkeypatch, tmp_path, _bundle())
    captured: dict = {}
    _patch_render(monkeypatch, tmp_path, captured)
    received: list[str] = []
    cancel = threading.Event()

    with pytest.raises(AnalysisCancelledError):
        analyze_stock(
            AnalysisRequest(ticker="600519", use_llm=True),
            token_callback=received.append,
            cancel_event=cancel,
            llm_factory=lambda: _FakeLLM(["A", "B", "C"], auto_cancel_after=1),
            gateway=gateway,
        )

    # Deltas delivered up to the cancellation point; the report was never rendered.
    assert received == ["A", "B"]
    assert "llm_output" not in captured
    for junk in (tmp_path / "json", tmp_path / "output", tmp_path / "report.md"):
        assert not junk.exists() or not any(junk.iterdir()), f"partial artifact {junk}"
    record = _store_list(tmp_path)[0]
    assert record["outcome"]["status"] == "cancelled"


def test_service_without_token_callback_uses_non_streaming_generate(
    monkeypatch: pytest.MonkeyPatch, tmp_path,
) -> None:
    _, gateway = _patch_harness(monkeypatch, tmp_path, _bundle())
    fake = _FakeLLM(["[non-stream 报告]"])
    captured: dict = {}
    _patch_render(monkeypatch, tmp_path, captured)

    result = analyze_stock(
        AnalysisRequest(ticker="600519", use_llm=True),
        llm_factory=lambda: fake,
        gateway=gateway,
    )

    assert fake.stream_calls == 0
    assert fake.generate_calls == 1
    assert captured["llm_output"] == "[non-stream 报告]"
    assert result.output_kind == "report"


def _store_list(tmp_path):
    store = RunRecordStore(tmp_path / "run_records.jsonl")
    return store.list()
