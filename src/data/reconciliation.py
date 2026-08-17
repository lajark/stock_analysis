"""Deterministic comparison of financial data from two providers.

The reconciler deliberately does not choose a primary source. It produces a
small, auditable report covering point-in-time coverage, revisions, missing
periods, and numeric disagreements so that a source decision can be made from
evidence rather than from API convenience.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any

import pandas as pd

from src.data.financials import filter_financial_as_of, normalize_financial_frame

_METADATA_COLUMNS = {
    "ts_code",
    "end_date",
    "ann_date",
    "f_ann_date",
    "period",
    "report_type",
    "comp_type",
    "update_flag",
}


@dataclass(frozen=True)
class ReconciliationResult:
    """Comparison result for one financial dataset and one as-of date."""

    dataset: str
    as_of: str
    left_source: str
    right_source: str
    left_rows: int
    right_rows: int
    left_canonical_rows: int
    right_canonical_rows: int
    left_revision_rows: int
    right_revision_rows: int
    left_future_rows: int
    right_future_rows: int
    matched_periods: int
    left_only_periods: tuple[str, ...]
    right_only_periods: tuple[str, ...]
    mismatch_count: int
    mismatches: tuple[dict[str, Any], ...]
    status: str

    def to_dict(self) -> dict[str, Any]:
        """Serialize the result without pandas-specific values."""
        return asdict(self)


def _clean_frame(frame: pd.DataFrame | None, as_of: str) -> tuple[pd.DataFrame, int, int]:
    if frame is None or frame.empty or "end_date" not in frame.columns:
        return pd.DataFrame(), 0, 0
    raw = frame.copy()
    raw["end_date"] = pd.to_datetime(raw["end_date"], errors="coerce")
    raw = raw.loc[raw["end_date"].notna()].copy()
    if raw.empty:
        return raw, 0, 0

    future_mask = raw["end_date"] > pd.Timestamp(as_of)
    announcement_columns = [
        column for column in ("f_ann_date", "ann_date") if column in raw.columns
    ]
    if announcement_columns:
        announcement = pd.Series(pd.NaT, index=raw.index, dtype="datetime64[ns]")
        for column in announcement_columns:
            announcement = announcement.fillna(pd.to_datetime(raw[column], errors="coerce"))
        future_mask |= announcement > pd.Timestamp(as_of)

    filtered = filter_financial_as_of(raw, as_of)
    revision_rows = int(filtered["end_date"].duplicated().sum())
    future_rows = int(future_mask.sum())
    return filtered, revision_rows, future_rows


def _period_text(value: Any) -> str:
    return pd.Timestamp(value).strftime("%Y-%m-%d")


def _key_columns(left: pd.DataFrame, right: pd.DataFrame) -> list[str]:
    """Use security plus period when both sides are multi-security exports."""
    if "ts_code" in left.columns and "ts_code" in right.columns:
        return ["ts_code", "end_date"]
    return ["end_date"]


def _key_text(key: Any, key_columns: list[str]) -> str:
    """Render a comparison key without exposing pandas-specific values."""
    if key_columns == ["end_date"]:
        return _period_text(key)
    ts_code, end_date = key
    return f"{ts_code}|{_period_text(end_date)}"


def _safe_value(value: Any) -> Any:
    if pd.isna(value):
        return None
    if hasattr(value, "item"):
        return value.item()
    return value


def _numeric_columns(left: pd.DataFrame, right: pd.DataFrame) -> list[str]:
    columns = sorted((set(left.columns) & set(right.columns)) - _METADATA_COLUMNS)
    result: list[str] = []
    for column in columns:
        left_values = pd.to_numeric(left[column], errors="coerce")
        right_values = pd.to_numeric(right[column], errors="coerce")
        if left_values.notna().any() or right_values.notna().any():
            result.append(column)
    return result


def _values_match(left: Any, right: Any, *, abs_tol: float, rel_tol: float) -> bool:
    left_number = pd.to_numeric(pd.Series([left]), errors="coerce").iloc[0]
    right_number = pd.to_numeric(pd.Series([right]), errors="coerce").iloc[0]
    if pd.isna(left_number) and pd.isna(right_number):
        return True
    if pd.isna(left_number) or pd.isna(right_number):
        return False
    difference = abs(float(left_number) - float(right_number))
    scale = max(abs(float(left_number)), abs(float(right_number)), 1.0)
    return difference <= abs_tol + rel_tol * scale


def reconcile_financial_frames(
    left: pd.DataFrame | None,
    right: pd.DataFrame | None,
    *,
    dataset: str,
    as_of: str,
    left_source: str = "tushare",
    right_source: str = "reference",
    abs_tol: float = 1e-6,
    rel_tol: float = 1e-4,
    max_mismatches: int = 100,
) -> ReconciliationResult:
    """Compare two frames after applying the same point-in-time policy."""
    left_filtered, left_revisions, left_future = _clean_frame(left, as_of)
    right_filtered, right_revisions, right_future = _clean_frame(right, as_of)
    left_canonical = normalize_financial_frame(left_filtered, as_of=as_of)
    right_canonical = normalize_financial_frame(right_filtered, as_of=as_of)

    key_columns = _key_columns(left_canonical, right_canonical)
    left_periods = (
        set(map(tuple, left_canonical[key_columns].itertuples(index=False, name=None)))
        if key_columns != ["end_date"] and not left_canonical.empty
        else set(left_canonical["end_date"])
        if not left_canonical.empty
        else set()
    )
    right_periods = (
        set(map(tuple, right_canonical[key_columns].itertuples(index=False, name=None)))
        if key_columns != ["end_date"] and not right_canonical.empty
        else set(right_canonical["end_date"])
        if not right_canonical.empty
        else set()
    )
    matched = sorted(left_periods & right_periods)
    left_only = tuple(
        _key_text(value, key_columns) for value in sorted(left_periods - right_periods)
    )
    right_only = tuple(
        _key_text(value, key_columns) for value in sorted(right_periods - left_periods)
    )

    mismatches: list[dict[str, Any]] = []
    mismatch_count = 0
    if matched:
        left_indexed = left_canonical.set_index(key_columns)
        right_indexed = right_canonical.set_index(key_columns)
        for period in matched:
            for column in _numeric_columns(left_canonical, right_canonical):
                left_value = left_indexed.at[period, column]
                right_value = right_indexed.at[period, column]
                if _values_match(left_value, right_value, abs_tol=abs_tol, rel_tol=rel_tol):
                    continue
                mismatch_count += 1
                if len(mismatches) < max_mismatches:
                    mismatches.append(
                        {
                            "period": _key_text(period, key_columns),
                            "field": column,
                            "left": _safe_value(left_value),
                            "right": _safe_value(right_value),
                        }
                    )

    if not left_periods or not right_periods:
        status = "insufficient"
    elif not left_only and not right_only and mismatch_count == 0:
        status = "pass"
    else:
        status = "partial"
    return ReconciliationResult(
        dataset=dataset,
        as_of=as_of,
        left_source=left_source,
        right_source=right_source,
        left_rows=len(left_filtered),
        right_rows=len(right_filtered),
        left_canonical_rows=len(left_canonical),
        right_canonical_rows=len(right_canonical),
        left_revision_rows=left_revisions,
        right_revision_rows=right_revisions,
        left_future_rows=left_future,
        right_future_rows=right_future,
        matched_periods=len(matched),
        left_only_periods=left_only,
        right_only_periods=right_only,
        mismatch_count=mismatch_count,
        mismatches=tuple(mismatches),
        status=status,
    )


def reconcile_financial_sets(
    left: Mapping[str, pd.DataFrame | None],
    right: Mapping[str, pd.DataFrame | None],
    *,
    as_of: str,
    datasets: tuple[str, ...] = ("income", "balance_sheet", "cashflow", "fina_indicator"),
    left_source: str = "tushare",
    right_source: str = "reference",
) -> dict[str, Any]:
    """Reconcile all standard financial datasets and return an aggregate report."""
    results = {
        dataset: reconcile_financial_frames(
            left.get(dataset),
            right.get(dataset),
            dataset=dataset,
            as_of=as_of,
            left_source=left_source,
            right_source=right_source,
        )
        for dataset in datasets
    }
    statuses = {result.status for result in results.values()}
    if statuses == {"pass"}:
        overall_status = "pass"
    elif statuses == {"insufficient"}:
        overall_status = "insufficient"
    else:
        overall_status = "partial"
    return {
        "as_of": as_of,
        "left_source": left_source,
        "right_source": right_source,
        "status": overall_status,
        "datasets": {name: result.to_dict() for name, result in results.items()},
    }
