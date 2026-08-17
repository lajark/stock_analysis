"""Phase 6 批量并发（T-7.8）契约测试：锁/原子写/限频/有界线程池。

覆盖：RunRecordStore 并发追加不丢行、CacheManager 并发合并不丢更新、
RateLimiter 最小间隔、AnalysisHistory 并发 add 不丢且 id 唯一、
analyze_batch 有界并发 + 失败隔离 + 输入顺序、并发输出路径不碰撞。
全程本地假数据、无网络。
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from src.analysis.contracts import RunRecord
from src.app.history import AnalysisHistory
from src.app.run_records import RunRecordStore
from src.app.service import (
    AnalysisRequest,
    AnalysisResult,
    StockAnalysisError,
    analyze_batch,
    analyze_stock,
)
from src.data.cache import CacheManager
from src.data.rate_limit import RateLimiter


def _run_many(worker: Callable[[int], None], n: int) -> None:
    """Start n threads against worker(i) and join them all."""
    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()


# ---------------------------------------------------------------------------
# RunRecordStore 并发追加
# ---------------------------------------------------------------------------
def test_run_record_store_concurrent_save_drops_no_line(tmp_path: Path) -> None:
    store = RunRecordStore(tmp_path / "run_records.jsonl")
    per_thread = 5
    n_threads = 8
    total = per_thread * n_threads

    def worker(i: int) -> None:
        for j in range(per_thread):
            run = RunRecord.start({"ticker": f"T{i}-{j}"})
            run.finish()
            store.save(run)

    _run_many(worker, n_threads)

    records = store.list(limit=total + 10)
    # Every record survived, and no JSON line was interleaved or truncated.
    assert len(records) == total
    assert len({r["run_id"] for r in records}) == total
    raw = (tmp_path / "run_records.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(raw) == total
    for line in raw:
        assert isinstance(json.loads(line), dict)


# ---------------------------------------------------------------------------
# CacheManager 并发合并：不同日期段不互相丢失
# ---------------------------------------------------------------------------
def test_cache_concurrent_save_daily_merges_all_rows(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    cache_dir = tmp_path / "cache"
    # CacheManager reads get_config() at __init__; patch module-local import.
    monkeypatch.setattr(
        "src.data.cache.get_config",
        lambda: SimpleNamespace(cache_dir=cache_dir, cache=SimpleNamespace(enabled=True)),
    )
    CacheManager()  # create the directory

    all_dates = [f"2026-01-{d:02d}" for d in range(1, 16)]  # 15 disjoint days
    per_thread = 5
    n_threads = 3

    def worker(i: int) -> None:
        # A fresh CacheManager instance per thread loads a (possibly stale)
        # meta snapshot; the write path must merge from disk, not clobber.
        CacheManager().save_daily(
            "600519.SH",
            _daily_frame(all_dates[i * per_thread:(i + 1) * per_thread], offset=float(i)),
        )

    _run_many(worker, n_threads)

    merged = pd.read_parquet(cache_dir / "daily" / "600519.SH_2026.parquet")
    assert len(merged) == len(all_dates)
    assert merged["trade_date"].nunique() == len(all_dates)

    # meta.json must be the latest, valid JSON with the daily key present.
    meta = json.loads((cache_dir / "meta.json").read_text(encoding="utf-8"))
    assert meta["daily/600519.SH"]["latest_date"] == "2026-01-15"


def _daily_frame(dates: list[str], offset: float) -> pd.DataFrame:
    """Minimal daily frame matching CacheManager.save_daily's expectations."""
    return pd.DataFrame(
        {
            "trade_date": pd.to_datetime(dates),
            "open": [10.0 + offset] * len(dates),
            "high": [11.0 + offset] * len(dates),
            "low": [9.0 + offset] * len(dates),
            "close": [10.5 + offset] * len(dates),
            "volume": [100.0] * len(dates),
        }
    )


# ---------------------------------------------------------------------------
# RateLimiter 最小间隔
# ---------------------------------------------------------------------------
def test_rate_limiter_enforces_min_interval() -> None:
    interval = 0.02
    limiter = RateLimiter(interval)
    start = time.monotonic()
    for _ in range(20):
        limiter.acquire()
    elapsed = time.monotonic() - start
    # (n-1) gaps must each be at least `interval`; allow 10% timing tolerance.
    assert elapsed >= (20 - 1) * interval * 0.9


# ---------------------------------------------------------------------------
# AnalysisHistory 并发 add
# ---------------------------------------------------------------------------
def test_history_concurrent_add_keeps_all_and_ids_unique(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "src.app.history.get_config",
        lambda: SimpleNamespace(output_dir=tmp_path),
    )

    def worker(i: int) -> None:
        AnalysisHistory().add(
            ticker=f"T{i}",
            name="测试",
            mode="quick",
            report_path="x.md",
            tokens={},
            cost=0.0,
            date="",
        )

    _run_many(worker, 5)

    records = AnalysisHistory().list(limit=100)
    assert len(records) == 5
    ids = [r["id"] for r in records]
    assert len(ids) == len(set(ids)) == 5


# ---------------------------------------------------------------------------
# analyze_batch：失败隔离 + 有界并发 + 输入顺序
# ---------------------------------------------------------------------------
def test_analyze_batch_failure_isolation_and_bounded_concurrency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests = [AnalysisRequest(ticker=f"T{i}", use_llm=False) for i in range(5)]
    doomed = requests[2].ticker
    state = {"active": 0, "max_active": 0}
    state_lock = threading.Lock()

    def fake_analyze(req: AnalysisRequest, cancel_event=None):  # noqa: ANN001
        with state_lock:
            state["active"] += 1
            state["max_active"] = max(state["max_active"], state["active"])
        try:
            if req.ticker == doomed:
                raise StockAnalysisError("boom")
            time.sleep(0.05)
            return AnalysisResult(
                ticker=req.ticker,
                stock_name="x",
                output_path=Path(f"/tmp/{req.ticker}.json"),
                output_kind="json",
                elapsed_seconds=0.05,
            )
        finally:
            with state_lock:
                state["active"] -= 1

    monkeypatch.setattr("src.app.service.analyze_stock", fake_analyze)
    items = analyze_batch(requests, max_workers=4)

    # Input order preserved.
    assert [i.request.ticker for i in items] == [r.ticker for r in requests]
    # Concurrency was actually bounded by the worker pool (and did overlap).
    assert 2 <= state["max_active"] <= 4
    failed = next(i for i in items if i.request.ticker == doomed)
    assert failed.result is None
    assert failed.error and "boom" in failed.error
    for item in items:
        if item.request.ticker != doomed:
            assert item.error is None
            assert item.result is not None


def test_analyze_batch_serial_path_matches_input_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests = [AnalysisRequest(ticker=f"T{i}", use_llm=False) for i in range(3)]

    def fake_analyze(req: AnalysisRequest, cancel_event=None):  # noqa: ANN001
        return AnalysisResult(
            ticker=req.ticker,
            stock_name="x",
            output_path=Path(f"/tmp/{req.ticker}.json"),
            output_kind="json",
            elapsed_seconds=0.0,
        )

    monkeypatch.setattr("src.app.service.analyze_stock", fake_analyze)
    items = analyze_batch(requests, max_workers=1)
    assert all(i.result is not None and i.error is None for i in items)
    assert [i.request.ticker for i in items] == [r.ticker for r in requests]


def test_analyze_batch_serial_forwards_stage_progress_with_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Serial batch forwards per-item stage callbacks carrying the [k/N] prefix."""
    calls: list[AnalysisRequest] = []
    messages: list[str] = []

    def fake_analyze(req: AnalysisRequest, **kwargs) -> AnalysisResult | None:
        calls.append(req)
        cb = kwargs.get("stage_progress")
        if cb is not None:
            cb("acquire_data", "running", "获取")
        return None

    monkeypatch.setattr("src.app.service.analyze_stock", fake_analyze)
    requests = [AnalysisRequest(ticker="600519.SH"), AnalysisRequest(ticker="000858.SZ")]
    analyze_batch(
        requests,
        max_workers=1,
        stage_progress=lambda s, st, m: messages.append(m),
        item_prefix=lambda i, n: f"[{i + 1}/{n}]",
    )
    assert messages == ["[1/2] 获取", "[2/2] 获取"]
    assert [r.ticker for r in calls] == ["600519.SH", "000858.SZ"]


def test_analyze_batch_concurrent_prefixes_bound_per_item(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Concurrent batch: every item keeps its own prefix bound at submit time."""
    messages: list[str] = []

    def fake_analyze(req, **kwargs):
        cb = kwargs.get("stage_progress")
        if cb is not None:
            cb("build_evidence", "done", "计算")
        return None

    monkeypatch.setattr("src.app.service.analyze_stock", fake_analyze)
    requests = [AnalysisRequest(ticker="T1"), AnalysisRequest(ticker="T2")]
    analyze_batch(
        requests,
        max_workers=2,
        stage_progress=lambda s, st, m: messages.append(m),
        item_prefix=lambda i, n: f"[{i + 1}/{n}]",
    )
    assert sorted(messages) == ["[1/2] 计算", "[2/2] 计算"]


def test_analyze_batch_default_no_new_kwargs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without callbacks the historic kwargs set reaches analyze_stock unchanged."""
    received: list[set[str]] = []

    def fake_analyze(req, **kwargs):
        received.append(set(kwargs))
        return None

    monkeypatch.setattr("src.app.service.analyze_stock", fake_analyze)
    analyze_batch(
        [AnalysisRequest(ticker="T1"), AnalysisRequest(ticker="T2")],
        max_workers=1,
    )
    assert received == [{"cancel_event"}, {"cancel_event"}]


# ---------------------------------------------------------------------------
# 并发 analyze_stock（no-llm）输出路径不碰撞
# ---------------------------------------------------------------------------
def test_concurrent_analyze_stock_no_llm_distinct_outputs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Two concurrent runs on the same ticker must not overwrite each other."""
    _patch_common(monkeypatch, tmp_path)
    _patch_package(monkeypatch)

    results: list[AnalysisResult | None] = [None, None]

    def worker(i: int) -> None:
        results[i] = analyze_stock(
            AnalysisRequest(ticker="600519", use_llm=False)
        )

    _run_many(worker, 2)

    assert results[0] is not None and results[1] is not None
    assert results[0].output_path != results[1].output_path
    assert results[0].output_path.exists()
    assert results[1].output_path.exists()
    # The store holds one completed record per run (no lost lines).
    store = RunRecordStore(tmp_path / "run_records.jsonl")
    assert len(store.list(limit=10)) == 2


# ---------------------------------------------------------------------------
# 本地、无网络的 patch 套件（与 test_service_pipeline 同构）
# ---------------------------------------------------------------------------
def _patch_common(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> RunRecordStore:
    store = RunRecordStore(tmp_path / "run_records.jsonl")
    monkeypatch.setattr("src.app.service.RunRecordStore", lambda: store)
    monkeypatch.setattr(
        "src.app.service.validate_request",
        lambda request: AnalysisRequest(
            ticker="600519.SH",
            mode=request.mode,
            date="2026-08-14",
            use_llm=request.use_llm,
            chart=request.chart,
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
            from src.data.gateway import StockDataBundle

            return StockDataBundle(
                stock_info={"code": "600519.SH", "name": "测试股票"},
                daily=_daily_bundle_frame(),
                daily_basic={},
                income=pd.DataFrame(),
                balance_sheet=pd.DataFrame(),
                cashflow=pd.DataFrame(),
                fina_indicator=pd.DataFrame(),
                providers={"stock_info": "tushare", "daily": "tushare"},
                quality={"stock_info": "ok", "daily": "ok"},
            )

    monkeypatch.setattr("src.data.gateway.DataGateway", FakeGateway)
    return store


def _daily_bundle_frame() -> pd.DataFrame:
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


def _patch_package(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "src.analysis.package.build_analysis_package",
        lambda **kwargs: {
            "schema_version": "1.0",
            "quality": "ok",
            "data_gaps": [],
        },
    )
