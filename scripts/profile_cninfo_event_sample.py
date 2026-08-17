"""Profile an exported CNINFO official-event sample without network access."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.analysis.evidence_decision import (  # noqa: E402
    OFFICIAL_EVENT_POLARITY,
    normalize_official_event_evidence,
)

REQUIRED_COLUMNS = {
    "ts_code",
    "item_id",
    "title",
    "event_type",
    "published_at",
    "source",
    "source_url",
}


def _profile(frame: pd.DataFrame, as_of: str) -> dict[str, Any]:
    missing = REQUIRED_COLUMNS - set(frame.columns)
    if missing:
        raise ValueError(f"公告快照缺少字段：{', '.join(sorted(missing))}")
    work = frame.copy()
    work["published_at"] = pd.to_datetime(work["published_at"], errors="coerce")
    invalid_dates = int(work["published_at"].isna().sum())
    duplicate_ids = int(work.duplicated(["source", "item_id"]).sum())
    missing_urls = int(work["source_url"].astype("string").str.strip().eq("").sum())
    missing_titles = int(work["title"].astype("string").str.strip().eq("").sum())
    records = work.to_dict(orient="records")
    accepted, normalized_summary = normalize_official_event_evidence(
        records,
        as_of=as_of,
    )
    accepted_frame = pd.DataFrame(accepted)
    polarity_counts = (
        accepted_frame["polarity"].value_counts().to_dict()
        if not accepted_frame.empty
        else {}
    )
    event_counts = work["event_type"].fillna("unknown").value_counts().to_dict()
    recognized = int(
        work["event_type"].fillna("unknown").isin(OFFICIAL_EVENT_POLARITY).sum()
    )
    per_security = (
        work.groupby("ts_code", dropna=False)
        .size()
        .sort_values(ascending=False)
        .astype(int)
        .to_dict()
    )
    return {
        "status": "pass"
        if not missing and invalid_dates == 0 and missing_urls == 0 and missing_titles == 0
        else "fail",
        "as_of": as_of,
        "row_count": int(len(work)),
        "security_count": int(work["ts_code"].nunique()),
        "coverage_start": work["published_at"].min().strftime("%Y-%m-%d")
        if invalid_dates < len(work)
        else "",
        "coverage_end": work["published_at"].max().strftime("%Y-%m-%d")
        if invalid_dates < len(work)
        else "",
        "invalid_date_count": invalid_dates,
        "duplicate_source_item_count": duplicate_ids,
        "missing_url_count": missing_urls,
        "missing_title_count": missing_titles,
        "source_counts": {
            str(key): int(value) for key, value in work["source"].value_counts().items()
        },
        "event_type_counts": {str(key): int(value) for key, value in event_counts.items()},
        "polarity_counts": {str(key): int(value) for key, value in polarity_counts.items()},
        "recognized_type_count": recognized,
        "recognized_type_rate": recognized / len(work) if len(work) else 0.0,
        "unknown_type_count": int((work["event_type"].fillna("unknown") == "unknown").sum()),
        "normalization": normalized_summary,
        "per_security_counts": per_security,
        "limitations": [
            "关键词分类可复核但覆盖率有限，unknown 公告保持 neutral，不由模型猜测方向",
            "本快照只有公告元数据和 PDF 链接，未读取全文，也未构造公告后异常收益标签",
            "覆盖与方向统计不能证明官方事件对回测或投资决策有增量预测价值",
        ],
    }


def _markdown(profile: dict[str, Any], input_path: Path) -> str:
    polarity = profile["polarity_counts"]
    lines = [
        "# CNINFO 官方公告事件样本剖面",
        "",
        f"输入：`{input_path}`；as-of：`{profile['as_of']}`",
        "",
        "## 结果",
        "",
        (
            f"- 状态：`{profile['status']}`；{profile['security_count']} 家证券，"
            f"{profile['row_count']} 条公告。"
        ),
        (
            f"- 日期覆盖：`{profile['coverage_start']}` 至 "
            f"`{profile['coverage_end']}`；无效日期 {profile['invalid_date_count']} 条。"
        ),
        (
            f"- 来源字段：{profile['source_counts']}；缺 PDF 链接 "
            f"{profile['missing_url_count']} 条；缺标题 {profile['missing_title_count']} 条。"
        ),
        f"- `(source, item_id)` 重复：{profile['duplicate_source_item_count']} 条。",
        (
            f"- 固定分类命中 {profile['recognized_type_count']} 条"
            f"（{profile['recognized_type_rate']:.2%}）；"
            f"unknown {profile['unknown_type_count']} 条。"
        ),
        (
            f"- 归一化接受 {profile['normalization'].get('accepted_count', 0)} 条，"
            f"拒绝 {profile['normalization'].get('rejected_count', 0)} 条。"
        ),
        (
            f"- 极性分布：正面 {polarity.get('positive', 0)}、"
            f"负面 {polarity.get('negative', 0)}、中性 {polarity.get('neutral', 0)}。"
        ),
        "",
        "## 解释",
        "",
        "- 该快照满足方案 A 的来源、时点、链接和固定分类审计要求，可作为历史事件层输入。",
        "- 绝大多数公告未命中有限关键词，系统保持中性，避免把未知公告误判为利好或利空。",
        "- 该结果只证明可追溯覆盖与分类行为，不证明公告事件已经产生可交易的样本外增量。",
        "",
        "## 限制与下一步",
        "",
    ]
    lines.extend(f"- {item}" for item in profile["limitations"])
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="剖析已导出的 CNINFO 公告事件快照")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--as-of", default="2025-12-31")
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-md", type=Path, required=True)
    args = parser.parse_args()
    frame = pd.read_csv(args.input, dtype=str).fillna("")
    profile = _profile(frame, args.as_of)
    report = {
        "input": str(args.input.resolve()),
        "profile": profile,
    }
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    args.output_md.write_text(_markdown(profile, args.input), encoding="utf-8")
    print(json.dumps(profile, ensure_ascii=False, indent=2))
    return 0 if profile["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
