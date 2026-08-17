"""Integration checks for the service validation gate and run records.

The core e2e tests use direct injection (``gateway`` / ``llm_factory``) instead
of import-site monkeypatch (ROADMAP L226 lightweight DI).
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pandas as pd
import pytest

from src.app.run_records import RunRecordStore
from src.app.service import AnalysisRequest, _safe_error_message, analyze_stock
from src.data.gateway import StockDataBundle
from src.errors import DataValidationError, StockAnalysisError


class FakeGateway:
    """Drop-in DataGateway replacement returning a fixed bundle."""

    def __init__(self, bundle: StockDataBundle) -> None:
        self._bundle = bundle

    def fetch(self, *args, **kwargs) -> StockDataBundle:
        return self._bundle


def _daily(*, invalid: bool = False) -> pd.DataFrame:
    high = [11.0, float("nan")] if invalid else [11.0, 11.5]
    return pd.DataFrame(
        {
            "trade_date": pd.to_datetime(["2026-08-13", "2026-08-14"]),
            "open": [10.0, 10.5],
            "high": high,
            "low": [9.5, 10.0],
            "close": [10.5, 11.0],
            "volume": [100.0, 120.0],
        }
    )


def _bundle(*, invalid: bool = False) -> StockDataBundle:
    return StockDataBundle(
        stock_info={"code": "600519.SH", "name": "测试股票"},
        daily=_daily(invalid=invalid),
        daily_basic={},
        income=pd.DataFrame(),
        balance_sheet=pd.DataFrame(),
        cashflow=pd.DataFrame(),
        fina_indicator=pd.DataFrame(),
        providers={"stock_info": "tushare", "daily": "tushare"},
        quality={"stock_info": "ok", "daily": "ok"},
    )


def _patch_common(
    monkeypatch, tmp_path, bundle: StockDataBundle
) -> tuple[RunRecordStore, FakeGateway]:
    """Patch service-layer dependencies and return a (store, gateway) pair.

    The caller is responsible for passing ``gateway`` to ``analyze_stock``
    via the injection parameter (``gateway=gateway``) — this is the preferred
    lightweight-DI path.  The ``RunRecordStore`` patch is kept because the
    test does not control the ``store`` lifecycle inside ``analyze_stock``.
    """
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
    # Return a (store, gateway) pair; the caller injects the gateway via the
    # lightweight-DI ``gateway=`` parameter on ``analyze_stock``.
    return store, FakeGateway(bundle)


def test_service_blocks_llm_when_validation_fails(monkeypatch, tmp_path) -> None:
    store, gateway = _patch_common(monkeypatch, tmp_path, _bundle(invalid=True))
    called = False

    def fail_if_called(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("LLM must not be called after a validation block")

    monkeypatch.setattr("src.app.service._create_llm_report", fail_if_called)

    with pytest.raises(DataValidationError, match="已阻止报告生成"):
        analyze_stock(AnalysisRequest(ticker="600519", use_llm=True), gateway=gateway)

    assert called is False
    records = store.list()
    assert records[0]["outcome"]["status"] == "failed"
    assert records[0]["stages"]["validate_evidence"]["allow_llm"] is False


def test_service_persists_successful_no_llm_run(monkeypatch, tmp_path) -> None:
    store, gateway = _patch_common(monkeypatch, tmp_path, _bundle())
    monkeypatch.setattr(
        "src.analysis.package.build_analysis_package",
        lambda **kwargs: {
            "schema_version": "1.0",
            "quality": "degraded",
            "data_gaps": ["income"],
            "technical": {},
        },
    )

    result = analyze_stock(AnalysisRequest(ticker="600519", use_llm=False), gateway=gateway)

    assert result.output_kind == "json"
    assert result.output_path.exists()
    assert json.loads(result.output_path.read_text(encoding="utf-8"))["quality"] == "degraded"
    records = store.list()
    assert records[0]["outcome"]["status"] == "success"
    assert records[0]["stages"]["generate_report"]["llm_used"] is False


def test_service_records_context_router_metadata_for_llm(monkeypatch, tmp_path) -> None:
    store, gateway = _patch_common(monkeypatch, tmp_path, _bundle())
    monkeypatch.setattr(
        "src.analysis.package.build_analysis_package",
        lambda **kwargs: {
            "schema_version": "1.0",
            "run_id": "run-package",
            "stock": {"code": "600519.SH"},
            "quality": "ok",
            "data_gaps": [],
            "technical": {"trend": "上升"},
            "risk": {"risk_level": {"label": "中等风险"}},
            "valuation": {"percentiles": {"level": "中等"}},
            "price_levels": {},
        },
    )
    report_path = tmp_path / "report.md"
    captured: dict = {}

    def fake_report(
        package, mode, *, context, run_id=None, token_callback=None, cancel_event=None, **kw
    ):
        captured["context"] = context
        report_path.write_text("# report", encoding="utf-8")
        return report_path, {"model": "test-model", "total_tokens": 3}

    monkeypatch.setattr("src.app.service._create_llm_report", fake_report)

    result = analyze_stock(
        AnalysisRequest(ticker="600519", mode="trade", use_llm=True), gateway=gateway
    )

    assert result.output_kind == "report"
    record = store.list()[0]
    stage = record["stages"]["generate_report"]
    assert stage["context_router_version"] == "context-router-v1"
    expected_ids = [fragment["id"] for fragment in captured["context"]["fragments"]]
    assert stage["context_fragment_ids"] == expected_ids
    assert stage["context_hash"] == captured["context"]["content_hash"]
    assert stage["context_chars"] == captured["context"]["char_count"]


class FakeLLM:
    """Drop-in LLMClient replacement covering the non-streaming path only."""

    def __init__(
        self, *, text: str = "AI 分析结论：测试生成内容", error: Exception | None = None
    ) -> None:
        self._text = text
        self._error = error
        self.last_usage = {"model": "test-model", "input_tokens": 120, "output_tokens": 80}

    def generate(self, system_prompt: str, user_prompt: str, *, deep: bool = False) -> str:
        if self._error is not None:
            raise self._error
        return self._text


def _fake_package() -> dict:
    """Minimal structured package that satisfies the report template fields."""
    return {
        "schema_version": "1.0",
        "run_id": "run-123456",
        "quality": "ok",
        "data_gaps": [],
        "stock": {"name": "测试股票", "code": "600519.SH"},
        "meta": {
            "analysis_date": "2026-08-14",
            "generated_at": "2026-08-14T12:00:00",
            "data_provider": "tushare",
            "data_date": "2026-08-14",
        },
        "technical": {
            "close": 1500.0,
            "trend": "上升",
            "macd_status": "金叉",
            "macd": 1.2,
            "rsi": 55,
            "rsi_status": "中性",
            "kdj_k": 60,
            "kdj_d": 55,
            "kdj_j": 70,
            "kdj_status": "强势",
        },
        "valuation": {"percentiles": {"price_percentile_1y": 15.0, "level": "低估"}},
        "fundamental": {"score": 82, "score_label": "优秀"},
        "risk": {"risk_level": {"label": "中风险", "score": 45}},
        "price_levels": {},
        "changes": {},
    }


def _fake_llm_config(tmp_path) -> SimpleNamespace:
    return SimpleNamespace(
        json_dir=tmp_path / "json",
        output_dir=tmp_path / "output",
        reports_dir=tmp_path / "reports",
        llm_model="test-model",
    )


def test_e2e_llm_report_evidence_and_record(monkeypatch, tmp_path) -> None:
    """LLM success: FakeLLM injected through DI, real render/evidence/history to RunRecord."""
    store, gateway = _patch_common(monkeypatch, tmp_path, _bundle())
    fake_config = _fake_llm_config(tmp_path)
    monkeypatch.setattr(
        "src.analysis.package.build_analysis_package", lambda **kwargs: _fake_package()
    )
    # Real render_report/AnalysisHistory derive config via their own module imports.
    monkeypatch.setattr("src.reports.renderer.get_config", lambda: fake_config)
    monkeypatch.setattr("src.app.history.get_config", lambda: fake_config)
    # Inject FakeLLM via the lightweight DI hook instead of patching the import site.
    result = analyze_stock(
        AnalysisRequest(ticker="600519", mode="trade", use_llm=True),
        llm_factory=FakeLLM,
        gateway=gateway,
    )

    assert result.output_kind == "report"
    assert str(result.output_path).startswith(str(fake_config.reports_dir))
    report = result.output_path.read_text(encoding="utf-8")
    assert "600519.SH" in report and "AI 分析结论" in report
    evidence = sorted((tmp_path / "json").glob("*_evidence.json"))
    assert len(evidence) == 1
    assert json.loads(evidence[0].read_text(encoding="utf-8"))["schema_version"] == "1.0"
    assert list(fake_config.reports_dir.glob("*.md.tmp")) == []

    record = store.list()[0]
    assert record["outcome"]["status"] == "success"
    stages = record["stages"]
    assert set(stages) == {
        "validate_request",
        "acquire_data",
        "validate_evidence",
        "build_evidence",
        "generate_report",
    }
    for name in ("validate_request", "acquire_data", "build_evidence", "generate_report"):
        assert stages[name]["status"] == "completed"
    assert stages["validate_evidence"]["allow_llm"] is True
    generate = stages["generate_report"]
    assert generate["llm_used"] is True
    assert generate["model"] == "test-model"
    # Token usage is intentionally redacted at the key level (marker "token").
    assert generate["tokens"] == "<redacted>"
    assert generate["evidence_artifact"] == str(evidence[0])

    history = json.loads(
        (fake_config.output_dir / "history.json").read_text(encoding="utf-8")
    )
    assert "600519.SH" in json.dumps(history, ensure_ascii=False)


def test_e2e_llm_failure_records_failed_no_artifacts(monkeypatch, tmp_path) -> None:
    """LLM failure mid-report: failed record, safe message, no partial artifacts."""
    store, gateway = _patch_common(monkeypatch, tmp_path, _bundle())
    fake_config = _fake_llm_config(tmp_path)
    monkeypatch.setattr(
        "src.analysis.package.build_analysis_package", lambda **kwargs: _fake_package()
    )
    monkeypatch.setattr("src.reports.renderer.get_config", lambda: fake_config)
    monkeypatch.setattr("src.app.history.get_config", lambda: fake_config)
    # Inject failing FakeLLM via the DI hook.
    with pytest.raises(StockAnalysisError, match="上游超时"):
        analyze_stock(
            AnalysisRequest(ticker="600519", mode="trade", use_llm=True),
            llm_factory=lambda: FakeLLM(error=RuntimeError("LLM 上游超时")),
            gateway=gateway,
        )

    record = store.list()[0]
    assert record["outcome"]["status"] == "failed"
    assert record["outcome"]["error_type"] == "RuntimeError"
    assert "上游超时" in record["outcome"]["safe_message"]
    assert list((tmp_path / "json").glob("*")) == []
    assert not (tmp_path / "reports").exists()


def test_safe_error_message_redacts_credentials() -> None:
    message = _safe_error_message(RuntimeError("调用失败 api_key=sk-abc123 token=tok-456"))
    assert "sk-abc123" not in message
    assert "tok-456" not in message
    assert "<redacted>" in message
    assert len(message) <= 500


def test_e2e_validation_block_leaves_no_artifacts(monkeypatch, tmp_path) -> None:
    """Validation block: failed record with no evidence/report artifacts at all."""
    store, gateway = _patch_common(monkeypatch, tmp_path, _bundle(invalid=True))

    with pytest.raises(DataValidationError):
        analyze_stock(AnalysisRequest(ticker="600519", use_llm=True), gateway=gateway)

    record = store.list()[0]
    assert record["outcome"]["status"] == "failed"
    assert record["outcome"]["error_type"] == "DataValidationError"
    assert not (tmp_path / "json").exists()
    assert not (tmp_path / "reports").exists()
