"""价格水平分析 — 支撑位、阻力位、目标价、置信度。

纯本地计算，为交易决策提供量化的价格参考。
"""

from typing import Any

import numpy as np
import pandas as pd


def analyze_price_levels(daily: pd.DataFrame) -> dict[str, Any]:
    """分析关键价格水平。

    Args:
        daily: 日线行情（需包含 MA 指标列）

    Returns:
        结构化价格水平分析，包含支撑/阻力/目标价/置信度。
    """
    if daily.empty:
        return {"status": "无数据"}

    closes = daily["close"].values
    highs = daily["high"].values
    lows = daily["low"].values
    current = float(closes[-1])

    # 支撑位
    supports = _find_support_levels(daily, closes, lows, current)

    # 阻力位
    resistances = _find_resistance_levels(daily, closes, highs, current)

    # 趋势强度
    trend = _analyze_trend_strength(daily)

    # 目标价（3-6个月）
    targets = _estimate_targets(daily, supports, resistances, trend, current)

    # 置信度
    confidence = _assess_confidence(daily, trend, supports, resistances)

    return {
        "current_price": round(current, 2),
        "trend": trend,
        "supports": supports,
        "resistances": resistances,
        "targets": targets,
        "confidence": confidence,
    }


# ------------------------------------------------------------------
# 支撑位
# ------------------------------------------------------------------
def _find_support_levels(
    daily: pd.DataFrame,
    closes: np.ndarray,
    lows: np.ndarray,
    current: float,
) -> list[dict]:
    """识别关键支撑位。"""
    levels = []

    # 1. MA 支撑（MA20, MA60）
    for col, label in [("ma_20", "MA20"), ("ma_60", "MA60")]:
        if col in daily.columns:
            ma_val = float(daily[col].iloc[-1])
            if not np.isnan(ma_val) and ma_val < current:
                distance = (current - ma_val) / current * 100
                levels.append({
                    "price": round(ma_val, 2),
                    "type": label,
                    "distance_pct": round(distance, 1),
                    "strength": "强" if col == "ma_60" else "中",
                })

    # 2. 近期低点（20日、60日最低）
    for window, label in [(20, "20日低点"), (60, "60日低点")]:
        if len(lows) >= window:
            low = float(np.min(lows[-window:]))
            if low < current:
                distance = (current - low) / current * 100
                levels.append({
                    "price": round(low, 2),
                    "type": label,
                    "distance_pct": round(distance, 1),
                    "strength": "强" if window == 60 else "中",
                })

    # 3. 布林带下轨
    if "bb_lower" in daily.columns:
        bb_lower = float(daily["bb_lower"].iloc[-1])
        if not np.isnan(bb_lower) and bb_lower < current:
            distance = (current - bb_lower) / current * 100
            levels.append({
                "price": round(bb_lower, 2),
                "type": "布林下轨",
                "distance_pct": round(distance, 1),
                "strength": "中",
            })

    # 按距离排序，取最近的 5 个
    levels.sort(key=lambda x: x["distance_pct"])
    return levels[:5]


# ------------------------------------------------------------------
# 阻力位
# ------------------------------------------------------------------
def _find_resistance_levels(
    daily: pd.DataFrame,
    closes: np.ndarray,
    highs: np.ndarray,
    current: float,
) -> list[dict]:
    """识别关键阻力位。"""
    levels = []

    # 1. MA 阻力
    for col, label in [("ma_20", "MA20"), ("ma_60", "MA60"), ("ma_5", "MA5")]:
        if col in daily.columns:
            ma_val = float(daily[col].iloc[-1])
            if not np.isnan(ma_val) and ma_val > current:
                distance = (ma_val - current) / current * 100
                levels.append({
                    "price": round(ma_val, 2),
                    "type": label,
                    "distance_pct": round(distance, 1),
                    "strength": "强" if col == "ma_60" else "中",
                })

    # 2. 近期高点（20日、60日最高）
    for window, label in [(20, "20日高点"), (60, "60日高点")]:
        if len(highs) >= window:
            high = float(np.max(highs[-window:]))
            if high > current:
                distance = (high - current) / current * 100
                levels.append({
                    "price": round(high, 2),
                    "type": label,
                    "distance_pct": round(distance, 1),
                    "strength": "强" if window == 60 else "中",
                })

    # 3. 布林带上轨
    if "bb_upper" in daily.columns:
        bb_upper = float(daily["bb_upper"].iloc[-1])
        if not np.isnan(bb_upper) and bb_upper > current:
            distance = (bb_upper - current) / current * 100
            levels.append({
                "price": round(bb_upper, 2),
                "type": "布林上轨",
                "distance_pct": round(distance, 1),
                "strength": "中",
            })

    # 按距离排序
    levels.sort(key=lambda x: x["distance_pct"])
    return levels[:5]


# ------------------------------------------------------------------
# 趋势强度
# ------------------------------------------------------------------
def _analyze_trend_strength(daily: pd.DataFrame) -> dict[str, Any]:
    """分析趋势方向和强度。"""
    closes = daily["close"].values
    current = float(closes[-1])

    # 均线排列
    ma5 = float(daily["ma_5"].iloc[-1]) if "ma_5" in daily.columns else current
    ma20 = float(daily["ma_20"].iloc[-1]) if "ma_20" in daily.columns else current
    ma60 = float(daily["ma_60"].iloc[-1]) if "ma_60" in daily.columns else current

    if ma5 > ma20 > ma60:
        alignment = "多头排列"
        direction = "上升"
    elif ma5 < ma20 < ma60:
        alignment = "空头排列"
        direction = "下降"
    else:
        alignment = "均线缠绕"
        direction = "震荡"

    # 价格相对于 MA60 的位置
    if not np.isnan(ma60):
        ma60_distance = (current - ma60) / ma60 * 100
    else:
        ma60_distance = 0.0

    # 近期涨跌（5日、20日）
    if len(closes) >= 5:
        change_5d = (current - closes[-5]) / closes[-5] * 100
    else:
        change_5d = 0.0

    if len(closes) >= 20:
        change_20d = (current - closes[-20]) / closes[-20] * 100
    else:
        change_20d = 0.0

    # 趋势强度评分（0-100）
    strength_score = 50.0
    if alignment == "多头排列":
        strength_score += 20
    elif alignment == "空头排列":
        strength_score -= 20

    if change_20d > 5:
        strength_score += 10
    elif change_20d < -5:
        strength_score -= 10

    if change_5d > 0:
        strength_score += 5
    else:
        strength_score -= 5

    strength_score = max(0, min(100, strength_score))

    if strength_score >= 70:
        strength_label = "强势"
    elif strength_score >= 55:
        strength_label = "偏强"
    elif strength_score >= 45:
        strength_label = "中性"
    elif strength_score >= 30:
        strength_label = "偏弱"
    else:
        strength_label = "弱势"

    return {
        "direction": direction,
        "alignment": alignment,
        "strength_score": round(strength_score, 1),
        "strength_label": strength_label,
        "ma60_distance_pct": round(ma60_distance, 1),
        "change_5d_pct": round(change_5d, 1),
        "change_20d_pct": round(change_20d, 1),
    }


# ------------------------------------------------------------------
# 目标价估算
# ------------------------------------------------------------------
def _estimate_targets(
    daily: pd.DataFrame,
    supports: list[dict],
    resistances: list[dict],
    trend: dict,
    current: float,
) -> dict[str, Any]:
    """估算 3-6 个月目标买入价和卖出价。"""

    # 目标买入价：最近的有效支撑位
    buy_targets = []
    for s in supports[:3]:
        buy_targets.append({
            "price": s["price"],
            "reason": s["type"],
            "confidence": "高" if s["strength"] == "强" and s["distance_pct"] < 10 else "中",
        })

    # 如果没有支撑位低于当前价，使用回撤估算
    if not buy_targets:
        # 基于波动率的回撤估算
        daily_returns = np.diff(daily["close"].values) / daily["close"].values[:-1]
        vol = float(np.std(daily_returns[-60:])) if len(daily_returns) >= 60 else float(np.std(daily_returns))
        pullback = current * (1 - vol * 2)  # 2 倍标准差回撤
        buy_targets.append({
            "price": round(pullback, 2),
            "reason": "波动率回撤估算",
            "confidence": "低",
        })

    # 目标卖出价：最近的阻力位
    sell_targets = []
    for r in resistances[:3]:
        sell_targets.append({
            "price": r["price"],
            "reason": r["type"],
            "confidence": "高" if r["strength"] == "强" and r["distance_pct"] < 15 else "中",
        })

    if not sell_targets:
        # 基于近期高点 + 波动率
        recent_high = float(np.max(daily["high"].values[-60:]))
        sell_targets.append({
            "price": round(recent_high, 2),
            "reason": "60日高点",
            "confidence": "中",
        })

    return {
        "buy_targets": buy_targets,
        "sell_targets": sell_targets,
        "horizon": "3-6个月",
    }


# ------------------------------------------------------------------
# 置信度评估
# ------------------------------------------------------------------
def _assess_confidence(
    daily: pd.DataFrame,
    trend: dict,
    supports: list[dict],
    resistances: list[dict],
) -> dict[str, Any]:
    """评估买卖信号的置信度。"""

    # 买入置信度：多指标共振
    buy_signals = 0
    buy_total = 5

    # 1. 趋势：上升或震荡偏强
    if trend["direction"] in ("上升",) and trend["strength_score"] >= 45:
        buy_signals += 1
    elif trend["direction"] == "震荡" and trend["strength_score"] >= 50:
        buy_signals += 0.5

    # 2. MACD：金叉
    if "macd" in daily.columns and "macd_signal" in daily.columns:
        macd = float(daily["macd"].iloc[-1])
        macd_sig = float(daily["macd_signal"].iloc[-1])
        if macd > macd_sig:
            buy_signals += 1

    # 3. RSI：非超买
    if "rsi" in daily.columns:
        rsi = float(daily["rsi"].iloc[-1])
        if 30 <= rsi <= 60:
            buy_signals += 1
        elif rsi < 30:
            buy_signals += 1.5  # 超卖加更多分

    # 4. 有明确支撑位
    if supports and supports[0]["distance_pct"] < 10:
        buy_signals += 1

    # 5. 成交量配合
    if "volume_ratio" in daily.columns:
        vr = float(daily["volume_ratio"].iloc[-1])
        if vr < 0.7:  # 缩量可能是底部
            buy_signals += 0.5

    buy_confidence = round(buy_signals / buy_total * 100)

    # 卖出置信度
    sell_signals = 0
    sell_total = 5

    # 1. 趋势：下降
    if trend["direction"] == "下降":
        sell_signals += 1

    # 2. MACD：死叉
    if "macd" in daily.columns and "macd_signal" in daily.columns:
        macd = float(daily["macd"].iloc[-1])
        macd_sig = float(daily["macd_signal"].iloc[-1])
        if macd < macd_sig:
            sell_signals += 1

    # 3. RSI：超买
    if "rsi" in daily.columns:
        rsi = float(daily["rsi"].iloc[-1])
        if rsi > 70:
            sell_signals += 1.5
        elif rsi > 60:
            sell_signals += 0.5

    # 4. 接近阻力位
    if resistances and resistances[0]["distance_pct"] < 5:
        sell_signals += 1.5

    # 5. 趋势转弱
    if trend["strength_score"] < 40:
        sell_signals += 1

    sell_confidence = round(sell_signals / sell_total * 100)

    return {
        "buy_confidence": min(buy_confidence, 100),
        "buy_label": _confidence_label(buy_confidence),
        "sell_confidence": min(sell_confidence, 100),
        "sell_label": _confidence_label(sell_confidence),
    }


def _confidence_label(score: int) -> str:
    if score >= 70:
        return "高"
    elif score >= 50:
        return "中"
    elif score >= 30:
        return "低"
    else:
        return "极低"