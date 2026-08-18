"""Web GUI 后端 API 测试 — 无需显示器，起本地服务器用 http.client 直连。

覆盖：静态文件服务、bootstrap/设置读写、策略保存/重置、参数采用、路径
越权防护、单任务并发互斥、SSE 事件（分析完成 / 回测结果）。
"""

from __future__ import annotations

import http.client
import importlib
import json
import threading
import time
from types import SimpleNamespace

import pytest

from src.app.service import AnalysisResult


def _http(port: int, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
    payload = json.dumps(body or {}) if body is not None else None
    headers = {"Content-Type": "application/json"} if body is not None else {}
    conn.request(method, path, body=payload, headers=headers)
    resp = conn.getresponse()
    data = json.loads(resp.read().decode("utf-8") or "{}")
    conn.close()
    return resp.status, data


def _http_text(port: int, method: str, path: str) -> tuple[int, str]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
    conn.request(method, path)
    resp = conn.getresponse()
    text = resp.read().decode("utf-8", errors="replace")
    conn.close()
    return resp.status, text


@pytest.fixture()
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCK_ANALYSIS_HOME", str(tmp_path))
    from src.config import reset_config

    reset_config()
    mod = importlib.import_module("src.app.webgui.server")
    importlib.reload(mod)
    srv = mod.WebGuiServer(port=0)
    srv.start()
    yield srv, mod, tmp_path
    srv.stop()


def _drain(mod, expected_type: str, timeout: float = 10.0) -> dict:
    sub = mod.CTX.bus.subscribe()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            event = sub.get(timeout=0.3)
        except Exception:
            continue
        if event.get("type") == expected_type:
            return event
    raise AssertionError(f"未收到事件 {expected_type}")


# ---------------------------------------------------------------------------
# 静态资源与基本信息
# ---------------------------------------------------------------------------


def test_static_index_and_css(server) -> None:
    srv, _, _ = server
    status, text = _http_text(srv.port, "GET", "/")
    assert status == 200
    assert "股票分析工具" in text
    assert "static/app.js" in text
    status, css = _http_text(srv.port, "GET", "/static/style.css")
    assert status == 200
    assert "backdrop-filter" in css


def test_bootstrap_exposes_modes_strategy_settings(server) -> None:
    srv, _, _ = server
    status, data = _http(srv.port, "GET", "/api/bootstrap")
    assert status == 200
    assert {m["key"] for m in data["modes"]} == {"quick", "deep", "value", "trade"}
    assert data["strategy"]["source"] == "builtin"
    assert data["effective_ma_periods"] == [5, 10, 20, 60]


def test_chart_and_static_traversal_guarded(server) -> None:
    srv, _, _ = server
    status, data = _http(srv.port, "GET", "/api/chart?path=..%2F..%2F.env")
    assert status == 400 or status == 404
    status, _ = _http(srv.port, "GET", "/static/../../config/settings.yaml")
    assert status == 400 or status == 404


# ---------------------------------------------------------------------------
# 设置读写
# ---------------------------------------------------------------------------


def test_settings_save_and_reload(server, tmp_path) -> None:
    srv, mod, _ = server
    status, data = _http(
        srv.port,
        "POST",
        "/api/settings",
        {
            "tushare_token": "tok-1",
            "llm_api_key": "key-1",
            "llm_base_url": "https://example.com/v1",
            "llm_model": "m1",
            "llm_model_deep": "m2",
            "analysis_ma_periods": "5,10",
            "use_analysis_ma_override": "1",
        },
    )
    assert status == 200
    assert data["effective_ma_periods"] == [5, 10]

    status, data = _http(srv.port, "GET", "/api/settings")
    assert status == 200
    assert data["settings"]["TUSHARE_TOKEN"] == "tok-1"
    assert data["effective_ma_periods"] == [5, 10]


def test_adopt_params_enables_analysis_override(server) -> None:
    srv, _, _ = server
    status, data = _http(
        srv.port, "POST", "/api/adopt", {"ma_fast": 5, "ma_slow": 30}
    )
    assert status == 200
    assert data["adopted"] is True
    status, data = _http(srv.port, "GET", "/api/settings")
    assert data["settings"]["ANALYSIS_MA_PERIODS"] == "5,30"
    assert data["effective_ma_periods"] == [5, 30]


def test_adopt_rejects_invalid_params(server) -> None:
    srv, _, _ = server
    status, _ = _http(srv.port, "POST", "/api/adopt", {"ma_fast": 60, "ma_slow": 5})
    assert status == 400


# ---------------------------------------------------------------------------
# 策略接口
# ---------------------------------------------------------------------------


def test_strategy_save_reset_roundtrip(server) -> None:
    srv, mod, _ = server
    user_code = (
        "NAME='custom'\nDESCRIPTION='custom strategy'\n"
        "PARAMETERS=('fast','slow')\nDEFAULTS={'fast':5,'slow':20}\n"
        "def compute_signal(frame, params):\n"
        "    f=frame['close'].rolling(int(params['fast'])).mean()\n"
        "    s=frame['close'].rolling(int(params['slow'])).mean()\n"
        "    return ((f>s)&f.notna()&s.notna()).astype(int)\n"
    )
    status, data = _http(
        srv.port, "POST", "/api/strategy", {"action": "save", "source_code": user_code}
    )
    assert status == 200
    assert data["strategy"]["source"] == "user"
    assert data["strategy"]["name"] == "custom"

    status, data = _http(srv.port, "GET", "/api/strategy")
    assert status == 200
    assert data["strategy"]["source"] == "user"

    status, data = _http(srv.port, "POST", "/api/strategy", {"action": "reset"})
    assert status == 200
    assert data["strategy"]["source"] == "builtin"


def test_strategy_broken_file_falls_back(server) -> None:
    srv, mod, tmp_path = server
    _http(
        srv.port,
        "POST",
        "/api/strategy",
        {"action": "save", "source_code": "def broken(:\n  return\n"},
    )
    status, data = _http(srv.port, "GET", "/api/strategy")
    assert status == 200
    assert data["strategy"]["source"] == "builtin"


# ---------------------------------------------------------------------------
# 分析任务（mock service）与并发互斥
# ---------------------------------------------------------------------------


def test_analyze_publishes_finished_event(server, tmp_path, monkeypatch) -> None:
    srv, mod, _ = server

    def fake_analyze(request, **kwargs):
        out = tmp_path / "report.md"
        out.write_text("# 测试报告\n\n## 结论\n\n正常输出。", encoding="utf-8")
        if kwargs.get("token_callback"):
            kwargs["token_callback"]("报告流")
        if kwargs.get("stage_progress"):
            kwargs["stage_progress"]("generate_report", "done", "完成")
        return AnalysisResult(
            ticker=request.ticker,
            stock_name="测试股票",
            output_path=out,
            output_kind="markdown",
            elapsed_seconds=0.5,
            chart_path=None,
        )

    monkeypatch.setattr("src.app.webgui.server.analyze_stock", fake_analyze)
    status, data = _http(
        srv.port,
        "POST",
        "/api/analyze",
        {"tickers": ["600519.SH"], "mode": "quick", "use_llm": False, "chart": False},
    )
    assert status == 202
    event = _drain(mod, "finished")
    assert event["ticker"] == "600519.SH"
    assert "测试报告" in event["content"]


def test_concurrent_analyze_rejected(server, monkeypatch) -> None:
    srv, mod, _ = server
    started = threading.Event()
    release = threading.Event()

    def fake_analyze(request, **kwargs):
        started.set()
        release.wait(timeout=10)
        out = SimpleNamespace(
            ticker=request.ticker, stock_name="x", output_path=__import__("pathlib").Path("x"),
            output_kind="json", elapsed_seconds=0.1, chart_path=None,
        )
        return out

    monkeypatch.setattr("src.app.webgui.server.analyze_stock", fake_analyze)
    thread = threading.Thread(
        target=lambda: _http(
            srv.port, "POST", "/api/analyze", {"tickers": ["600519.SH"]}
        ),
        daemon=True,
    )
    thread.start()
    assert started.wait(5)
    status, _ = _http(srv.port, "POST", "/api/analyze", {"tickers": ["000858.SZ"]})
    assert status == 409
    release.set()
    thread.join(timeout=5)


def test_analyze_normalizes_bare_tickers(server, monkeypatch) -> None:
    """裸代码（含北交所）在入口规范化后再派发（002001 -> 002001.SZ）。"""
    srv, mod, _ = server
    captured: dict = {}

    def fake_batch(requests, **kwargs):
        captured["requests"] = requests
        return []

    monkeypatch.setattr("src.app.webgui.server.analyze_batch", fake_batch)
    status, _ = _http(
        srv.port,
        "POST",
        "/api/analyze",
        {"tickers": ["002001", "430047.BJ"], "mode": "quick", "use_llm": False},
    )
    assert status == 202
    _drain(mod, "batch_done")  # 等待后台线程派发
    assert [r.ticker for r in captured["requests"]] == ["002001.SZ", "430047.BJ"]


def test_analyze_rejects_invalid_ticker_before_job(server) -> None:
    """非法代码在入口返回 400，不进入任务队列。"""
    srv, _, _ = server
    status, data = _http(
        srv.port,
        "POST",
        "/api/analyze",
        {"tickers": ["600519.BJ"]},
    )
    assert status == 400
    assert "后缀有误" in data["error"]


# ---------------------------------------------------------------------------
# 回测 / 优化（mock 取数）
# ---------------------------------------------------------------------------


def test_backtest_endpoint_publishes_result(server, tmp_path, monkeypatch) -> None:
    srv, mod, _ = server

    monkeypatch.setattr(
        "src.app.webgui.server.DataGateway",
        lambda: SimpleNamespace(
            fetch_daily_bars=lambda *a, **k: SimpleNamespace(daily=_make_daily())
        ),
    )
    status, _ = _http(
        srv.port,
        "POST",
        "/api/backtest",
        {"ticker": "600519.SH", "start": "2024-01-01", "end": "2024-06-01",
         "ma_fast": 5, "ma_slow": 20},
    )
    assert status == 202
    event = _drain(mod, "bt_result")
    assert event["kind"] == "backtest"
    assert "总收益率" in event["text"]


def test_backtest_normalizes_bare_ticker(server, monkeypatch) -> None:
    """Bare codes must reach the gateway normalized (002001 -> 002001.SZ)."""
    srv, mod, _ = server
    captured: dict = {}

    def fake_fetch(code, start, end, **kwargs):
        captured["code"] = code
        return SimpleNamespace(daily=_make_daily())

    monkeypatch.setattr(
        "src.app.webgui.server.DataGateway",
        lambda: SimpleNamespace(fetch_daily_bars=fake_fetch),
    )
    status, _ = _http(
        srv.port,
        "POST",
        "/api/backtest",
        {"ticker": "002001", "start": "2024-01-01", "end": "2024-06-01",
         "ma_fast": 5, "ma_slow": 20},
    )
    assert status == 202
    _drain(mod, "bt_result")  # 后台线程执行完后 fetch_daily_bars 才会被调用
    assert captured.get("code") == "002001.SZ"


def test_backtest_rejects_invalid_ticker(server) -> None:
    srv, _, _ = server
    status, data = _http(
        srv.port,
        "POST",
        "/api/backtest",
        {"ticker": "abcdef", "start": "2024-01-01", "end": "2024-06-01"},
    )
    assert status == 400
    assert "股票代码" in data["error"]


def _make_daily():
    import numpy as np
    import pandas as pd

    prices = np.concatenate([np.linspace(30, 70, 120), np.linspace(70, 40, 120)])
    dates = pd.bdate_range("2024-01-01", periods=len(prices))
    return pd.DataFrame(
        {
            "trade_date": dates,
            "open": prices,
            "high": prices * 1.01,
            "low": prices * 0.99,
            "close": prices,
            "volume": np.full(len(prices), 1_000_000.0),
        }
    )


def test_optimize_endpoint_publishes_selected(server, monkeypatch) -> None:
    srv, mod, _ = server
    monkeypatch.setattr(
        "src.app.webgui.server.DataGateway",
        lambda: SimpleNamespace(
            fetch_daily_bars=lambda *a, **k: SimpleNamespace(daily=_make_daily())
        ),
    )
    status, _ = _http(
        srv.port,
        "POST",
        "/api/optimize",
        {"ticker": "600519.SH", "start": "2024-01-01", "end": "2024-06-01",
         "objective": "sharpe",
         "grids": {"ma_fast": [5, 10], "ma_slow": [20, 60]}},
    )
    assert status == 202
    event = _drain(mod, "bt_result")
    assert event["kind"] == "optimize"
    assert "选中参数" in event["text"]


def test_open_file_guarded_outside_output(server, tmp_path) -> None:
    srv, _, _ = server
    outside = tmp_path / "secret.txt"
    outside.write_text("x", encoding="utf-8")
    status, data = _http(
        srv.port, "POST", "/api/open_file", {"path": str(outside)}
    )
    assert status == 403


def test_update_install_rejects_foreign_host(server) -> None:
    srv, _, _ = server
    status, _ = _http(
        srv.port,
        "POST",
        "/api/update/install",
        {"url": "https://evil.example.com/StockAnalysis-Setup-9.9.9.exe"},
    )
    assert status == 403


def test_update_install_rejects_foreign_host_in_urls(server) -> None:
    srv, _, _ = server
    status, _ = _http(
        srv.port,
        "POST",
        "/api/update/install",
        {
            "urls": [
                "https://gitee.com/li_nanqi/stock_analysis/releases/download/"
                "v1.3.0/StockAnalysis-Setup-1.3.0.exe",
                "https://evil.example.com/StockAnalysis-Setup-9.9.9.exe",
            ]
        },
    )
    assert status == 403


def test_update_install_rejects_missing_source(server) -> None:
    srv, _, _ = server
    status, _ = _http(srv.port, "POST", "/api/update/install", {})
    assert status == 400


def test_installer_asset_url_prefers_exe() -> None:
    from src.app.update_check import _installer_asset_url

    release = {
        "assets": [
            {"name": "StockAnalysis-Setup-1.2.1.exe",
             "browser_download_url": "https://x/y.exe"},
            {"name": "notes.md", "browser_download_url": "https://x/y.md"},
        ]
    }
    assert _installer_asset_url(release) == "https://x/y.exe"
    assert _installer_asset_url({"assets": []}) is None
    assert _installer_asset_url({}) is None
