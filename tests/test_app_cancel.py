"""Phase 6-GUI（T-7.7）契约测试：取消令牌与确定性阶段进度。

覆盖 analyze_stock 新增的可选参数（cancel_event / stage_progress），
保持既有字符串 progress 回调与 CLI 用法完全兼容（不传新参数时行为不变，
由 test_service_pipeline.py 现有用例回归保证）。全程本地假数据，无网络。
"""

from __future__ import annotations

import threading
from types import SimpleNamespace

import pandas as pd
import pytest

from src.app.run_records import RunRecordStore
from src.app.service import (
    STAGES,
    AnalysisCancelledError,
    AnalysisRequest,
    analyze_stock,
)
from src.data.gateway import StockDataBundle


def _daily() -> pd.DataFrame:
    """Two valid trading days (matches the service-pipeline fixture)."""
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


def _patch_common(
    monkeypatch: pytest.MonkeyPatch, tmp_path, bundle: StockDataBundle
) -> RunRecordStore:
    """Local, network-free harness mirroring test_service_pipeline._patch_common."""
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

    class FakeGateway:
        def fetch(self, *args, **kwargs):  # noqa: ANN002, ANN003
            return bundle

    monkeypatch.setattr("src.data.gateway.DataGateway", FakeGateway)
    return store


def _patch_package(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub the local evidence package builder (kept away from real data)."""
    monkeypatch.setattr(
        "src.analysis.package.build_analysis_package",
        lambda **kwargs: {
            "schema_version": "1.0",
            "quality": "ok",
            "data_gaps": [],
        },
    )


def test_cancel_at_checkpoint_raises_and_records_cancelled(
    monkeypatch: pytest.MonkeyPatch, tmp_path,
) -> None:
    store = _patch_common(monkeypatch, tmp_path, _bundle())
    cancel = threading.Event()
    cancel.set()

    with pytest.raises(AnalysisCancelledError):
        analyze_stock(
            AnalysisRequest(ticker="600519", use_llm=False),
            cancel_event=cancel,
        )

    records = store.list()
    assert records[0]["outcome"]["status"] == "cancelled"
    # A cancelled run must not leave partial artifacts behind.
    for junk in (tmp_path / "json", tmp_path / "output"):
        assert not junk.exists() or not any(junk.iterdir()), f"partial artifact in {junk}"


def test_stage_progress_emits_deterministic_sequence(
    monkeypatch: pytest.MonkeyPatch, tmp_path,
) -> None:
    _patch_common(monkeypatch, tmp_path, _bundle())
    _patch_package(monkeypatch)
    seen: list[tuple[str, str]] = []

    analyze_stock(
        AnalysisRequest(ticker="600519", use_llm=False),
        stage_progress=lambda stage, status, message: seen.append((stage, status)),
    )

    assert all(status in ("running", "done") for _, status in seen)
    # Order guard: acquire must appear after validate and before generate_report.
    assert seen.index(("validate_request", "running")) < seen.index(("acquire_data", "running"))
    assert seen.index(("acquire_data", "done")) < seen.index(
        ("generate_report", "running")
    )
    # Both terminal book-keeping events are emitted for a successful run.
    assert ("finish", "running") in seen
    assert ("finish", "done") in seen
    # The emit sequence references no stage outside the canonical list.
    for stage, _ in seen:
        assert stage in STAGES
