"""风险分析 — 波动率、最大回撤、流动性评估。

纯本地计算，零 Token 消耗。
"""

from typing import Any

import numpy as np
import pandas as pd


def analyze_risk(daily: pd.DataFrame) -> dict[str, Any]:
    """分析风险指标。

    Args:
        daily: 日线行情

    Returns:
        结构化风险分析摘要。
    """
    if daily.empty:
        return {"status": "无数据"}

    closes = daily["close"].values

    result: dict[str, Any] = {
        "volatility": _analyze_volatility(closes, daily),
        "max_drawdown": _analyze_max_drawdown(closes),
        "liquidity": _analyze_liquidity(daily),
    }

    # 综合风险评分
    result["risk_level"] = _risk_level(result)

    return result


def _analyze_volatility(closes: np.ndarray, daily: pd.DataFrame) -> dict:
    """波动率分析。"""
    if len(closes) < 5:
        return {"status": "数据不足"}

    # 日收益率
    daily_returns = np.diff(closes) / closes[:-1]

    # 年化波动率
    daily_vol = float(np.std(daily_returns))
    annual_vol = round(daily_vol * np.sqrt(250) * 100, 1)

    # 近期波动（20日）
    if len(daily_returns) >= 20:
        recent_vol = round(float(np.std(daily_returns[-20:])) * np.sqrt(250) * 100, 1)
    else:
        recent_vol = annual_vol

    # 波动率标签
    if annual_vol > 50:
        level = "极高"
    elif annual_vol > 35:
        level = "较高"
    elif annual_vol > 20:
        level = "适中"
    else:
        level = "较低"

    return {
        "annual_volatility_pct": annual_vol,
        "recent_volatility_pct": recent_vol,
        "level": level,
    }


def _analyze_max_drawdown(closes: np.ndarray) -> dict:
    """最大回撤分析。"""
    if len(closes) < 5:
        return {"status": "数据不足"}

    # 滚动最大回撤
    peak = np.maximum.accumulate(closes)
    drawdown = (closes - peak) / peak
    max_dd = float(np.min(drawdown)) * 100

    # 最近回撤
    current_dd = float(drawdown[-1]) * 100

    if max_dd < -40:
        level = "极大"
    elif max_dd < -25:
        level = "较大"
    elif max_dd < -15:
        level = "适中"
    else:
        level = "较小"

    return {
        "max_drawdown_pct": round(abs(max_dd), 1),
        "current_drawdown_pct": round(abs(current_dd), 1),
        "level": level,
    }


def _analyze_liquidity(daily: pd.DataFrame) -> dict:
    """流动性分析（成交量和换手率代理）。"""
    if daily.empty:
        return {"status": "无数据"}

    recent = daily.tail(20)
    avg_volume = float(recent["volume"].mean())
    avg_amount = float(recent["amount"].mean()) / 1e5  # Tushare amount 单位为千元，转为亿元

    # 流动性判断基于日均成交额
    if avg_amount > 10:
        level = "充裕"
    elif avg_amount > 3:
        level = "良好"
    elif avg_amount > 1:
        level = "一般"
    else:
        level = "偏低"

    return {
        "avg_daily_volume": round(avg_volume / 1e4, 1),  # 万手
        "avg_daily_amount_yi": round(avg_amount, 2),
        "level": level,
    }


def _risk_level(analysis: dict) -> dict:
    """综合风险评估。"""
    vol = analysis.get("volatility", {})
    dd = analysis.get("max_drawdown", {})
    liq = analysis.get("liquidity", {})

    risk_score = 50

    # 波动率
    vol_level = vol.get("level", "")
    if vol_level == "极高":
        risk_score -= 20
    elif vol_level == "较高":
        risk_score -= 10
    elif vol_level == "较低":
        risk_score += 10

    # 回撤
    dd_level = dd.get("level", "")
    if dd_level == "极大":
        risk_score -= 20
    elif dd_level == "较大":
        risk_score -= 10

    # 流动性
    liq_level = liq.get("level", "")
    if liq_level == "偏低":
        risk_score -= 10
    elif liq_level == "充裕":
        risk_score += 5

    risk_score = max(0, min(100, risk_score))

    if risk_score >= 70:
        label = "低风险"
    elif risk_score >= 50:
        label = "中等风险"
    elif risk_score >= 30:
        label = "中高风险"
    else:
        label = "高风险"

    return {"score": risk_score, "label": label}