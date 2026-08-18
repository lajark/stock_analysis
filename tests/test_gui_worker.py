"""GUI worker/事件循环测试 — 无显示器安全（绝不创建 tk.Tk()）。

通过 ``object.__new__`` 构造 ``StockAnalysisApp`` 并注入假控件，直接调用
worker 与事件处理方法，验证其与 Tk 解耦的契约：分析线程发事件、事件循环
分发并重排、取消/失败不产生部分产物等。生产代码零改动。
"""

from __future__ import annotations

import queue
import threading
from pathlib import Path
from types import SimpleNamespace

from src.app.gui import StockAnalysisApp, _split_tickers
from src.app.service import (
    AnalysisCancelledError,
    AnalysisRequest,
    AnalysisResult,
    BatchItem,
)
from src.app.update_check import version_gt


class _Var:
    def __init__(self, value: str = "") -> None:
        self.value = value

    def set(self, value: str) -> None:
        self.value = str(value)

    def get(self) -> str:
        return self.value


class _BoolVar:
    def __init__(self, value: bool = False) -> None:
        self.value = value

    def set(self, value: bool) -> None:
        self.value = bool(value)

    def get(self) -> bool:
        return self.value


class _Button:
    def __init__(self) -> None:
        self.state = "normal"
        self.mapped = False

    def configure(self, **kwargs) -> None:
        if "state" in kwargs:
            self.state = kwargs["state"]

    def grid(self, *args, **kwargs) -> None:
        self.mapped = True

    def grid_remove(self) -> None:
        self.mapped = False


class _Progress:
    def __init__(self) -> None:
        self.running = False

    def start(self, *args) -> None:
        self.running = True

    def stop(self) -> None:
        self.running = False


class _Preview:
    def __init__(self) -> None:
        self.state = "disabled"
        self.text = ""
        self.see_calls: list[str] = []
        self.mapped = False

    def configure(self, **kwargs) -> None:
        self.state = kwargs.get("state", self.state)

    def delete(self, *args) -> None:
        self.text = ""

    def insert(self, index: str, content: str, *args) -> None:
        if str(index) == "1.0":
            self.text = content
        else:
            self.text += content

    def see(self, index: str) -> None:
        self.see_calls.append(index)

    def load_html(self, content: str) -> None:
        self.text = content

    def grid(self, *args, **kwargs) -> None:
        self.mapped = True

    def grid_remove(self) -> None:
        self.mapped = False


class _Root:
    def __init__(self) -> None:
        self.calls: list[tuple[int, object]] = []

    def after(self, ms: int, callback) -> None:
        self.calls.append((ms, callback))


class _Notebook:
    def __init__(self) -> None:
        self.selected = None

    def select(self, page) -> None:
        self.selected = page


def _make_app(**overrides) -> StockAnalysisApp:
    app = object.__new__(StockAnalysisApp)
    app.events = queue.Queue()
    app.status_var = _Var()
    app.stage_var = _Var()
    app.progress = _Progress()
    app.analyze_button = _Button()
    app.open_button = _Button()
    app.cancel_button = _Button()
    app._preview_text = _Preview()
    app._preview_html = _Preview()
    app._preview_toggle = _Button()
    app._preview_mode = "text"
    app._preview_content_raw = ""
    app._preview_font_size = 14
    app.notebook = _Notebook()
    app.settings_page = "settings_page"
    app.root = _Root()
    app.current_result = None
    app.cancel_event = threading.Event()
    app._token_preview_active = False
    app.ticker_var = _Var()
    app.mode_var = _Var(value="快速扫描")
    app.use_llm_var = _BoolVar(True)
    app.chart_var = _BoolVar(False)
    app.mode_labels = {"快速扫描": "quick"}
    for key, value in overrides.items():
        setattr(app, key, value)
    return app


def _run_worker(app: StockAnalysisApp) -> None:
    app._analysis_worker(AnalysisRequest(ticker="600519.SH"))


def test_worker_posts_finished_event(monkeypatch, tmp_path) -> None:
    result = AnalysisResult(
        ticker="600519.SH",
        stock_name="测试股票",
        output_path=tmp_path / "out.json",
        output_kind="json",
        elapsed_seconds=0.5,
    )
    monkeypatch.setattr(
        "src.app.gui.analyze_stock", lambda request, **kwargs: result
    )
    app = _make_app()
    _run_worker(app)
    assert app.events.get() == ("finished", result)
    assert app.events.empty()


def test_token_events_append_streaming_preview() -> None:
    app = _make_app()
    app.events.put(("token", "你好"))
    app.events.put(("token", "，世界"))
    app._process_events()
    assert app._preview_text.text == "你好，世界"
    assert "流式输出中" in app.stage_var.value
    # Streaming stage hint set exactly once; both deltas scrolled into view.
    assert app._preview_text.see_calls == ["end", "end"]


def test_worker_stream_then_cancel_keeps_partial_preview(
    monkeypatch,
) -> None:
    def fake_analyze(request, **kwargs):
        kwargs["token_callback"]("部分预览文本")
        raise AnalysisCancelledError("用户取消")

    monkeypatch.setattr("src.app.gui.analyze_stock", fake_analyze)
    app = _make_app()
    _run_worker(app)
    app._process_events()
    # Partial streamed preview survives on screen (never persisted); UI resets.
    assert app._preview_text.text == "部分预览文本"
    assert app.status_var.value == "分析已取消：未生成报告"
    assert app.analyze_button.state == "normal"
    assert app.cancel_button.state == "disabled"


def test_worker_posts_cancelled_event(monkeypatch) -> None:
    def raise_cancelled(request, **kwargs):
        raise AnalysisCancelledError("用户取消")

    monkeypatch.setattr("src.app.gui.analyze_stock", raise_cancelled)
    app = _make_app()
    _run_worker(app)
    assert app.events.get() == ("cancelled", None)
    assert app.events.empty()


def test_worker_posts_failed_event(monkeypatch) -> None:
    def raise_error(request, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr("src.app.gui.analyze_stock", raise_error)
    app = _make_app()
    _run_worker(app)
    assert app.events.get() == ("failed", "boom")
    assert app.events.empty()


def test_process_events_drains_queue_and_reschedules() -> None:
    app = _make_app()
    assert app.events.empty()
    app.events.put(("stage", ("acquire_data", "done", "已完成数据获取")))
    app._process_events()
    assert app.stage_var.value == "✓ 阶段 2/7：获取数据"
    assert app.status_var.value == "已完成数据获取"
    assert app.root.calls == [(100, app._process_events)]


def test_finished_event_updates_ui_and_preview(tmp_path) -> None:
    out = tmp_path / "out.json"
    out.write_text("# 测试报告", encoding="utf-8")
    result = AnalysisResult(
        ticker="600519.SH",
        stock_name="测试股票",
        output_path=out,
        output_kind="json",
        elapsed_seconds=2.0,
    )
    app = _make_app()
    app.events.put(("finished", result))
    app._process_events()
    assert app.status_var.value.startswith("完成：测试股票")
    assert app._preview_text.text == "# 测试报告"
    assert app.current_result is result
    assert app.analyze_button.state == "normal"
    assert app.open_button.state == "normal"
    assert app.cancel_button.state == "disabled"
    assert app.root.calls == [(100, app._process_events)]


def test_failed_token_message_navigates_to_settings(monkeypatch) -> None:
    monkeypatch.setattr("src.app.gui.messagebox.showerror", lambda *a, **k: None)
    app = _make_app()
    # gui._analysis_failed 以 "Token"/"API 设置"/"API Key" 命中设置页导航。
    app.events.put(("failed", "请先在“API 设置”中填写并保存 Tushare Token"))
    app._process_events()
    assert app.notebook.selected == app.settings_page
    assert app.status_var.value == "分析未完成"
    assert app.analyze_button.state == "normal"
    assert app.cancel_button.state == "disabled"


def test_update_stage_formats_deterministic_line() -> None:
    app = _make_app()
    app._update_stage("acquire_data", "done", "完成")
    assert app.stage_var.value == "✓ 阶段 2/7：获取数据"
    app._update_stage("validate_evidence", "running", "校验中")
    assert app.stage_var.value == "○ 阶段 3/7：数据质量校验"
    assert app.status_var.value == "校验中"


# ---------------------------------------------------------------------------
# 批量分析（T-7.12）
# ---------------------------------------------------------------------------
def _ok_item(
    ticker: str,
    *,
    name: str = "测试股票",
    elapsed: float = 1.0,
    out: Path | None = None,
) -> BatchItem:
    return BatchItem(
        request=AnalysisRequest(ticker=ticker),
        result=AnalysisResult(
            ticker=ticker,
            stock_name=name,
            output_path=out or Path(f"/tmp/{ticker}.md"),
            output_kind="markdown",
            elapsed_seconds=elapsed,
        ),
    )


def test_split_tickers_splits_separators() -> None:
    assert _split_tickers("600519,000858\n000001 600000;601318") == [
        "600519",
        "000858",
        "000001",
        "600000",
        "601318",
    ]
    assert _split_tickers("  600519, 000858  ") == ["600519", "000858"]
    assert _split_tickers("") == []


def test_version_gt_semver() -> None:
    # Numeric comparison, not lexicographic: 1.2.10 > 1.2.9.
    assert version_gt("1.2.10", "1.2.9") is True
    assert version_gt("1.2.9", "1.2.10") is False
    # Equal and major/minor boundaries.
    assert version_gt("1.2.1", "1.2.1") is False
    assert version_gt("2.0.0", "1.9.9") is True
    assert version_gt("1.3", "1.2.1") is True
    assert version_gt("1.2.1", "1.3") is False


def test_start_analysis_batch_path(monkeypatch) -> None:
    captured: dict = {}
    done = threading.Event()

    def fake_batch(requests, **kwargs):
        captured["requests"] = requests
        captured["kwargs"] = kwargs
        done.set()
        return []

    monkeypatch.setattr("src.app.gui.analyze_batch", fake_batch)
    monkeypatch.setattr(
        "src.app.gui.get_config",
        lambda: SimpleNamespace(batch=SimpleNamespace(max_workers=1)),
    )
    app = _make_app(ticker_var=_Var("600519,000858"))
    app._start_analysis()
    assert done.wait(2)
    assert [r.ticker for r in captured["requests"]] == ["600519", "000858"]
    assert captured["kwargs"]["max_workers"] == 1
    assert captured["kwargs"]["cancel_event"] is app.cancel_event
    # Batch mode disables streaming: no token_callback (single-stock only).
    assert "token_callback" not in captured["kwargs"]
    assert captured["kwargs"]["item_prefix"](0, 3) == "[1/3]"


def test_batch_worker_posts_done(monkeypatch) -> None:
    items = [
        _ok_item("600519.SH"),
        BatchItem(request=AnalysisRequest(ticker="123456"), error="股票代码应为 6 位数字"),
    ]
    monkeypatch.setattr("src.app.gui.analyze_batch", lambda requests, **kw: items)
    app = _make_app()
    app._batch_worker(
        [AnalysisRequest(ticker="600519.SH"), AnalysisRequest(ticker="123456")]
    )
    assert app.events.get() == ("batch_started", 2)
    assert app.events.get() == ("batch_done", items)
    assert app.events.empty()


def test_batch_worker_posts_cancelled_when_event_set(monkeypatch) -> None:
    items = [_ok_item("600519.SH")]
    app = _make_app()

    def fake_batch(requests, **kw):
        app.cancel_event.set()
        return items

    monkeypatch.setattr("src.app.gui.analyze_batch", fake_batch)
    app._batch_worker([AnalysisRequest(ticker="600519.SH")])
    assert app.events.get() == ("batch_started", 1)
    assert app.events.get() == ("batch_cancelled", items)
    assert app.events.empty()


def test_batch_worker_posts_failed(monkeypatch) -> None:
    def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr("src.app.gui.analyze_batch", boom)
    app = _make_app()
    app._batch_worker([AnalysisRequest(ticker="600519.SH")])
    assert app.events.get() == ("batch_started", 1)
    assert app.events.get() == ("failed", "boom")
    assert app.events.empty()


def test_batch_done_event_updates_ui(tmp_path) -> None:
    out = tmp_path / "a.md"
    ok_item = BatchItem(
        request=AnalysisRequest(ticker="600519.SH"),
        result=AnalysisResult(
            ticker="600519.SH",
            stock_name="贵州茅台",
            output_path=out,
            output_kind="markdown",
            elapsed_seconds=2.5,
        ),
    )
    err_item = BatchItem(
        request=AnalysisRequest(ticker="123456"),
        error="股票代码应为 6 位数字",
    )
    app = _make_app()
    app.events.put(("batch_done", [ok_item, err_item]))
    app._process_events()
    assert app.status_var.value == "批量完成：1/2 只成功"
    assert "✓ [1/2] 600519.SH 贵州茅台 — 2.5s" in app._preview_text.text
    assert str(out) in app._preview_text.text
    assert "✗ [2/2] 123456 — 股票代码应为 6 位数字" in app._preview_text.text
    assert app.open_button.state == "disabled"
    assert app.cancel_button.state == "disabled"
    assert app.analyze_button.state == "normal"
    assert app.stage_var.value == ""


def test_batch_cancelled_event_updates_ui() -> None:
    ok_item = _ok_item("600519.SH")
    cancelled_item = BatchItem(
        request=AnalysisRequest(ticker="000858.SZ"),
        error="用户取消",
    )
    app = _make_app()
    app.events.put(("batch_cancelled", [ok_item, cancelled_item]))
    app._process_events()
    assert app.status_var.value == "批量已取消：已分析 1/2 只"
    assert "✓ [1/2] 600519.SH" in app._preview_text.text
    assert "○ [2/2] 000858.SZ — 已取消" in app._preview_text.text
