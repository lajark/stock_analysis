"""Run a compact cross-symbol rolling backtest from the local audit ZIP.

The ZIP contains a controlled six-symbol qfq sample used for adjustment-factor
verification.  This script reuses it without extraction or network access.  It
is a validation sample, not a production cache promotion mechanism.
"""

from __future__ import annotations

import argparse
import json
import sys
import zipfile
from pathlib import Path
from typing import Any

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.analysis.backtest import (  # noqa: E402
    BacktestSpec,
    optimize_ma_cross_multi,
    run_backtest,
)

PARAMETER_GRID = {"ma_fast": [5, 10, 20, 30], "ma_slow": [30, 60, 120]}


def _load_qfq_frames(zip_path: Path) -> dict[str, pd.DataFrame]:
    frames: dict[str, pd.DataFrame] = {}
    with zipfile.ZipFile(zip_path) as archive:
        for member in archive.namelist():
            if not member.endswith("/daily_qfq.csv"):
                continue
            parts = member.split("/")
            if len(parts) < 3:
                continue
            symbol = parts[-2]
            frame = pd.read_csv(archive.open(member), dtype={"trade_date": str})
            frame["trade_date"] = pd.to_datetime(
                frame["trade_date"], format="%Y%m%d", errors="coerce"
            )
            frame = frame.dropna(subset=["trade_date"]).drop(columns=["ts_code"], errors="ignore")
            frames[symbol] = frame.sort_values("trade_date").reset_index(drop=True)
    if not frames:
        raise ValueError("ZIP 中未找到 adjustment_samples/*/daily_qfq.csv")
    return dict(sorted(frames.items()))


def _compact_backtest(result: Any) -> dict[str, Any]:
    payload = result.to_dict(include_curve=False)
    payload.pop("trades", None)
    return payload


def _compact_multi(result: Any) -> dict[str, Any]:
    raw = result.to_dict()
    per_symbol: dict[str, Any] = {}
    for symbol, summary in result.per_symbol.items():
        per_symbol[symbol] = {
            "status": summary.get("status"),
            "group": summary.get("group"),
            "selected_parameters": summary.get("selected_parameters"),
            "parameter_stability": summary.get("parameter_stability"),
            "aggregate": summary.get("aggregate"),
            "warnings": summary.get("warnings", []),
            "windows": [
                {
                    key: window.get(key)
                    for key in (
                        "window_index",
                        "status",
                        "selected_parameters",
                        "train_total_return",
                        "validation_total_return",
                        "test_total_return",
                        "train_trade_count",
                        "validation_trade_count",
                        "test_trade_count",
                    )
                }
                for window in summary.get("windows", [])
            ],
        }
    raw["per_symbol"] = per_symbol
    return raw


def evaluate(zip_path: Path, *, initial_cash: float) -> dict[str, Any]:
    frames = _load_qfq_frames(zip_path)
    states: dict[str, str] = {}
    baselines: dict[str, Any] = {}
    for symbol, frame in frames.items():
        train_end = min(252, len(frame) - 1)
        train_return = float(frame["close"].iloc[train_end] / frame["close"].iloc[0] - 1.0)
        states[symbol] = "train_trend_positive" if train_return >= 0 else "train_trend_negative"
        baselines[symbol] = _compact_backtest(
            run_backtest(
                frame,
                spec=BacktestSpec(
                    ma_fast=20,
                    ma_slow=60,
                    initial_cash=initial_cash,
                    adjustment="qfq",
                ),
            )
        )
    result = optimize_ma_cross_multi(
        frames,
        PARAMETER_GRID,
        train_size=252,
        validation_size=63,
        test_size=63,
        step_size=63,
        objective="robust",
        initial_cash=initial_cash,
        min_trades=1,
        min_successful_symbols=4,
        market_state_by_symbol=states,
        adjustment="qfq",
        max_trials=16,
    )
    return {
        "status": "pass",
        "data_source": {
            "archive": str(zip_path.resolve()),
            "dataset": "adjustment_samples/*/daily_qfq.csv",
            "symbols": list(frames),
            "rows_by_symbol": {symbol: int(len(frame)) for symbol, frame in frames.items()},
            "start": min(frame["trade_date"].min() for frame in frames.values()).strftime(
                "%Y-%m-%d"
            ),
            "end": max(frame["trade_date"].max() for frame in frames.values()).strftime(
                "%Y-%m-%d"
            ),
            "adjustment": "qfq",
        },
        "market_state_labels": states,
        "baseline_ma_20_60": baselines,
        "multi_symbol_optimization": _compact_multi(result),
        "decision": (
            "do_not_promote_parameters"
            if result.selected_parameters is None
            else "candidate_only_pending_full_universe_validation"
        ),
        "limitations": [
            "仅 6 只股票，样本来自复权核验 ZIP，不代表完整股票池",
            "市场状态标签是每只股票训练窗口收益的审计分组，不是独立市场指数状态",
            "滚动窗口交易次数不足或参数不稳定时，不输出可直接采用的参数",
            "该技术回测不能单独证明基本面或公告情绪来源具有增量价值",
        ],
    }


def _markdown(report: dict[str, Any]) -> str:
    data = report["data_source"]
    multi = report["multi_symbol_optimization"]
    stability = multi["parameter_stability"]
    aggregate = multi["aggregate"]
    lines = [
        "# 跨股票滚动回测评估",
        "",
        (
            f"样本：`{data['dataset']}`；日期 `{data['start']}` 至 "
            f"`{data['end']}`；复权口径 `{data['adjustment']}`。"
        ),
        "",
        "## 结果",
        "",
        f"- 股票数：{len(data['symbols'])}；各股票行数：{data['rows_by_symbol']}。",
        (
            f"- 成功优化股票：{aggregate['successful_symbols']}/"
            f"{aggregate['symbol_count']}；成功窗口：{aggregate['successful_windows']}。"
        ),
        (
            f"- 全局参数稳定性：`{stability['stable']}`；"
            f"主参数 `{stability.get('dominant_key')}`；"
            f"频率 `{stability.get('dominant_frequency')}`。"
        ),
        f"- 样本外 robust 目标中位数：{aggregate.get('test_objective_median')}。",
        f"- 参数结论：`{report['decision']}`。",
        "",
        "## 解释",
        "",
        "- 基线和滚动优化均保存成本模型、复权口径、数据哈希、分段交易次数和警告，完整细节见 JSON。",
        "- 参数稳定性不足时不把历史最优参数写回用户配置，避免把小样本拟合误当作可用策略。",
        "",
        "## 限制",
        "",
    ]
    lines.extend(f"- {item}" for item in report["limitations"])
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="使用本地审计 ZIP 做跨股票滚动回测")
    parser.add_argument("--zip", type=Path, required=True)
    parser.add_argument("--initial-cash", type=float, default=2_000_000.0)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-md", type=Path, required=True)
    args = parser.parse_args()
    report = evaluate(args.zip, initial_cash=args.initial_cash)
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    args.output_md.write_text(_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            report["multi_symbol_optimization"]["aggregate"],
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
