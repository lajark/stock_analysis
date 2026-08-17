"""Build a reusable, incremental queue for official-financial audits.

The queue compares already-exported snapshots only.  It deliberately does
not parse PDFs or call a provider, so planning the next small audit batch is
cheap and deterministic.  A previous official snapshot can be supplied to
carry forward resolved rows and avoid reopening unchanged work.  Field-level
manual decisions keep semantic conflicts visible while preventing duplicate
PDF extraction.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from src.data.financials import filter_financial_as_of, normalize_financial_frame

DATASETS = ("income", "balance_sheet", "cashflow", "fina_indicator")
METADATA_COLUMNS = {
    "ts_code",
    "end_date",
    "ann_date",
    "f_ann_date",
    "period",
    "report_type",
    "comp_type",
    "update_flag",
}
MISMATCH_BUCKETS = {
    "minor_lt_1pct",
    "moderate_1_to_10pct",
    "large_10_to_50pct",
    "very_large_50pct_or_more",
    "missing_left",
    "missing_right",
}
REPORT_TYPES = {
    "03-31": "q1",
    "06-30": "semiannual",
    "09-30": "q3",
    "12-31": "annual",
}


@dataclass(frozen=True)
class AuditPlanSummary:
    """Small JSON-serializable summary for one queue build."""

    as_of: str
    datasets: tuple[str, ...]
    current_rows: int
    previous_rows: int
    open_candidates: int
    selected_candidates: int
    selected_reports: int
    resolved_since_previous: int
    carried_forward: int
    new_or_changed: int
    excluded_formula_coverage: int
    excluded_derived_indicator: int
    excluded_provider_revision: int
    excluded_source_unreadable: int
    max_per_cluster: int
    max_candidates: int
    excluded_manual_decision: int = 0
    actionable_open_candidates: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_dataset(directory: Path, dataset: str) -> pd.DataFrame | None:
    """Load one CSV/Parquet snapshot without requiring a provider client."""
    for suffix, reader in ((".parquet", pd.read_parquet), (".csv", pd.read_csv)):
        path = directory / f"{dataset}{suffix}"
        if path.exists():
            return reader(path)
    return None


def _unreadable_report_keys(directory: Path) -> set[tuple[str, str]]:
    """Identify reports whose PDF text is broadly unreadable.

    This is a queue classification only. The missing values remain visible in
    the snapshot and are not silently treated as correct.
    """
    path = directory / "official_financials.csv"
    if not path.exists():
        return set()
    frame = pd.read_csv(path, dtype=str).fillna("")
    required = {"ts_code", "period_end", "missing_fields"}
    if not required.issubset(frame.columns):
        return set()
    keys: set[tuple[str, str]] = set()
    for row in frame.itertuples(index=False):
        missing = {
            item.strip() for item in str(row.missing_fields).split(",") if item.strip()
        }
        if len(missing) >= 8:
            keys.add((str(row.ts_code), str(row.period_end)))
    return keys


def _load_manual_decisions(
    path: Path | None,
) -> dict[tuple[str, str, str, str], tuple[str, str]]:
    """Load field-level decisions that should stay visible but skip re-audit."""
    if path is None or not path.exists():
        return {}
    frame = pd.read_csv(path, dtype=str).fillna("")
    required = {"dataset", "ts_code", "period_end", "field", "decision"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"人工决议清单缺少字段：{', '.join(sorted(missing))}")
    decisions: dict[tuple[str, str, str, str], tuple[str, str]] = {}
    for row in frame.itertuples(index=False):
        key = (str(row.dataset), str(row.ts_code), str(row.period_end), str(row.field))
        decision = str(row.decision).strip()
        if not decision:
            continue
        decisions[key] = (decision, str(getattr(row, "reason", "")).strip())
    return decisions


def _key_columns(left: pd.DataFrame, right: pd.DataFrame) -> list[str]:
    if "ts_code" in left.columns and "ts_code" in right.columns:
        return ["ts_code", "end_date"]
    return ["end_date"]


def _numeric_columns(left: pd.DataFrame, right: pd.DataFrame) -> list[str]:
    columns = sorted((set(left.columns) & set(right.columns)) - METADATA_COLUMNS)
    return [
        column
        for column in columns
        if pd.to_numeric(left[column], errors="coerce").notna().any()
        or pd.to_numeric(right[column], errors="coerce").notna().any()
    ]


def _bucket(left: Any, right: Any, *, abs_tol: float = 1e-6, rel_tol: float = 1e-4) -> str:
    left_number = pd.to_numeric(pd.Series([left]), errors="coerce").iloc[0]
    right_number = pd.to_numeric(pd.Series([right]), errors="coerce").iloc[0]
    if pd.isna(left_number) and pd.isna(right_number):
        return "missing_both"
    if pd.isna(left_number):
        return "missing_left"
    if pd.isna(right_number):
        return "missing_right"
    difference = abs(float(left_number) - float(right_number))
    scale = max(abs(float(left_number)), abs(float(right_number)), 1.0)
    relative = difference / scale
    if difference <= abs_tol + rel_tol * scale:
        return "exact"
    if relative < 0.01:
        return "minor_lt_1pct"
    if relative < 0.10:
        return "moderate_1_to_10pct"
    if relative < 0.50:
        return "large_10_to_50pct"
    return "very_large_50pct_or_more"


def _safe(value: Any) -> Any:
    if pd.isna(value):
        return None
    if hasattr(value, "item"):
        return value.item()
    return value


def _period_text(value: Any) -> str:
    return pd.Timestamp(value).strftime("%Y-%m-%d")


def _report_type(period_end: str) -> str:
    return REPORT_TYPES.get(period_end[4:].lstrip("-"), "unknown")


def _value_at(frame: pd.DataFrame, key: Any, field: str) -> Any:
    if frame.empty or key not in frame.index or field not in frame.columns:
        return None
    return frame.at[key, field]


def _values_at(frame: pd.DataFrame, key: Any, field: str) -> list[Any]:
    """Return all raw revision values for one key/field."""
    if frame.empty or key not in frame.index or field not in frame.columns:
        return []
    values = frame.loc[key, field]
    if isinstance(values, pd.Series):
        return values.tolist()
    return [values]


def _priority(dataset: str, field: str, bucket: str, state: str) -> tuple[int, str, str]:
    """Return score, role and a human-readable reason for queue ordering."""
    if dataset == "fina_indicator" and field == "roa" and bucket == "missing_right":
        return 0, "formula_coverage", "ROA 由基础财务字段复算；右侧缺失不进入 PDF 解析队列"

    bucket_score = {
        "very_large_50pct_or_more": 90,
        "large_10_to_50pct": 70,
        "missing_left": 65,
        "missing_right": 60,
        "moderate_1_to_10pct": 35,
        "minor_lt_1pct": 15,
    }.get(bucket, 0)
    state_score = {"new": 25, "changed": 20, "carried_forward": 0}.get(state, 0)
    if dataset == "fina_indicator" and field in {"roe", "eps"}:
        return (
            bucket_score + state_score - 20,
            "derived_indicator",
            "供应商派生指标，先与基础字段和公式复算结果交叉判断",
        )
    return (
        bucket_score + state_score,
        "base_financial",
        "基础财务字段，优先核对原始报告标签、单位和列口径",
    )


def _compare_dataset(
    left: pd.DataFrame | None,
    current: pd.DataFrame | None,
    previous: pd.DataFrame | None,
    *,
    dataset: str,
    as_of: str,
    unreadable_keys: set[tuple[str, str]],
) -> tuple[list[dict[str, Any]], int, int]:
    left_filtered = (
        filter_financial_as_of(left, as_of) if left is not None else pd.DataFrame()
    )
    left_frame = normalize_financial_frame(left_filtered, as_of=as_of)
    current_frame = normalize_financial_frame(
        filter_financial_as_of(current, as_of) if current is not None else pd.DataFrame(),
        as_of=as_of,
    )
    previous_frame = normalize_financial_frame(
        filter_financial_as_of(previous, as_of) if previous is not None else pd.DataFrame(),
        as_of=as_of,
    )
    if left_frame.empty or current_frame.empty:
        return [], len(current_frame), len(previous_frame)

    key_columns = _key_columns(left_frame, current_frame)
    left_indexed = left_frame.set_index(key_columns)
    current_indexed = current_frame.set_index(key_columns)
    raw_left_indexed = (
        left_filtered.set_index(key_columns) if not left_filtered.empty else pd.DataFrame()
    )
    previous_indexed = (
        previous_frame.set_index(key_columns) if not previous_frame.empty else pd.DataFrame()
    )
    keys = sorted(set(left_indexed.index) & set(current_indexed.index))
    fields = _numeric_columns(left_frame, current_frame)
    rows: list[dict[str, Any]] = []
    for key in keys:
        period_end = key[-1] if isinstance(key, tuple) else key
        ts_code = key[0] if isinstance(key, tuple) else ""
        period_text = _period_text(period_end)
        report_type = _report_type(period_text)
        for field in fields:
            left_value = _value_at(left_indexed, key, field)
            current_value = _value_at(current_indexed, key, field)
            current_bucket = _bucket(left_value, current_value)
            previous_bucket = "unavailable"
            previous_value = None
            if not previous_indexed.empty:
                previous_value = _value_at(previous_indexed, key, field)
                previous_bucket = _bucket(left_value, previous_value)
            if current_bucket not in MISMATCH_BUCKETS:
                if current_bucket == "exact" and previous_bucket in MISMATCH_BUCKETS:
                    rows.append(
                        {
                            "dataset": dataset,
                            "ts_code": ts_code,
                            "period_end": period_text,
                            "report_type": report_type,
                            "field": field,
                            "left_value": _safe(left_value),
                            "right_value": _safe(current_value),
                            "previous_value": _safe(previous_value),
                            "current_bucket": current_bucket,
                            "previous_bucket": previous_bucket,
                            "state": "resolved",
                            "role": "resolved_regression",
                            "priority": 0,
                            "cluster_key": f"{dataset}.{field}.{previous_bucket}.{report_type}",
                            "reason": "当前快照已消除上一轮差异，纳入回归证据但不再重复抽查",
                        }
                    )
                continue

            right_changed = previous_bucket == "unavailable" or _bucket(
                current_value, previous_value
            ) != "exact"
            if previous_bucket in MISMATCH_BUCKETS:
                state = "changed" if right_changed else "carried_forward"
            else:
                state = "new"
            score, role, reason = _priority(dataset, field, current_bucket, state)
            revision_match = any(
                _bucket(raw_value, current_value) == "exact"
                for raw_value in _values_at(raw_left_indexed, key, field)
            )
            if revision_match:
                score = 5
                role = "provider_revision"
                reason = (
                    "官方值与 Tushare 同一报告期的另一修订版本一致；"
                    "先处理供应商修订选择，不重复抽查 PDF"
                )
            if (ts_code, period_text) in unreadable_keys:
                score = 0
                role = "source_unreadable"
                reason = (
                    "该报告多数基础字段因 PDF 字体/文本编码不可读而缺失；"
                    "保持缺失证据，不重复尝试同一解析规则"
                )
            rows.append(
                {
                    "dataset": dataset,
                    "ts_code": ts_code,
                    "period_end": period_text,
                    "report_type": report_type,
                    "field": field,
                    "left_value": _safe(left_value),
                    "right_value": _safe(current_value),
                    "previous_value": _safe(previous_value),
                    "current_bucket": current_bucket,
                    "previous_bucket": previous_bucket,
                    "state": state,
                    "role": role,
                    "priority": score,
                    "cluster_key": f"{dataset}.{field}.{current_bucket}.{report_type}",
                    "reason": reason,
                }
            )
    return rows, len(current_frame), len(previous_frame)


def build_audit_queue(
    left_dir: Path,
    current_right_dir: Path,
    *,
    as_of: str,
    previous_right_dir: Path | None = None,
    datasets: tuple[str, ...] = DATASETS,
    max_per_cluster: int = 3,
    max_candidates: int = 30,
    decisions_path: Path | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, AuditPlanSummary]:
    """Build all open rows, a small selected batch, and a summary.

    The first returned frame contains every unresolved mismatch.  The second
    is the recommended small batch for the next PDF extraction pass.  The
    third contains rows resolved by the current snapshot so they can be
    retained as regression evidence without being re-audited.  Rows listed in
    ``decisions_path`` remain in the first frame but are excluded from the
    selected batch.
    """
    rows: list[dict[str, Any]] = []
    current_rows = previous_rows = 0
    unreadable_keys = _unreadable_report_keys(current_right_dir)
    manual_decisions = _load_manual_decisions(decisions_path)
    for dataset in datasets:
        dataset_rows, current_count, previous_count = _compare_dataset(
            load_dataset(left_dir, dataset),
            load_dataset(current_right_dir, dataset),
            load_dataset(previous_right_dir, dataset) if previous_right_dir else None,
            dataset=dataset,
            as_of=as_of,
            unreadable_keys=unreadable_keys,
        )
        rows.extend(dataset_rows)
        current_rows += current_count
        previous_rows += previous_count

    all_rows = pd.DataFrame(rows)
    if all_rows.empty:
        empty = pd.DataFrame()
        summary = AuditPlanSummary(
            as_of=as_of,
            datasets=tuple(datasets),
            current_rows=current_rows,
            previous_rows=previous_rows,
            open_candidates=0,
            selected_candidates=0,
            selected_reports=0,
            resolved_since_previous=0,
            carried_forward=0,
            new_or_changed=0,
            excluded_formula_coverage=0,
            excluded_derived_indicator=0,
            excluded_provider_revision=0,
            excluded_source_unreadable=0,
            max_per_cluster=max_per_cluster,
            max_candidates=max_candidates,
            excluded_manual_decision=0,
            actionable_open_candidates=0,
        )
        return empty, empty, empty, summary

    if manual_decisions:
        all_rows["manual_decision"] = ""
        all_rows["manual_decision_reason"] = ""
        for key, (decision, reason) in manual_decisions.items():
            dataset, ts_code, period_end, field = key
            mask = (
                all_rows["dataset"].eq(dataset)
                & all_rows["ts_code"].eq(ts_code)
                & all_rows["period_end"].eq(period_end)
                & all_rows["field"].eq(field)
            )
            all_rows.loc[mask, "manual_decision"] = decision
            all_rows.loc[mask, "manual_decision_reason"] = reason
            all_rows.loc[mask, "role"] = "manual_decision"
            all_rows.loc[mask, "priority"] = 0
            all_rows.loc[mask, "reason"] = reason or f"人工决议：{decision}；保留差异但不重复抽取"

    all_rows = all_rows.sort_values(
        ["priority", "cluster_key", "ts_code", "period_end"],
        ascending=[False, True, True, True],
        kind="mergesort",
    ).reset_index(drop=True)
    all_rows["cluster_rank"] = all_rows.groupby("cluster_key", sort=False).cumcount() + 1
    all_rows["eligible"] = ~all_rows["role"].isin(
        [
            "formula_coverage",
            "derived_indicator",
            "provider_revision",
            "source_unreadable",
            "manual_decision",
        ]
    )
    selected = all_rows.loc[
        all_rows["eligible"] & all_rows["state"].ne("resolved")
    ].copy()
    selected = selected.loc[selected["cluster_rank"] <= max_per_cluster]
    selected = selected.head(max_candidates).copy()
    all_rows["selected"] = all_rows.index.isin(selected.index)
    resolved = all_rows.loc[all_rows["state"] == "resolved"].copy()
    # ``resolved`` is kept for callers that provide a previously materialized
    # queue; current mismatches never carry this state and remain open.
    open_rows = all_rows.loc[all_rows["state"] != "resolved"].copy()
    summary = AuditPlanSummary(
        as_of=as_of,
        datasets=tuple(datasets),
        current_rows=current_rows,
        previous_rows=previous_rows,
        open_candidates=len(open_rows),
        selected_candidates=len(selected),
        selected_reports=(
            len(selected[["ts_code", "period_end"]].drop_duplicates())
            if not selected.empty
            else 0
        ),
        resolved_since_previous=len(resolved),
        carried_forward=int((open_rows["state"] == "carried_forward").sum()),
        new_or_changed=int(open_rows["state"].isin(["new", "changed"]).sum()),
        excluded_formula_coverage=int((open_rows["role"] == "formula_coverage").sum()),
        excluded_derived_indicator=int(
            (open_rows["role"] == "derived_indicator").sum()
        ),
        excluded_provider_revision=int(
            (open_rows["role"] == "provider_revision").sum()
        ),
        excluded_source_unreadable=int(
            (open_rows["role"] == "source_unreadable").sum()
        ),
        max_per_cluster=max_per_cluster,
        max_candidates=max_candidates,
        excluded_manual_decision=int(
            (open_rows["role"] == "manual_decision").sum()
        ),
        actionable_open_candidates=int(
            (open_rows["eligible"] & open_rows["state"].ne("resolved")).sum()
        ),
    )
    return open_rows, selected, resolved, summary
