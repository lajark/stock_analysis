"""估值分析 — PE/PB/PS 历史分位计算。

纯本地计算，零 Token 消耗。
"""

from typing import Any

import numpy as np
import pandas as pd


def analyze_valuation(
    daily: pd.DataFrame,
    daily_basic: dict[str, Any],
    fina_indicator: pd.DataFrame,
) -> dict[str, Any]:
    """分析估值水平。

    Args:
        daily: 日线行情（用于计算历史分位）
        daily_basic: 当日每日指标（PE/PB/PS/市值）
        fina_indicator: 历史财务指标（用于计算 ROE 等）

    Returns:
        结构化估值分析摘要。
    """
    result: dict[str, Any] = {
        "current": _current_valuation(daily_basic),
        "percentiles": _calc_percentiles(daily),
        "summary": "",
    }

    # 生成估值摘要
    result["summary"] = _valuation_summary(result["current"], result["percentiles"])

    return result


def _current_valuation(daily_basic: dict) -> dict:
    """当前估值指标。"""
    pe = daily_basic.get("pe_ttm")
    pb = daily_basic.get("pb")
    ps = daily_basic.get("ps_ttm")

    return {
        "pe_ttm": round(float(pe), 2) if pe is not None and not (isinstance(pe, float) and np.isnan(pe)) else None,
        "pb": round(float(pb), 2) if pb is not None and not (isinstance(pb, float) and np.isnan(pb)) else None,
        "ps_ttm": round(float(ps), 2) if ps is not None and not (isinstance(ps, float) and np.isnan(ps)) else None,
        "total_mv_yi": round(float(daily_basic.get("total_mv", 0) or 0) / 1e8, 2),
    }


def _calc_percentiles(daily: pd.DataFrame) -> dict:
    """计算估值分位。

    用每日收盘价作为估值的代理变量（更精确的估值分位需要 PE/PB 历史序列）。
    这里用收盘价分位作为简单估值分位的参考——价格处于高位意味着估值可能偏高。
    """
    if daily.empty:
        return {"current_price_percentile": None, "note": "无历史数据"}

    closes = daily["close"].dropna().values
    current = closes[-1]

    if len(closes) < 20:
        return {"current_price_percentile": None, "note": "数据不足"}

    # 计算不同窗口的分位
    percentiles = {}
    for window_years in [1, 3, 5]:
        window_days = window_years * 250
        window_data = closes[-min(window_days, len(closes)):]
        # 当前价格在窗口中的百分位排名
        pct = round(float((window_data <= current).mean()) * 100, 1)
        percentiles[f"price_percentile_{window_years}y"] = pct

    # 价格分位含义
    pct_1y = percentiles.get("price_percentile_1y", 50)
    if pct_1y is not None:
        if pct_1y > 80:
            level = "偏高"
        elif pct_1y > 60:
            level = "中等偏上"
        elif pct_1y > 40:
            level = "中等"
        elif pct_1y > 20:
            level = "中等偏下"
        else:
            level = "偏低"
    else:
        level = "无法判断"

    percentiles["level"] = level
    return percentiles


def _valuation_summary(current: dict, percentiles: dict) -> str:
    """生成估值评估文字。"""
    pe = current.get("pe_ttm")
    pb = current.get("pb")
    level = percentiles.get("level", "无法判断")

    parts = []
    if pe is not None:
        if pe > 50:
            parts.append(f"PE(TTM) {pe} 处于较高水平")
        elif pe > 20:
            parts.append(f"PE(TTM) {pe} 处于合理偏高水平")
        elif pe > 0:
            parts.append(f"PE(TTM) {pe} 处于合理水平")
        else:
            parts.append(f"PE(TTM) 为负值（亏损）")

    if pb is not None:
        if pb > 5:
            parts.append(f"PB {pb} 较高")
        elif pb > 1:
            parts.append(f"PB {pb} 合理")
        else:
            parts.append(f"PB {pb} 低于净资产（破净）")

    parts.append(f"近1年价格分位 {percentiles.get('price_percentile_1y', 'N/A')}%（{level}）")

    return "；".join(parts) if parts else "估值数据不足"