"""Price-adjustment utilities for reproducible historical analysis."""

from __future__ import annotations

from typing import Literal

import numpy as np
import pandas as pd

AdjustmentMode = Literal["none", "qfq", "hfq"]
ADJUSTMENT_APPLICATION_VERSION = "tushare-factor-v1"


class AdjustmentError(ValueError):
    """Raised when an adjustment factor cannot be applied safely."""


def normalize_adjustment_factors(frame: pd.DataFrame | None) -> pd.DataFrame:
    """Normalize provider adjustment factors and reject invalid values."""
    if frame is None or frame.empty:
        return pd.DataFrame()
    required = {"trade_date", "adj_factor"}
    if not required.issubset(frame.columns):
        raise AdjustmentError("复权因子缺少 trade_date 或 adj_factor")
    result = frame[["trade_date", "adj_factor"]].copy()
    result["trade_date"] = pd.to_datetime(result["trade_date"], errors="coerce")
    result["adj_factor"] = pd.to_numeric(result["adj_factor"], errors="coerce")
    result = result.dropna(subset=["trade_date", "adj_factor"])
    if result.empty or (result["adj_factor"] <= 0).any():
        raise AdjustmentError("复权因子必须为正数")
    result = result.sort_values("trade_date", kind="stable")
    return result.drop_duplicates("trade_date", keep="last").reset_index(drop=True)


def apply_price_adjustment(
    daily: pd.DataFrame,
    factors: pd.DataFrame,
    mode: AdjustmentMode = "none",
) -> pd.DataFrame:
    """Apply Tushare-style qfq/hfq factors to OHLC prices.

    qfq uses the last factor in the requested range as the base; hfq uses the
    provider factor directly. Missing factors are an error rather than a silent raw-price
    fallback, because mixing adjusted and unadjusted rows invalidates returns.
    Volume and amount remain provider-native and are not fabricated.
    """
    if mode not in {"none", "qfq", "hfq"}:
        raise AdjustmentError("adjustment 必须是 none、qfq 或 hfq")
    if mode == "none":
        return daily.copy()
    required = {"trade_date", "open", "high", "low", "close"}
    if not required.issubset(daily.columns):
        raise AdjustmentError("日线缺少复权所需的 trade_date/OHLC 字段")
    if daily.empty:
        raise AdjustmentError("日线为空，无法复权")
    factor_frame = normalize_adjustment_factors(factors)
    if factor_frame.empty:
        raise AdjustmentError("复权因子为空，不能请求复权行情")
    result = daily.copy()
    result["trade_date"] = pd.to_datetime(result["trade_date"], errors="coerce")
    if result["trade_date"].isna().any():
        raise AdjustmentError("日线包含无效交易日期")
    merged = result.merge(factor_frame, on="trade_date", how="left", validate="one_to_one")
    if merged["adj_factor"].isna().any():
        missing = int(merged["adj_factor"].isna().sum())
        raise AdjustmentError(f"{missing} 个交易日缺少复权因子")
    if mode == "qfq":
        base = float(factor_frame["adj_factor"].iloc[-1])
        multiplier = merged["adj_factor"] / base
    else:
        multiplier = merged["adj_factor"]
    for column in ("open", "high", "low", "close"):
        merged[column] = pd.to_numeric(merged[column], errors="coerce") * multiplier
    values = merged[["open", "high", "low", "close"]].to_numpy(dtype=float)
    if not np.isfinite(values).all() or (values <= 0).any():
        raise AdjustmentError("复权后 OHLC 包含空值、非有限值或非正数")
    merged["adjustment_factor"] = multiplier
    return merged.drop(columns=["adj_factor"]).sort_values("trade_date").reset_index(drop=True)
