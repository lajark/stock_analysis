"""Profile post-announcement returns for the controlled CNINFO/qfq sample.

This is an exploratory event study only.  It uses the first trading day after
the announcement as the execution boundary, does not claim abnormal returns,
and must not be used to promote event weights automatically.
"""

from __future__ import annotations

import argparse
import json
import sys
import zipfile
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.analysis.evidence_decision import (  # noqa: E402
    OFFICIAL_EVENT_POLARITY,
)

HORIZONS = (1, 5, 20)


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
            frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
            frame = frame.dropna(subset=["trade_date", "close"])
            frames[symbol] = frame.sort_values("trade_date").reset_index(drop=True)
    return dict(sorted(frames.items()))


def _event_rows(
    events: pd.DataFrame,
    frames: dict[str, pd.DataFrame],
) -> tuple[list[dict[str, Any]], int]:
    rows: list[dict[str, Any]] = []
    skipped = 0
    for raw in events.to_dict(orient="records"):
        symbol = str(raw.get("ts_code", ""))
        event_type = str(raw.get("event_type", "unknown"))
        polarity = OFFICIAL_EVENT_POLARITY.get(event_type)
        if not polarity or symbol not in frames:
            continue
        published = pd.to_datetime(raw.get("published_at"), errors="coerce")
        frame = frames[symbol]
        if pd.isna(published):
            skipped += 1
            continue
        dates = pd.DatetimeIndex(frame["trade_date"])
        prior_index = int(dates.searchsorted(published, side="left")) - 1
        start_index = prior_index + 1
        if prior_index < 0:
            skipped += 1
            continue
        returns: dict[str, float | None] = {}
        for horizon in HORIZONS:
            target_index = start_index + horizon - 1
            if target_index >= len(frame):
                returns[str(horizon)] = None
                continue
            returns[str(horizon)] = float(
                frame.iloc[target_index]["close"] / frame.iloc[prior_index]["close"] - 1.0
            )
        rows.append(
            {
                "event_id": str(raw.get("item_id", "")),
                "ts_code": symbol,
                "event_type": event_type,
                "polarity": polarity,
                "published_at": published.strftime("%Y-%m-%d"),
                "prior_trade_date": dates[prior_index].strftime("%Y-%m-%d"),
                "first_post_trade_date": (
                    dates[start_index].strftime("%Y-%m-%d")
                    if start_index < len(frame)
                    else ""
                ),
                "returns": returns,
            }
        )
    return rows, skipped


def _stats(rows: list[dict[str, Any]], key: str | None = None) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        group = str(row[key]) if key else "all"
        groups.setdefault(group, []).append(row)
    result: dict[str, Any] = {}
    for group, group_rows in sorted(groups.items()):
        horizon_stats: dict[str, Any] = {}
        for horizon in HORIZONS:
            values = [
                float(row["returns"][str(horizon)])
                for row in group_rows
                if row["returns"].get(str(horizon)) is not None
            ]
            horizon_stats[str(horizon)] = {
                "count": len(values),
                "mean": float(np.mean(values)) if values else None,
                "median": float(np.median(values)) if values else None,
                "positive_fraction": (
                    float(np.mean(np.asarray(values) > 0)) if values else None
                ),
            }
        result[group] = {"event_count": len(group_rows), "horizons": horizon_stats}
    return result


def evaluate(events_path: Path, zip_path: Path, as_of: str) -> dict[str, Any]:
    events = pd.read_csv(events_path, dtype=str).fillna("")
    events["published_at"] = pd.to_datetime(events["published_at"], errors="coerce")
    cutoff = pd.to_datetime(as_of, errors="coerce")
    events = events.loc[events["published_at"].notna()]
    if pd.notna(cutoff):
        events = events.loc[events["published_at"] <= cutoff]
    frames = _load_qfq_frames(zip_path)
    rows, skipped = _event_rows(events, frames)
    return {
        "status": "pass" if rows else "insufficient",
        "as_of": as_of,
        "input": {
            "events": str(events_path.resolve()),
            "daily_archive": str(zip_path.resolve()),
            "symbols": sorted(frames),
            "event_rows_after_cutoff": int(len(events)),
            "recognized_events_in_sample": int(
                events["event_type"].isin(OFFICIAL_EVENT_POLARITY).sum()
            ),
            "usable_event_windows": len(rows),
            "skipped_events": skipped,
        },
        "overall": _stats(rows),
        "by_polarity": _stats(rows, "polarity"),
        "by_event_type": _stats(rows, "event_type"),
        "method": {
            "horizons_trading_days": list(HORIZONS),
            "boundary": "previous trading close to first trading day after publication",
            "price_basis": "qfq close",
            "abnormal_return_adjustment": False,
        },
        "limitations": [
            "未扣除市场/行业基准异常收益，不能解释为公告因果效应",
            "公告时间只有日期，采用保守的公告日后首个交易日边界，可能损失盘中信息",
            "样本仅覆盖 6 只股票和固定关键词事件，不能自动提升 official_event 权重",
            "unknown 公告未参与方向统计，避免把未知文本当作中性之外的方向证据",
        ],
    }


def _markdown(report: dict[str, Any]) -> str:
    overall = report["overall"]["all"]["horizons"]
    polarity = report["by_polarity"]
    lines = [
        "# CNINFO 公告后收益剖面",
        "",
        (
            f"as-of：`{report['as_of']}`；事件窗口："
            f"{report['input']['usable_event_windows']} 条；"
            f"跳过 {report['input']['skipped_events']} 条。"
        ),
        "",
        "## 总体统计",
        "",
        f"- 1/5/20 个交易日的样本数：{[overall[str(h)]['count'] for h in HORIZONS]}。",
        (
            f"- 1 日收益中位数：{overall['1']['median']:.4%}；"
            f"5 日：{overall['5']['median']:.4%}；"
            f"20 日：{overall['20']['median']:.4%}。"
        ),
        f"- 正负事件分组：{ {group: value['event_count'] for group, value in polarity.items()} }。",
        "",
        "## 结论边界",
        "",
        "- 这是公告层可回放性的事件剖面，不是策略回测，也没有做基准异常收益调整。",
        "- 结果仅用于决定是否继续扩大样本和补充基准，不自动修改证据权重或投资决策。",
        "",
        "## 方法与限制",
        "",
    ]
    lines.extend(f"- {item}" for item in report["limitations"])
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="评估 CNINFO 固定事件的公告后收益剖面")
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--zip", type=Path, required=True)
    parser.add_argument("--as-of", default="2025-12-31")
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-md", type=Path, required=True)
    args = parser.parse_args()
    report = evaluate(args.events, args.zip, args.as_of)
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    args.output_md.write_text(_markdown(report), encoding="utf-8")
    print(json.dumps(report["input"], ensure_ascii=False, indent=2))
    return 0 if report["status"] != "insufficient" else 1


if __name__ == "__main__":
    raise SystemExit(main())
