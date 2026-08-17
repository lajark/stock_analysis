"""Benchmark deterministic multi-symbol optimization resource usage locally."""

from __future__ import annotations

import argparse
import json
import sys
import time
import tracemalloc
import zipfile
from pathlib import Path
from typing import Any

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.analysis.backtest import optimize_ma_cross_multi  # noqa: E402

GRID = {"ma_fast": [5, 10, 20, 30], "ma_slow": [30, 60, 120]}


def _load_frames(zip_path: Path) -> dict[str, pd.DataFrame]:
    frames: dict[str, pd.DataFrame] = {}
    with zipfile.ZipFile(zip_path) as archive:
        for member in archive.namelist():
            if not member.endswith("/daily_qfq.csv"):
                continue
            symbol = member.split("/")[-2]
            frame = pd.read_csv(archive.open(member), dtype={"trade_date": str})
            frame["trade_date"] = pd.to_datetime(
                frame["trade_date"], format="%Y%m%d", errors="coerce"
            )
            frames[symbol] = frame.drop(columns=["ts_code"], errors="ignore").sort_values(
                "trade_date"
            )
    return dict(sorted(frames.items()))


def benchmark(zip_path: Path, *, initial_cash: float) -> dict[str, Any]:
    frames = _load_frames(zip_path)
    tracemalloc.start()
    started = time.perf_counter()
    result = optimize_ma_cross_multi(
        frames,
        GRID,
        train_size=252,
        validation_size=63,
        test_size=63,
        step_size=63,
        objective="robust",
        initial_cash=initial_cash,
        min_trades=1,
        min_successful_symbols=4,
        max_trials=16,
        adjustment="qfq",
    )
    elapsed_ms = int(round((time.perf_counter() - started) * 1000))
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    trial_count = int(result.aggregate.get("trial_count", 0))
    return {
        "status": "pass",
        "input": {
            "archive": str(zip_path.resolve()),
            "symbol_count": len(frames),
            "rows_by_symbol": {symbol: int(len(frame)) for symbol, frame in frames.items()},
            "max_trials": 16,
        },
        "runtime": {
            "elapsed_ms": elapsed_ms,
            "trial_count": trial_count,
            "trials_per_second": trial_count / (elapsed_ms / 1000) if elapsed_ms else None,
            "reported_optimizer_elapsed_ms": result.aggregate.get("elapsed_ms"),
        },
        "memory": {
            "tracemalloc_current_bytes": int(current),
            "tracemalloc_peak_bytes": int(peak),
            "tracemalloc_peak_mib": round(peak / 1024 / 1024, 3),
            "measurement_scope": "Python allocations only; native extensions may not be included",
        },
        "parameter_stability": result.parameter_stability,
        "decision": "keep_serial_and_budgeted",
        "limitations": [
            "基准只代表当前 Windows 环境和 6 只受控样本",
            "tracemalloc 不覆盖所有原生扩展内存，不能替代系统级监控",
            "开启 tracemalloc 的耗时包含追踪开销，生产运行应另行做无追踪基准",
            "资源基准不改变参数稳定性未通过的结论",
        ],
    }


def _markdown(report: dict[str, Any]) -> str:
    runtime = report["runtime"]
    memory = report["memory"]
    stability = report["parameter_stability"]
    return "\n".join(
        [
            "# 回测优化资源基准",
            "",
            (
                f"- 股票数：{report['input']['symbol_count']}；"
                f"总试验次数：{runtime['trial_count']}；"
                f"max_trials：{report['input']['max_trials']}。"
            ),
            (
                f"- 总耗时：{runtime['elapsed_ms']} ms；"
                f"吞吐约 {runtime['trials_per_second']:.1f} trials/s。"
            ),
            f"- tracemalloc 峰值：{memory['tracemalloc_peak_mib']} MiB（仅 Python 分配）。",
            (
                f"- 参数稳定性：`{stability['stable']}`，"
                f"主参数 `{stability.get('dominant_key')}`，"
                f"频率 `{stability.get('dominant_frequency')}`。"
            ),
            (
                "- 资源策略：保持串行、设置 max_trials，并保留耗时/"
                "试验次数审计字段；不因单次基准擅自并发化。"
            ),
            "- 注意：tracemalloc 会放大耗时；本报告用于资源压力基准，不作为生产性能基线。",
            "",
            "## 限制",
            "",
            *[f"- {item}" for item in report["limitations"]],
            "",
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="基准回测优化的时间和内存使用")
    parser.add_argument("--zip", type=Path, required=True)
    parser.add_argument("--initial-cash", type=float, default=2_000_000.0)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-md", type=Path, required=True)
    args = parser.parse_args()
    report = benchmark(args.zip, initial_cash=args.initial_cash)
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    args.output_md.write_text(_markdown(report), encoding="utf-8")
    print(json.dumps(report["runtime"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
