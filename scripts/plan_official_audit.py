"""Plan a small, incremental official-PDF audit batch.

This command compares exported snapshots only.  It never downloads data or
parses PDFs, so it can be run before every extraction pass at negligible cost.

Example::

    python scripts/plan_official_audit.py \
        --left .workspace/tmp/tushare-audit-20260815 \
        --current-right .workspace/tmp/official-financials-20260815 \
        --previous-right .workspace/tmp/official-financials-20260815-v13b \
        --as-of 2026-08-15 \
        --output-dir .workspace/tmp/official-audit-plan-20260815-v14
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.data.audit_queue import DATASETS, build_audit_queue  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="生成官方 PDF 增量审计候选队列")
    parser.add_argument("--left", type=Path, required=True, help="Tushare 快照目录")
    parser.add_argument("--current-right", type=Path, required=True, help="当前官方快照目录")
    parser.add_argument(
        "--previous-right",
        type=Path,
        help="上一轮官方快照目录；用于识别已解决和沿用差异",
    )
    parser.add_argument("--as-of", required=True, help="分析时点，格式 YYYY-MM-DD")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--decisions",
        type=Path,
        help="可选字段级人工决议 CSV；决议行保留在台账但不再进入 PDF 抽取批次",
    )
    parser.add_argument("--max-per-cluster", type=int, default=3)
    parser.add_argument("--max-candidates", type=int, default=30)
    parser.add_argument("--datasets", default=",".join(DATASETS))
    args = parser.parse_args()
    if args.max_per_cluster < 1 or args.max_candidates < 1:
        raise SystemExit("--max-per-cluster 和 --max-candidates 必须为正整数")
    datasets = tuple(item.strip() for item in args.datasets.split(",") if item.strip())
    invalid = sorted(set(datasets) - set(DATASETS))
    if invalid:
        raise SystemExit(f"不支持的数据集：{', '.join(invalid)}")

    open_rows, selected_rows, resolved_rows, summary = build_audit_queue(
        args.left,
        args.current_right,
        as_of=args.as_of,
        previous_right_dir=args.previous_right,
        datasets=datasets,
        max_per_cluster=args.max_per_cluster,
        max_candidates=args.max_candidates,
        decisions_path=args.decisions,
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    open_rows.to_csv(args.output_dir / "audit-candidates.csv", index=False, encoding="utf-8-sig")
    selected_rows.to_csv(
        args.output_dir / "audit-selected-batch.csv", index=False, encoding="utf-8-sig"
    )
    open_rows.loc[open_rows["role"] == "derived_indicator"].to_csv(
        args.output_dir / "audit-derived-review.csv",
        index=False,
        encoding="utf-8-sig",
    )
    resolved_rows.to_csv(
        args.output_dir / "audit-resolved-regression.csv", index=False, encoding="utf-8-sig"
    )

    manifest = {
        **summary.to_dict(),
        "left_dir": str(args.left.resolve()),
        "current_right_dir": str(args.current_right.resolve()),
        "previous_right_dir": str(args.previous_right.resolve()) if args.previous_right else None,
        "decisions_path": str(args.decisions.resolve()) if args.decisions else None,
        "selection_policy": (
            "先排除公式覆盖类缺失和已解析的派生指标差异，再按差异规模/缺失/新增变化排序；"
            "人工决议、来源不可读、供应商修订和派生指标只保留在台账，"
            "每个字段×差异类型×报告期簇最多取 max_per_cluster 份，"
            "总量不超过 max_candidates。"
        ),
    }
    (args.output_dir / "audit-plan.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
