"""Typer CLI 分层集成测试 — 验证命令路由、批量输出与退出码。

遵循既有模式（tests/test_service_pipeline.py:_patch_common）：本地假数据、
FakeGateway 阻断全部真实网络请求。注意 Rich 在模块导入时急绑定 stdout，
因此 CliRunner 的 ``result.output`` 为空——断言副作用（文件、退出码、run 记录）
或通过 console.print spy 断言文案。
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pandas as pd
import pytest
from typer.testing import CliRunner

from src.app import cli as cli_module
from src.app.run_records import RunRecordStore
from src.app.service import AnalysisRequest, AnalysisResult, BatchItem
from src.data.gateway import StockDataBundle


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


class FakeGateway:
    def fetch(self, *args, **kwargs):
        return _bundle()


@pytest.fixture
def runner(tmp_path, monkeypatch) -> CliRunner:
    """Patch the whole service path; return a CliRunner on the real app."""
    store = RunRecordStore(tmp_path / "run_records.jsonl")
    monkeypatch.setattr("src.app.cli._setup_logging", lambda debug=None: None)
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
    monkeypatch.setattr("src.data.gateway.DataGateway", FakeGateway)
    monkeypatch.setattr(
        "src.analysis.package.build_analysis_package",
        lambda **kwargs: {"schema_version": "1.0", "quality": "ok", "data_gaps": []},
    )
    runner = CliRunner()
    # Keep the store reachable for per-test assertions.
    runner._store = store  # type: ignore[attr-defined]
    return runner


def _spy_console(monkeypatch) -> list[str]:
    captured: list[str] = []

    def spy_print(*args, **kwargs):
        captured.append(str(args[0]) if args else "")

    monkeypatch.setattr(cli_module.console, "print", spy_print)
    return captured


def test_cli_single_no_llm_end_to_end(runner, tmp_path, monkeypatch) -> None:
    spy = _spy_console(monkeypatch)
    result = runner.invoke(
        cli_module.app, ["analyze", "--ticker", "600519", "--no-llm", "--date", "2026-08-14"]
    )
    assert result.exit_code == 0, result.exception

    json_files = list((tmp_path / "json").glob("600519.SH_*.json"))
    assert len(json_files) == 1
    payload = json.loads(json_files[0].read_text(encoding="utf-8"))
    assert payload["quality"] == "ok"

    records = runner._store.list()  # type: ignore[attr-defined]
    assert records[0]["outcome"]["status"] == "success"
    assert " -> " in spy[-1]  # output line was printed


def test_cli_batch_routing_and_failure_isolation(runner, monkeypatch, tmp_path) -> None:
    captured = _spy_console(monkeypatch)
    ok = BatchItem(
        request=AnalysisRequest(ticker="600519.SH"),
        result=AnalysisResult(
            ticker="600519.SH",
            stock_name="测试股票",
            output_path=tmp_path / "out_ok.json",
            output_kind="json",
            elapsed_seconds=0.5,
        ),
    )
    failed = BatchItem(
        request=AnalysisRequest(ticker="000858.SZ"), error="模拟失败"
    )

    def fake_batch(requests, **kwargs):
        return [ok, failed]

    monkeypatch.setattr(cli_module, "analyze_batch", fake_batch)

    result = runner.invoke(
        cli_module.app,
        [
            "analyze",
            "--tickers",
            "600519,000858",
            "--no-llm",
            "--date",
            "2026-08-14",
            "--workers",
            "2",
        ],
    )
    assert result.exit_code == 0, result.exception
    joined = "\n".join(captured)
    assert "600519.SH" in joined and "out_ok.json" in joined
    assert "000858.SZ" in joined and "失败" in joined and "模拟失败" in joined
    assert "1/2 只股票失败" in joined


def test_cli_invalid_ticker_exits_nonzero(runner, tmp_path) -> None:
    result = runner.invoke(cli_module.app, ["analyze", "--ticker", "abc", "--no-llm"])
    assert result.exit_code == 1


def test_cli_requires_ticker_option(runner, tmp_path) -> None:
    result = runner.invoke(cli_module.app, ["analyze", "--no-llm"])
    assert result.exit_code == 1


def test_cli_rejects_conflicting_ticker_options(runner, tmp_path) -> None:
    result = runner.invoke(
        cli_module.app, ["analyze", "--ticker", "600519", "--tickers", "000858", "--no-llm"]
    )
    assert result.exit_code == 1


def test_cli_unknown_mode_exits_nonzero(runner, tmp_path) -> None:
    result = runner.invoke(
        cli_module.app,
        ["analyze", "--ticker", "600519", "--mode", "bogus", "--no-llm", "--date", "2026-08-14"],
    )
    assert result.exit_code == 1
