"""Historical market-behavior normalization with point-in-time filtering."""

from __future__ import annotations

import pandas as pd


def normalize_moneyflow_frame(
    frame: pd.DataFrame | None,
    *,
    as_of: str | None = None,
) -> pd.DataFrame:
    """Normalize Tushare money-flow rows and remove future observations.

    The endpoint is daily, so ``trade_date`` is the publication/use date. A
    later revision for the same date is kept deterministically as the last
    row after stable sorting; no missing flow value is synthesized.
    """
    if frame is None or frame.empty:
        return pd.DataFrame()
    result = frame.copy()
    if "trade_date" not in result.columns:
        return pd.DataFrame()
    result["trade_date"] = pd.to_datetime(result["trade_date"], errors="coerce")
    result = result.dropna(subset=["trade_date"])
    if as_of:
        cutoff = pd.to_datetime(as_of, errors="coerce")
        if pd.notna(cutoff):
            result = result[result["trade_date"] <= cutoff]
    result = result.sort_values("trade_date", kind="stable")
    result = result.drop_duplicates(subset=["trade_date"], keep="last")
    numeric_columns = [
        column
        for column in result.columns
        if column not in {"trade_date", "ts_code"}
    ]
    for column in numeric_columns:
        result[column] = pd.to_numeric(result[column], errors="coerce")
    return result.reset_index(drop=True)


def moneyflow_data_as_of(frame: pd.DataFrame | None) -> str:
    """Return the latest usable money-flow date for provenance metadata."""
    normalized = normalize_moneyflow_frame(frame)
    if normalized.empty:
        return ""
    return normalized["trade_date"].max().strftime("%Y-%m-%d")
