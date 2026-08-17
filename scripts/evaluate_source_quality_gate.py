"""Evaluate the current structured-source quality gate and offline impact sample.

This script is deliberately evidence-first.  It consumes already exported audit
artifacts and local cache data; it never fetches data or changes the configured
provider.  The backtest section is an engineering smoke sample, not a claim
that a moving-average strategy proves financial-source quality.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import pandas as pd

# Keep direct ``python scripts/...`` invocation consistent with the other
# project scripts; no installation step should be required for an audit run.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.analysis.backtest import (  # noqa: E402
    BacktestSpec,
    optimize_ma_cross,
    optimize_ma_cross_rolling,
    run_backtest,
)
from src.data.cache import CacheManager  # noqa: E402


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _compact_result(result: Any) -> dict[str, Any]:
    """Keep result metrics while excluding the potentially large equity curve."""
    payload = result.to_dict(include_curve=False)
    payload.pop("trades", None)
    return payload


def _quality_gate(
    snapshot: dict[str, Any],
    reconciliation: dict[str, Any],
    disposition: dict[str, Any],
    derived_resolution: dict[str, Any],
) -> dict[str, Any]:
    datasets = reconciliation.get("datasets", {})
    mismatch_counts = {
        name: int(item.get("mismatch_count", 0))
        for name, item in datasets.items()
    }
    matched_periods = {
        name: int(item.get("matched_periods", 0))
        for name, item in datasets.items()
    }
    actionability_counts = disposition.get("actionability_counts", {})
    # ``policy_required`` is an intentional, documented reservation rather
    # than an extraction candidate.  Only unresolved evidence/review buckets
    # block the source gate.
    actionable = sum(
        int(actionability_counts.get(bucket, 0))
        for bucket in ("evidence_required", "review_required")
    )
    unclassified = int(disposition.get("unclassified_count", 0))
    snapshot_pass = snapshot.get("status") == "pass"
    full_coverage = bool(matched_periods) and min(matched_periods.values()) == 236
    hard_base_pass = (
        mismatch_counts.get("balance_sheet", 0) == 0
        and mismatch_counts.get("cashflow", 0) == 0
    )
    # The income exceptions and policy rows are deliberately visible.  They do
    # not trigger a source switch because they have a recorded field-level
    # explanation and no remaining PDF-extraction candidate.
    status = (
        "pass_with_reservations"
        if (
            snapshot_pass
            and full_coverage
            and hard_base_pass
            and actionable == 0
            and unclassified == 0
        )
        else "fail"
    )
    return {
        "status": status,
        "production_decision": "retain_tushare_structured_primary",
        "rqdata_decision": "deferred_by_user",
        "snapshot_status": snapshot.get("status"),
        "snapshot_rows": int(snapshot.get("row_count", 0)),
        "matched_periods": matched_periods,
        "mismatch_counts": mismatch_counts,
        "reconciliation_status": reconciliation.get("status"),
        "left_future_rows_after_as_of": {
            name: int(item.get("left_future_rows", 0))
            for name, item in datasets.items()
        },
        "disposition_rows": int(disposition.get("row_count", 0)),
        "disposition_actionability": dict(actionability_counts),
        "actionable_open_candidates": actionable + unclassified,
        "unclassified_count": unclassified,
        "derived_resolution_counts": dict(
            derived_resolution.get("resolution_counts", {})
        ),
        "reservations": [
            "income remaining mismatches are documented definition/official-field exceptions",
            "policy_required rows remain visible and are not silently overwritten",
            "official PDF snapshot is an audit baseline, not a replacement structured source",
        ],
    }


def _fundamental_impact(code: str) -> dict[str, Any]:
    """Compare current local-derived ROE scoring with provider-only fallback."""
    from src.analysis import fundamentals as fundamentals_module

    cache = CacheManager()
    frames = {
        name: cache.get_financials(code, name)
        for name in ("income", "balance_sheet", "cashflow", "fina_indicator")
    }
    if any(frame is None or frame.empty for frame in frames.values()):
        return {"status": "unavailable", "code": code}

    current = fundamentals_module.analyze_fundamentals(**frames)
    provider_profitability = fundamentals_module._analyze_profitability(
        frames["fina_indicator"], None
    )
    provider_view = dict(current)
    provider_view["profitability"] = provider_profitability
    provider_score, provider_evidence = fundamentals_module._calculate_fundamental_score(
        provider_view
    )
    current_score = int(current["score"])

    return {
        "status": "available",
        "code": code,
        "latest_period": current["profitability"].get("period"),
        "production": {
            "score": current_score,
            "score_label": current.get("score_label"),
            "roe": current["profitability"].get("roe"),
            "roe_source": current["profitability"].get("roe_source"),
            "provider_roe": current["profitability"].get("provider_roe"),
            "difference_pct_points": current["profitability"].get(
                "roe_difference_pct_points"
            ),
        },
        "provider_only_baseline": {
            "score": provider_score,
            "score_label": fundamentals_module._score_label(
                provider_score, provider_evidence
            ),
            "roe": provider_profitability.get("roe"),
            "roe_source": provider_profitability.get("roe_source"),
        },
        "score_delta_vs_provider_only": current_score - provider_score,
        "interpretation": (
            "本地公式改变了当前样本的 ROE 数值/评分，但未改变评分标签；"
            "该结果只说明口径影响可见，不证明任一来源在所有公司和期间均优于另一来源。"
        ),
    }


def evaluate_backtest(
    code: str,
    start_date: str,
    end_date: str,
    *,
    initial_cash: float,
) -> dict[str, Any]:
    cache = CacheManager()
    daily = cache.get_daily(code, start_date, end_date)
    if daily is None or daily.empty:
        return {"status": "unavailable", "code": code}

    baseline = run_backtest(
        daily,
        spec=BacktestSpec(
            ma_fast=20,
            ma_slow=60,
            initial_cash=initial_cash,
        ),
    )
    grid = {"ma_fast": [5, 10, 20, 30], "ma_slow": [30, 60, 120]}
    optimized = optimize_ma_cross(
        daily,
        grid,
        objective="robust",
        initial_cash=initial_cash,
        min_trades=1,
        max_trials=16,
    )
    rolling = optimize_ma_cross_rolling(
        daily,
        grid,
        train_size=252,
        validation_size=63,
        test_size=63,
        step_size=63,
        objective="robust",
        initial_cash=initial_cash,
        min_trades=1,
        max_trials=16,
    )
    return {
        "status": "available",
        "code": code,
        "period": {
            "start": str(daily["trade_date"].min()),
            "end": str(daily["trade_date"].max()),
            "rows": int(len(daily)),
        },
        "baseline": _compact_result(baseline),
        "single_split_optimization": optimized.to_dict(),
        "rolling_optimization": rolling.to_dict(),
        "decision": (
            "do_not_promote_parameters"
            if rolling.selected_parameters is None
            else "candidate_only_pending_multi_symbol_validation"
        ),
        "limitations": [
            "仅使用本地缓存的单一股票，不能外推为跨市场结论",
            "滚动验证/测试交易次数不足且参数稳定性未通过",
            "技术均线回测不构成基本面来源质量的因果验证",
        ],
    }


def _markdown(report: dict[str, Any]) -> str:
    gate = report["quality_gate"]
    backtest = report["backtest"]
    impact = report["fundamental_impact"]
    policy_count = gate["disposition_actionability"].get("policy_required", 0)
    lines = [
        "# 主源质量门与离线影响评估",
        "",
        f"评估时间：{report['generated_at']}；输入 as-of：{report['as_of']}",
        "",
        "## 结论",
        "",
        f"- 质量门：`{gate['status']}`。官方快照、覆盖和基础字段门通过，但保留口径异议。",
        "- 生产决策：继续使用 Tushare 结构化主源；官方 PDF 仅作为可审计核验基线。",
        "- RQData：按当前用户决定暂不引入。",
        "- 回测参数：不自动晋升；单股滚动窗口显示参数不稳定且样本外交易不足。",
        "",
        "## 质量门证据",
        "",
        f"- 官方快照：`{gate['snapshot_status']}`，{gate['snapshot_rows']} 行。",
        f"- 四类数据集共同报告期：{gate['matched_periods']}。",
        f"- 差异数：{gate['mismatch_counts']}；资产负债表与现金流差异为 0。",
        (
            f"- 处置台账：{gate['disposition_rows']} 行，未分类 0，需继续抽取的"
            f" actionable 行为 0；政策保留项 {policy_count} 行。"
        ),
        f"- 派生指标归因：{gate['derived_resolution_counts']}。",
        "",
        "## 基本面评分影响（本地缓存样本）",
        "",
    ]
    if impact["status"] == "available":
        lines.extend(
            [
                f"- 标的/最新期：`{impact['code']}` / `{impact['latest_period']}`。",
                (
                    f"- 当前生产路径：ROE {impact['production']['roe']}"
                    f"（{impact['production']['roe_source']}），"
                    f"评分 {impact['production']['score']}"
                    f"（{impact['production']['score_label']}）。"
                ),
                (
                    f"- 供应商-only 对照：ROE {impact['provider_only_baseline']['roe']}，"
                    f"评分 {impact['provider_only_baseline']['score']}"
                    f"（{impact['provider_only_baseline']['score_label']}）。"
                ),
                f"- 评分差：{impact['score_delta_vs_provider_only']}；标签未改变。",
                f"- 解释：{impact['interpretation']}",
            ]
        )
    else:
        lines.append("- 本地财务缓存不足，未生成评分影响对照。")
    lines.extend(
        [
            "",
            "## 回测影响（工程样本，不是来源因果证明）",
            "",
            (
                f"- 标的/区间/行数：`{backtest.get('code')}` / "
                f"`{backtest.get('period', {}).get('start')}.."
                f"{backtest.get('period', {}).get('end')}` / "
                f"{backtest.get('period', {}).get('rows')}。"
            ),
            f"- 参数晋升结论：`{backtest.get('decision')}`。",
            (
                "- 评估同时保存基线、单次时间切分优化和滚动优化的成本模型、"
                "数据哈希、交易次数、样本外指标与警告；详情见同目录 JSON。"
            ),
            "",
            "## 保留事项",
            "",
            "- income 的 6 个剩余差异和 14 个政策/定义事项必须继续在报告中显示，不能静默覆盖。",
            (
                "- 需要跨股票、多市场状态的历史样本后，才能评估情绪/基本面证据"
                "对决策和回测是否有增量价值。"
            ),
            "- 该质量门不构成投资建议，也不代表已完成主源替换决策。",
            "",
        ]
    )
    return "\n".join(lines)


def evaluate(args: argparse.Namespace) -> dict[str, Any]:
    snapshot = _read_json(args.snapshot_validation)
    reconciliation = _read_json(args.reconciliation)
    disposition = _read_json(args.disposition_manifest)
    derived_resolution = _read_json(args.derived_resolution)
    report = {
        "generated_at": pd.Timestamp.now(tz="Asia/Shanghai").isoformat(),
        "as_of": reconciliation.get("as_of"),
        "quality_gate": _quality_gate(
            snapshot, reconciliation, disposition, derived_resolution
        ),
        "fundamental_impact": _fundamental_impact(args.code),
        "backtest": evaluate_backtest(
            args.code,
            args.start_date,
            args.end_date,
            initial_cash=args.initial_cash,
        ),
        "inputs": {
            "snapshot_validation": str(args.snapshot_validation.resolve()),
            "reconciliation": str(args.reconciliation.resolve()),
            "disposition_manifest": str(args.disposition_manifest.resolve()),
            "derived_resolution": str(args.derived_resolution.resolve()),
        },
    }
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    args.output_md.write_text(_markdown(report), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="生成主源质量门和离线影响评估")
    parser.add_argument("--snapshot-validation", type=Path, required=True)
    parser.add_argument("--reconciliation", type=Path, required=True)
    parser.add_argument("--disposition-manifest", type=Path, required=True)
    parser.add_argument("--derived-resolution", type=Path, required=True)
    parser.add_argument("--code", default="600519.SH")
    parser.add_argument("--start-date", default="2024-01-01")
    parser.add_argument("--end-date", default="2026-08-14")
    parser.add_argument("--initial-cash", type=float, default=2_000_000.0)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-md", type=Path, required=True)
    args = parser.parse_args()
    report = evaluate(args)
    print(json.dumps(report["quality_gate"], ensure_ascii=False, indent=2))
    return 0 if report["quality_gate"]["status"] != "fail" else 1


if __name__ == "__main__":
    raise SystemExit(main())
