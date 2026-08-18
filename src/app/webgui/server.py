"""Local HTTP API + static server for the Liquid Glass web GUI.

Serves the web frontend and exposes a small JSON API over the existing
service / backtest / config layers. Progress events are pushed to the
frontend over Server-Sent Events (SSE). The server binds to 127.0.0.1 only,
on an ephemeral port, and never exposes secrets beyond the local machine.

No third-party HTTP framework is used: everything is standard library so the
web GUI adds no server dependency beyond ``pywebview`` (the window shell).
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import urllib.parse
import urllib.request
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from loguru import logger

from src.analysis.backtest import (
    BacktestSpec,
    CostModel,
    optimize_ma_cross,
    run_backtest,
)
from src.analysis.strategies import (
    STRATEGY_TEMPLATE,
    load_strategy,
    reset_user_strategy,
    save_user_strategy,
    strategy_source,
    user_strategy_file,
)
from src.app.backtest_records import persist_backtest_run
from src.app.help_text import USAGE_GUIDE_MD
from src.app.service import (
    MODES,
    STAGES,
    AnalysisCancelledError,
    AnalysisRequest,
    analyze_batch,
    analyze_stock,
    validate_ticker,
)
from src.app.update_check import check_for_updates, local_version
from src.config import get_config, get_user_settings, save_user_settings
from src.data.gateway import DataGateway
from src.runtime_paths import user_data_root

STATIC_DIR = Path(__file__).resolve().parent / "static"

# Hosts allowed to provide installers (the app's own GitHub/Gitee releases).
# ``STOCK_ANALYSIS_UPDATE_ALLOWED_HOSTS`` (comma-separated) extends the allow
# list — primarily for testing the update flow against a local/self-hosted
# mirror (same override mechanism as ``STOCK_ANALYSIS_UPDATE_URLS``).
_UPDATE_ALLOWED_HOSTS = {
    "api.github.com",
    "github.com",
    "objects.githubusercontent.com",
    "release-assets.githubusercontent.com",
    "gitee.com",
}


def _update_allowed_hosts() -> set[str]:
    """Return installer hosts: built-in allow list plus env override."""
    hosts = set(_UPDATE_ALLOWED_HOSTS)
    override = os.environ.get("STOCK_ANALYSIS_UPDATE_ALLOWED_HOSTS", "").strip()
    for part in override.split(","):
        part = part.strip()
        if part:
            hosts.add(part)
    return hosts


# ---------------------------------------------------------------------------
# Event bus (SSE fan-out)
# ---------------------------------------------------------------------------


class EventBus:
    """Thread-safe fan-out of JSON events to SSE subscribers."""

    def __init__(self) -> None:
        self._subscribers: list[queue.Queue[dict[str, Any]]] = []
        self._lock = threading.Lock()

    def subscribe(self) -> queue.Queue[dict[str, Any]]:
        subscriber: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=512)
        with self._lock:
            self._subscribers.append(subscriber)
        return subscriber

    def unsubscribe(self, subscriber: queue.Queue[dict[str, Any]]) -> None:
        with self._lock:
            try:
                self._subscribers.remove(subscriber)
            except ValueError:
                pass

    def publish(self, event: dict[str, Any]) -> None:
        with self._lock:
            subscribers = list(self._subscribers)
        for subscriber in subscribers:
            try:
                subscriber.put_nowait(event)
            except queue.Full:
                pass


class ServerContext:
    """Shared mutable state for one web GUI server process."""

    def __init__(self, static_dir: Path) -> None:
        self.static_dir = static_dir
        self.bus = EventBus()
        self.cancel_lock = threading.Lock()
        self.cancel_event: threading.Event | None = None
        self.busy = False

    def begin_job(self) -> bool:
        """Reserve the single worker slot; returns False when a job is running."""
        with self.cancel_lock:
            if self.busy:
                return False
            self.busy = True
            self.cancel_event = threading.Event()
            return True

    def end_job(self) -> None:
        with self.cancel_lock:
            self.busy = False
            self.cancel_event = None

    def cancel(self) -> None:
        with self.cancel_lock:
            if self.cancel_event is not None:
                self.cancel_event.set()


CTX = ServerContext(STATIC_DIR)


# ---------------------------------------------------------------------------
# Result formatting (shared text builders)
# ---------------------------------------------------------------------------


def format_backtest_result(
    result, ticker: str, start: str, end: str, ma_fast: int, ma_slow: int
) -> str:
    lines = [
        f"回测结果 — {ticker} ({start}~{end})",
        "=" * 40,
        f"总收益率：{result.total_return * 100:.2f}%",
        f"年化收益率：{result.annualized_return * 100:.2f}%",
        f"基准收益（买入持有）：{result.benchmark_return * 100:.2f}%",
        f"最大回撤：{result.max_drawdown * 100:.2f}%",
        f"夏普比率：{result.sharpe:.4f}",
        f"索提诺比率：{result.sortino:.4f}",
        f"交易次数：{result.trade_count}",
        f"胜率：{result.win_rate * 100:.2f}%",
        f"持仓暴露：{result.exposure * 100:.1f}%",
        f"期末持仓：{result.open_shares} 股",
        f"策略版本：{result.strategy_version}",
        f"数据哈希：{result.data_hash[:16]}…",
    ]
    if result.trade_count == 0:
        lines.append("")
        if result.open_shares > 0:
            lines.append(
                "[提示] 策略在样本期内买入后一直持有至期末（未完成卖出），"
                "因此没有完整交易记录；期末持仓反映的是未实现的浮盈/浮亏。"
            )
        else:
            lines.append(
                "[提示] 样本期内没有触发 MA("
                f"{ma_fast},{ma_slow}) 买入信号，全程空仓，"
                "收益指标不代表策略有效性。"
            )
        lines.append(
            "  建议：缩短窗口以包含明显趋势行情，或调小均线周期"
            "（如 5/20、10/30），或换一只趋势更清晰的股票。"
        )
    if result.warnings:
        lines.append("")
        lines.extend(f"· {warning}" for warning in result.warnings)
    return "\n".join(lines)


def format_optimize_result(result, ticker: str, start: str, end: str, objective: str) -> str:
    lines = [
        f"参数优化结果 — {ticker} ({start}~{end})",
        "=" * 40,
        f"优化目标：{objective}",
        f"遍历组合：{len(result.candidates)} 组",
        "选中参数："
        + "、".join(
            f"{name}={result.selected_parameters[name]}"
            for name in result.selected_parameters
        ),
        "分段表现：train_sharpe="
        f"{result.train.sharpe:.4f} / val={result.validation.sharpe:.4f} "
        f"/ test={result.test.sharpe:.4f}",
        "",
    ]
    all_zero = all(cand["trade_count"] == 0 for cand in result.candidates)
    for i, cand in enumerate(result.candidates[:10], 1):
        params = "、".join(f"{k}={v}" for k, v in cand["parameters"].items())
        lines.append(
            f"  [{i}] {params} sharpe={cand['sharpe']:.4f} "
            f"return={cand['total_return'] * 100:.2f}% "
            f"trades={cand['trade_count']}"
        )
    if all_zero:
        lines.append("")
        lines.append(
            "[提示] 所有参数组合在训练段都没有触发交易信号，"
            "本次优化未产生有效结论。请缩小均线周期或调整回测窗口。"
        )
    for segment in (result.train, result.validation, result.test):
        if segment.warnings:
            lines.append("")
            lines.extend(f"· {warning}" for warning in segment.warnings)
    return "\n".join(lines)


def _batch_item_dict(item) -> dict[str, Any]:
    if item.result is not None:
        return {
            "ticker": item.result.ticker,
            "name": item.result.stock_name,
            "status": "ok",
            "elapsed": round(item.result.elapsed_seconds, 2),
            "output_path": str(item.result.output_path),
        }
    return {
        "ticker": item.request.ticker,
        "name": "",
        "status": "error",
        "error": item.error or "未知错误",
    }


def _settings_payload() -> dict[str, Any]:
    config = get_config()
    return {
        "settings": get_user_settings(),
        "effective_ma_periods": list(config.analysis.ma_periods),
        "modes": [
            {"key": key, "desc": info["desc"], "deep": info["deep"], "kb": info["kb"]}
            for key, info in MODES.items()
        ],
        "stages": list(STAGES),
        "strategy": load_strategy().to_dict(),
        "version": local_version(),
        "max_tokens": config.llm.max_tokens,
        "data_provider": config.data_provider,
    }


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------


class Handler(BaseHTTPRequestHandler):
    """JSON API + static file handler. Stateless; uses the module CTX."""

    server_version = "StockAnalysisWeb/1.0"

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - base class
        return  # keep the console clean

    # -- helpers -----------------------------------------------------------

    def _send_bytes(self, status: int, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _send_json(self, status: int, payload: Any) -> None:
        self._send_bytes(
            status,
            "application/json; charset=utf-8",
            json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        )

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(raw.decode("utf-8") or "{}")
            return data if isinstance(data, dict) else {}
        except json.JSONDecodeError:
            return {}

    def _send_static(self, rel: str) -> None:
        if rel in {"", "index.html"}:
            rel = "index.html"
        if ".." in rel or rel.startswith("/") or "\\" in rel:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "非法路径"})
            return
        path = (CTX.static_dir / rel).resolve()
        if not path.is_file() or CTX.static_dir.resolve() not in path.parents:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "文件不存在"})
            return
        content_type = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".json": "application/json; charset=utf-8",
            ".png": "image/png",
            ".svg": "image/svg+xml",
            ".ico": "image/x-icon",
        }.get(path.suffix.lower(), "application/octet-stream")
        self._send_bytes(HTTPStatus.OK, content_type, path.read_bytes())

    # -- GET routes ---------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 - base class method name
        parsed = urllib.parse.urlparse(self.path)
        route = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        if route in {"/", "/index.html"}:
            self._send_static("index.html")
            return
        if route.startswith("/static/"):
            self._send_static(route[len("/static/"):])
            return

        if route == "/api/bootstrap":
            self._send_json(HTTPStatus.OK, _settings_payload())
            return
        if route == "/api/settings":
            self._send_json(HTTPStatus.OK, _settings_payload())
            return
        if route == "/api/strategy":
            self._send_json(
                HTTPStatus.OK,
                {
                    "strategy": load_strategy().to_dict(),
                    "source": strategy_source(),
                    "template": STRATEGY_TEMPLATE,
                    "user_file": str(user_strategy_file()),
                },
            )
            return
        if route == "/api/help":
            self._send_json(HTTPStatus.OK, {"guide": USAGE_GUIDE_MD})
            return
        if route == "/api/check_update":
            local, results, download_urls, release_page = check_for_updates()
            self._send_json(
                HTTPStatus.OK,
                {
                    "local": local,
                    "results": results,
                    "download_urls": download_urls,
                    "download_url": download_urls[0] if download_urls else release_page,
                    "release_page": release_page,
                },
            )
            return
        if route == "/api/chart":
            name = query.get("path", [""])[0]
            if not name or "/" in name or "\\" in name:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "非法图表路径"})
                return
            chart_dir = get_config().output_dir / "charts"
            path = (chart_dir / name).resolve()
            if not path.is_file() or chart_dir.resolve() not in path.parents:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "图表不存在"})
                return
            self._send_bytes(
                HTTPStatus.OK, "text/html; charset=utf-8", path.read_bytes()
            )
            return
        if route == "/api/events":
            self._stream_events()
            return

        self._send_json(HTTPStatus.NOT_FOUND, {"error": "接口不存在"})

    def _stream_events(self) -> None:
        """Server-Sent Events stream (kept open; heartbeat every 15s)."""
        subscriber = CTX.bus.subscribe()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        try:
            while True:
                try:
                    event = subscriber.get(timeout=15)
                    payload = json.dumps(event, ensure_ascii=False)
                    self.wfile.write(f"data: {payload}\n\n".encode())
                    self.wfile.flush()
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            CTX.bus.unsubscribe(subscriber)

    # -- POST routes ----------------------------------------------------------

    def do_POST(self) -> None:  # noqa: N802 - base class method name
        route = urllib.parse.urlparse(self.path).path
        body = self._read_json()

        if route == "/api/analyze":
            self._post_analyze(body)
            return
        if route == "/api/cancel":
            CTX.cancel()
            self._send_json(HTTPStatus.OK, {"cancelled": True})
            return
        if route == "/api/backtest":
            self._post_backtest(body)
            return
        if route == "/api/optimize":
            self._post_optimize(body)
            return
        if route == "/api/adopt":
            self._post_adopt(body)
            return
        if route == "/api/settings":
            self._post_settings(body)
            return
        if route == "/api/strategy":
            self._post_strategy(body)
            return
        if route == "/api/open_dir":
            self._post_open_dir()
            return
        if route == "/api/open_file":
            self._post_open_file(body)
            return
        if route == "/api/update/install":
            self._post_update_install(body)
            return

        self._send_json(HTTPStatus.NOT_FOUND, {"error": "接口不存在"})

    # -- job workers ----------------------------------------------------------

    def _post_analyze(self, body: dict[str, Any]) -> None:
        raw_tickers = [t.strip() for t in body.get("tickers", []) if t.strip()]
        if not raw_tickers:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "至少需要一只股票代码"})
            return
        # Normalize (002001 -> 002001.SZ, 430047 -> 430047.BJ) at the entry,
        # exactly like the desktop GUI and the backtest/optimize endpoints, so
        # the primary source and per-symbol cache key are consistent; reject
        # invalid codes before starting a job.
        try:
            tickers = [validate_ticker(t) for t in raw_tickers]
        except Exception as exc:  # noqa: BLE001 - surface a readable message
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return
        mode = str(body.get("mode", "quick"))
        use_llm = bool(body.get("use_llm", True))
        chart = bool(body.get("chart", False))
        if not CTX.begin_job():
            self._send_json(HTTPStatus.CONFLICT, {"error": "已有任务在运行"})
            return
        self._send_json(HTTPStatus.ACCEPTED, {"started": True, "count": len(tickers)})

        def run() -> None:
            try:
                if len(tickers) == 1:
                    self._run_single(tickers[0], mode, use_llm, chart)
                else:
                    self._run_batch(tickers, mode, use_llm, chart)
            finally:
                CTX.end_job()

        threading.Thread(target=run, daemon=True).start()

    def _run_single(self, ticker: str, mode: str, use_llm: bool, chart: bool) -> None:
        request = AnalysisRequest(ticker=ticker, mode=mode, use_llm=use_llm, chart=chart)
        try:
            result = analyze_stock(
                request,
                progress=lambda m: CTX.bus.publish({"type": "progress", "payload": m}),
                cancel_event=CTX.cancel_event,
                stage_progress=lambda s, st, m: CTX.bus.publish(
                    {"type": "stage", "stage": s, "status": st, "message": m}
                ),
                token_callback=lambda t: CTX.bus.publish({"type": "token", "payload": t}),
            )
            try:
                content = result.output_path.read_text(encoding="utf-8")
            except OSError:
                content = f"结果已保存至：\n{result.output_path}"
            chart_name = result.chart_path.name if result.chart_path else None
            CTX.bus.publish(
                {
                    "type": "finished",
                    "ticker": result.ticker,
                    "name": result.stock_name,
                    "elapsed": round(result.elapsed_seconds, 2),
                    "content": content,
                    "chart_name": chart_name,
                    "output_path": str(result.output_path),
                }
            )
        except AnalysisCancelledError:
            CTX.bus.publish({"type": "cancelled", "payload": "用户取消"})
        except Exception as exc:  # noqa: BLE001 - surface to UI, never crash server
            logger.exception("Web analysis failed")
            CTX.bus.publish({"type": "failed", "payload": str(exc)})

    def _run_batch(self, tickers: list[str], mode: str, use_llm: bool, chart: bool) -> None:
        requests = [
            AnalysisRequest(ticker=t, mode=mode, use_llm=use_llm, chart=chart)
            for t in tickers
        ]
        try:
            items = analyze_batch(
                requests,
                max_workers=get_config().batch.max_workers,
                cancel_event=CTX.cancel_event,
                stage_progress=lambda s, st, m: CTX.bus.publish(
                    {"type": "stage", "stage": s, "status": st, "message": m}
                ),
                item_prefix=lambda i, c: f"[{i + 1}/{c}]",
            )
            CTX.bus.publish(
                {
                    "type": "batch_done",
                    "items": [_batch_item_dict(item) for item in items],
                    "cancelled": bool(CTX.cancel_event and CTX.cancel_event.is_set()),
                }
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Web batch failed")
            CTX.bus.publish({"type": "failed", "payload": str(exc)})

    def _post_backtest(self, body: dict[str, Any]) -> None:
        raw_ticker = str(body.get("ticker", "")).strip()
        start = str(body.get("start", "")).strip()
        end = str(body.get("end", "")).strip()
        try:
            ma_fast = int(body.get("ma_fast", 20))
            ma_slow = int(body.get("ma_slow", 60))
        except (TypeError, ValueError):
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "均线周期必须为整数"})
            return
        if not raw_ticker:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "缺少股票代码"})
            return
        # Normalize (002001 -> 002001.SZ) exactly like the analysis path so the
        # primary Tushare source and the per-symbol cache key are consistent.
        try:
            ticker = validate_ticker(raw_ticker)
        except Exception as exc:  # noqa: BLE001 - surface a readable message
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return
        if not CTX.begin_job():
            self._send_json(HTTPStatus.CONFLICT, {"error": "已有任务在运行"})
            return
        self._send_json(HTTPStatus.ACCEPTED, {"started": True})

        def run() -> None:
            try:
                strategy = load_strategy()
                gw = DataGateway()
                data = gw.fetch_daily_bars(ticker, start, end, adjustment="none")
                daily = data.daily
                if daily is None or daily.empty:
                    CTX.bus.publish({"type": "bt_error", "payload": "未获取到行情数据"})
                    return
                result = run_backtest(
                    daily,
                    spec=BacktestSpec(
                        ma_fast=ma_fast,
                        ma_slow=ma_slow,
                        initial_cash=100_000,
                        costs=CostModel(),
                    ),
                    strategy=strategy,
                )
                persist_backtest_run(
                    {
                        "ticker": ticker,
                        "start_date": start,
                        "end_date": end,
                        "strategy": strategy.name,
                        "ma_fast": ma_fast,
                        "ma_slow": ma_slow,
                    },
                    result,
                )
                CTX.bus.publish(
                    {
                        "type": "bt_result",
                        "kind": "backtest",
                        "text": format_backtest_result(
                            result, ticker, start, end, ma_fast, ma_slow
                        ),
                        "strategy": strategy.to_dict(),
                        "result": result.to_dict(include_curve=False),
                    }
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception("Web backtest failed")
                CTX.bus.publish({"type": "bt_error", "payload": f"回测失败：{exc}"})
            finally:
                CTX.end_job()

        threading.Thread(target=run, daemon=True).start()

    def _post_optimize(self, body: dict[str, Any]) -> None:
        raw_ticker = str(body.get("ticker", "")).strip()
        start = str(body.get("start", "")).strip()
        end = str(body.get("end", "")).strip()
        objective = str(body.get("objective", "sharpe"))
        grids = body.get("grids")
        if not isinstance(grids, dict) or not grids:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "缺少参数网格"})
            return
        try:
            parsed_grids = {
                name: [int(v) for v in values if str(v).strip()]
                for name, values in grids.items()
            }
        except (TypeError, ValueError):
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "参数网格必须为整数列表"})
            return
        if not raw_ticker:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "缺少股票代码"})
            return
        # Normalize (002001 -> 002001.SZ) like the analysis path.
        try:
            ticker = validate_ticker(raw_ticker)
        except Exception as exc:  # noqa: BLE001 - surface a readable message
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return
        if not CTX.begin_job():
            self._send_json(HTTPStatus.CONFLICT, {"error": "已有任务在运行"})
            return
        self._send_json(HTTPStatus.ACCEPTED, {"started": True})

        def run() -> None:
            try:
                strategy = load_strategy()
                gw = DataGateway()
                data = gw.fetch_daily_bars(ticker, start, end, adjustment="none")
                daily = data.daily
                if daily is None or daily.empty:
                    CTX.bus.publish({"type": "bt_error", "payload": "未获取到行情数据"})
                    return
                result = optimize_ma_cross(
                    daily,
                    parsed_grids,
                    objective=objective,
                    initial_cash=100_000,
                    costs=CostModel(),
                    strategy=strategy,
                )
                persist_backtest_run(
                    {
                        "ticker": ticker,
                        "start_date": start,
                        "end_date": end,
                        "strategy": strategy.name,
                        "objective": objective,
                        "grids": parsed_grids,
                    },
                    result,
                )
                CTX.bus.publish(
                    {
                        "type": "bt_result",
                        "kind": "optimize",
                        "text": format_optimize_result(
                            result, ticker, start, end, objective
                        ),
                        "strategy": strategy.to_dict(),
                        "result": result.to_dict(),
                    }
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception("Web optimize failed")
                CTX.bus.publish({"type": "bt_error", "payload": f"优化失败：{exc}"})
            finally:
                CTX.end_job()

        threading.Thread(target=run, daemon=True).start()

    def _post_adopt(self, body: dict[str, Any]) -> None:
        try:
            ma_fast = int(body["ma_fast"])
            ma_slow = int(body["ma_slow"])
        except (KeyError, TypeError, ValueError):
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "缺少 ma_fast/ma_slow"})
            return
        if not (0 < ma_fast < ma_slow):
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "参数无效"})
            return
        values = get_user_settings()
        try:
            save_user_settings(
                tushare_token=values["TUSHARE_TOKEN"],
                llm_api_key=values["LLM_API_KEY"],
                llm_base_url=values["LLM_BASE_URL"],
                llm_model=values["LLM_MODEL"],
                llm_model_deep=values["LLM_MODEL_DEEP"],
                mairui_licence=values.get("MAIRUI_LICENCE", ""),
                biyingapi_appcode=values.get("BIYINGAPI_APPCODE", ""),
                analysis_ma_periods=f"{ma_fast},{ma_slow}",
                use_analysis_ma_override="1",
            )
        except Exception as exc:  # noqa: BLE001
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return
        self._send_json(
            HTTPStatus.OK,
            {
                "adopted": True,
                "ma_periods": [ma_fast, ma_slow],
                "effective_ma_periods": list(get_config().analysis.ma_periods),
            },
        )

    def _post_settings(self, body: dict[str, Any]) -> None:
        values = get_user_settings()
        try:
            save_user_settings(
                tushare_token=str(body.get("tushare_token", values["TUSHARE_TOKEN"])),
                llm_api_key=str(body.get("llm_api_key", values["LLM_API_KEY"])),
                llm_base_url=str(body.get("llm_base_url", values["LLM_BASE_URL"])),
                llm_model=str(body.get("llm_model", values["LLM_MODEL"])),
                llm_model_deep=str(body.get("llm_model_deep", values["LLM_MODEL_DEEP"])),
                mairui_licence=str(
                    body.get("mairui_licence", values.get("MAIRUI_LICENCE", ""))
                ),
                biyingapi_appcode=str(
                    body.get("biyingapi_appcode", values.get("BIYINGAPI_APPCODE", ""))
                ),
                analysis_ma_periods=str(
                    body.get("analysis_ma_periods", values.get("ANALYSIS_MA_PERIODS", ""))
                ),
                use_analysis_ma_override=str(
                    body.get(
                        "use_analysis_ma_override",
                        values.get("USE_ANALYSIS_MA_OVERRIDE", "0"),
                    )
                ),
            )
        except Exception as exc:  # noqa: BLE001
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return
        self._send_json(
            HTTPStatus.OK,
            {
                "saved": True,
                "effective_ma_periods": list(get_config().analysis.ma_periods),
            },
        )

    def _post_open_dir(self) -> None:
        import os

        output_dir = get_config().output_dir
        output_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(str(output_dir))  # type: ignore[attr-defined]
        except OSError as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return
        self._send_json(HTTPStatus.OK, {"opened": True})

    def _post_open_file(self, body: dict[str, Any]) -> None:
        import os

        path = str(body.get("path", "")).strip()
        if not path:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "缺少文件路径"})
            return
        target = Path(path).resolve()
        output_root = get_config().output_dir.resolve()
        if output_root not in target.parents:
            self._send_json(HTTPStatus.FORBIDDEN, {"error": "仅允许打开输出目录内文件"})
            return
        if not target.is_file():
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "文件不存在"})
            return
        try:
            os.startfile(str(target))  # type: ignore[attr-defined]
        except OSError as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return
        self._send_json(HTTPStatus.OK, {"opened": True})

    def _post_update_install(self, body: dict[str, Any]) -> None:
        """Download the installer and launch it silently; app exits afterwards.

        Accepts a single legacy ``url`` or a ``urls`` fallback list (Gitee
        first). The installer only ever writes to the install directory
        (``%LOCALAPPDATA%\\Programs\\StockAnalysis``); the user data directory
        (``%LOCALAPPDATA%\\StockAnalysis``) is never touched by an update.
        """
        urls: list[str] = []
        single = str(body.get("url", "") or "").strip()
        if single:
            urls.append(single)
        for candidate in body.get("urls") or []:
            candidate = str(candidate).strip()
            if candidate and candidate not in urls:
                urls.append(candidate)
        if not urls:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "缺少安装包下载地址"})
            return
        for url in urls:
            host = urllib.parse.urlparse(url).hostname or ""
            if host not in _update_allowed_hosts():
                self._send_json(HTTPStatus.FORBIDDEN, {"error": "仅允许官方发布源的安装包"})
                return
        if not CTX.begin_job():
            self._send_json(HTTPStatus.CONFLICT, {"error": "已有任务在运行"})
            return
        self._send_json(HTTPStatus.ACCEPTED, {"started": True, "urls": urls})
        threading.Thread(
            target=self._download_and_install, args=(urls,), daemon=True
        ).start()

    def _download_and_install(self, urls: list[str]) -> None:
        try:
            update_dir = user_data_root() / "updates"
            update_dir.mkdir(parents=True, exist_ok=True)
            target: Path | None = None
            last_error: Exception | None = None
            for url in urls:
                try:
                    target = self._download_installer(url, update_dir)
                    break
                except Exception as exc:  # noqa: BLE001
                    last_error = exc
                    # Silent failover to the next source: log only, do not
                    # surface a transient failure to the user.
                    logger.warning("安装包下载失败，切换备用源 %s：%s", url, exc)
            if target is None:
                raise last_error or RuntimeError("未找到可用的安装包下载源")

            # 静默安装（Inno Setup 参数）；安装器不触碰用户数据目录。
            creationflags = 0
            if hasattr(subprocess, "DETACHED_PROCESS"):
                creationflags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
            subprocess.Popen(
                [str(target), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"],
                creationflags=creationflags,
                close_fds=True,
            )
            CTX.bus.publish({"type": "update_done", "path": str(target)})
        except Exception as exc:  # noqa: BLE001
            logger.exception("更新安装失败")
            CTX.bus.publish({"type": "update_error", "payload": str(exc)})
        finally:
            CTX.end_job()

    def _download_installer(self, url: str, update_dir: Path) -> Path:
        """Download a single installer candidate; raise on any failure."""
        filename = Path(urllib.parse.urlparse(url).path).name or "update.exe"
        target = update_dir / filename
        part = target.with_suffix(target.suffix + ".part")

        req = urllib.request.Request(
            url, headers={"User-Agent": "stock-analysis/1.0"}
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            total = int(resp.headers.get("Content-Length") or 0)
            received = 0
            with open(part, "wb") as out:
                while True:
                    chunk = resp.read(1 << 16)
                    if not chunk:
                        break
                    out.write(chunk)
                    received += len(chunk)
                    CTX.bus.publish(
                        {
                            "type": "update_progress",
                            "received": received,
                            "total": total,
                        }
                    )
        part.replace(target)
        if not target.is_file() or target.stat().st_size == 0:
            raise RuntimeError("安装包下载为空，已中止")
        return target

    def _post_strategy(self, body: dict[str, Any]) -> None:
        action = str(body.get("action", "save"))
        if action == "save":
            source = str(body.get("source_code", ""))
            if not source.strip():
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "策略源码不能为空"})
                return
            try:
                strategy = save_user_strategy(source)
            except Exception as exc:  # noqa: BLE001
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                return
            self._send_json(
                HTTPStatus.OK,
                {"saved": True, "strategy": strategy.to_dict()},
            )
            return
        if action == "reset":
            reset_user_strategy()
            self._send_json(
                HTTPStatus.OK,
                {"reset": True, "strategy": load_strategy().to_dict()},
            )
            return
        self._send_json(HTTPStatus.BAD_REQUEST, {"error": "未知操作"})


# ---------------------------------------------------------------------------
# Server lifecycle
# ---------------------------------------------------------------------------


class WebGuiServer:
    """Owns the background HTTP server thread."""

    def __init__(self, host: str = "127.0.0.1", port: int = 0) -> None:
        self._host = host
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.port = port

    def start(self) -> None:
        self._httpd = ThreadingHTTPServer((self._host, self.port), Handler)
        self.port = int(self._httpd.server_address[1])
        self._thread = threading.Thread(
            target=self._httpd.serve_forever, daemon=True, name="webgui-http"
        )
        self._thread.start()
        logger.info("Web GUI 服务器已启动：http://{}:{}", self._host, self.port)

    @property
    def url(self) -> str:
        return f"http://{self._host}:{self.port}/"

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
