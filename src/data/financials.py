"""Financial statement normalization and point-in-time selection helpers.

Financial APIs commonly return several versions of the same reporting period.
These helpers keep the announcement/revision metadata, remove rows that were
not public at the requested analysis date, and select one deterministic record
per reporting period for local analysis.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

_DATE_COLUMNS = ("end_date", "ann_date", "f_ann_date", "period")
_ANNOUNCEMENT_COLUMNS = ("f_ann_date", "ann_date")


def _as_timestamp(value: Any) -> pd.Timestamp | None:
    """Convert a user/provider date to a timestamp, accepting empty values."""
    if value is None or value == "":
        return None
    timestamp = pd.to_datetime(value, errors="coerce")
    if pd.isna(timestamp):
        return None
    return pd.Timestamp(timestamp)


def _announcement_dates(frame: pd.DataFrame) -> pd.Series | None:
    """Return the effective public announcement date for each row."""
    available = [column for column in _ANNOUNCEMENT_COLUMNS if column in frame.columns]
    if not available:
        return None

    dates = pd.Series(pd.NaT, index=frame.index, dtype="datetime64[ns]")
    for column in available:
        parsed = pd.to_datetime(frame[column], errors="coerce")
        dates = dates.fillna(parsed)
    return dates


def filter_financial_as_of(
    frame: pd.DataFrame,
    as_of: Any = None,
) -> pd.DataFrame:
    """Keep records whose report and public dates are available at ``as_of``.

    Rows without announcement metadata are retained for backwards-compatible
    legacy caches, but the reporting period is still bounded when possible.
    """
    if frame is None or frame.empty:
        return pd.DataFrame() if frame is None else frame.copy()

    as_of_timestamp = _as_timestamp(as_of)
    result = frame.copy()
    for column in _DATE_COLUMNS:
        if column in result.columns:
            result[column] = pd.to_datetime(result[column], errors="coerce")
    if as_of_timestamp is None:
        return result

    mask = pd.Series(True, index=result.index)
    if "end_date" in result.columns:
        end_dates = pd.to_datetime(result["end_date"], errors="coerce")
        mask &= end_dates.isna() | (end_dates <= as_of_timestamp)

    announcement_dates = _announcement_dates(result)
    if announcement_dates is not None:
        mask &= announcement_dates.isna() | (announcement_dates <= as_of_timestamp)
    return result.loc[mask].copy()


def _report_type_rank(series: pd.Series) -> pd.Series:
    """Prefer consolidated report types only when other evidence ties."""
    values = series.astype("string")
    # Tushare's report_type=1 is consolidated; 3/4 are adjusted variants.
    return values.map({"1": 3, "4": 2, "3": 2, "2": 1}).fillna(0).astype(int)


def _deduplication_keys(frame: pd.DataFrame) -> list[str]:
    """Return the natural key used to select one revision per report.

    Provider exports can contain multiple securities.  In that case a report
    period is only unique within ``ts_code``; falling back to ``end_date`` is
    retained for legacy single-security frames and the small unit-test frames.
    """
    if "ts_code" in frame.columns:
        return ["ts_code", "end_date"]
    return ["end_date"]


def deduplicate_financial_records(frame: pd.DataFrame) -> pd.DataFrame:
    """Select one deterministic revision for every security/report period.

    The latest public announcement wins, then explicit update corrections, and
    finally consolidated report type. This preserves revisions in the cache
    while exposing a canonical view to analysis code.
    """
    if frame is None or frame.empty or "end_date" not in frame.columns:
        return pd.DataFrame() if frame is None else frame.copy()

    result = frame.copy()
    result["end_date"] = pd.to_datetime(result["end_date"], errors="coerce")
    result = result.loc[result["end_date"].notna()].copy()
    if result.empty:
        return result

    announcement_dates = _announcement_dates(result)
    if announcement_dates is None:
        announcement_dates = pd.Series(pd.NaT, index=result.index, dtype="datetime64[ns]")
    update_rank = (
        result["update_flag"].astype("string").eq("1").astype(int)
        if "update_flag" in result.columns
        else pd.Series(0, index=result.index)
    )
    report_rank = (
        _report_type_rank(result["report_type"])
        if "report_type" in result.columns
        else pd.Series(0, index=result.index)
    )
    result["_financial_announcement"] = announcement_dates
    result["_financial_update_rank"] = update_rank
    result["_financial_report_rank"] = report_rank
    result["_financial_order"] = range(len(result))
    deduplication_keys = _deduplication_keys(result)
    result = result.sort_values(
        deduplication_keys
        + [
            "_financial_announcement",
            "_financial_update_rank",
            "_financial_report_rank",
            "_financial_order",
        ],
        kind="mergesort",
        na_position="first",
    )
    result = result.drop_duplicates(subset=deduplication_keys, keep="last")
    result = result.drop(
        columns=[
            "_financial_announcement",
            "_financial_update_rank",
            "_financial_report_rank",
            "_financial_order",
        ],
        errors="ignore",
    )
    return result.sort_values(deduplication_keys).reset_index(drop=True)


def normalize_financial_frame(
    frame: pd.DataFrame,
    *,
    as_of: Any = None,
) -> pd.DataFrame:
    """Normalize date columns, apply PIT filtering, and select revisions."""
    if frame is None or frame.empty:
        return pd.DataFrame() if frame is None else frame.copy()
    return deduplicate_financial_records(filter_financial_as_of(frame, as_of))


def financial_data_as_of(frame: pd.DataFrame) -> str:
    """Return the latest public date (or report date) represented by a frame."""
    if frame is None or frame.empty:
        return ""
    announcement_dates = _announcement_dates(frame)
    if announcement_dates is not None and announcement_dates.notna().any():
        value = announcement_dates.max()
    elif "end_date" in frame.columns:
        value = pd.to_datetime(frame["end_date"], errors="coerce").max()
    else:
        return ""
    return value.strftime("%Y-%m-%d") if pd.notna(value) else ""
