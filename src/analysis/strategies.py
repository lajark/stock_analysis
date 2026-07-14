"""分析策略 — 多维度权重评分。

从 Stocks/backend/analysis/strategies.py 迁移，简化为函数式接口。
不依赖 Dify 或外部服务，纯本地计算。
"""

from typing import Any


def evaluate_default(
    technical: dict[str, Any],
    fundamental: dict[str, Any],
    risk: dict[str, Any],
) -> dict[str, Any]:
    """默认均衡策略：技术 40% + 基本面 30% + 情绪 20% + 风险 10%。

    情绪评分用技术指标代理（RSI + MACD + 成交量）。
    """
    # 技术评分（0-100）
    tech_score = _score_technical(technical)

    # 基本面评分
    fund_score = fundamental.get("score", 50)

    # 情绪评分（从技术指标推断）
    sentiment_score = _score_sentiment(technical)

    # 风险评分（风险越低分数越高）
    risk_score = risk.get("risk_level", {}).get("score", 50)

    # 加权综合
    overall = (
        tech_score * 0.40
        + fund_score * 0.30
        + sentiment_score * 0.20
        + risk_score * 0.10
    )

    return {
        "strategy": "default",
        "name": "默认均衡策略",
        "weights": {"technical": 0.40, "fundamental": 0.30, "sentiment": 0.20, "risk": 0.10},
        "scores": {
            "technical": round(tech_score, 1),
            "fundamental": round(fund_score, 1),
            "sentiment": round(sentiment_score, 1),
            "risk": round(risk_score, 1),
        },
        "overall": round(overall, 1),
        "signal": _signal(overall),
    }


def evaluate_conservative(
    technical: dict[str, Any],
    fundamental: dict[str, Any],
    risk: dict[str, Any],
) -> dict[str, Any]:
    """保守策略：基本面 50% + 风险 30% + 技术 15% + 情绪 5%。"""
    tech_score = _score_technical(technical)
    fund_score = fundamental.get("score", 50)
    sentiment_score = _score_sentiment(technical)
    risk_score = risk.get("risk_level", {}).get("score", 50)

    overall = (
        tech_score * 0.15
        + fund_score * 0.50
        + sentiment_score * 0.05
        + risk_score * 0.30
    )

    return {
        "strategy": "conservative",
        "name": "保守稳健策略",
        "weights": {"technical": 0.15, "fundamental": 0.50, "sentiment": 0.05, "risk": 0.30},
        "scores": {
            "technical": round(tech_score, 1),
            "fundamental": round(fund_score, 1),
            "sentiment": round(sentiment_score, 1),
            "risk": round(risk_score, 1),
        },
        "overall": round(overall, 1),
        "signal": _signal(overall),
    }


def evaluate_aggressive(
    technical: dict[str, Any],
    fundamental: dict[str, Any],
    risk: dict[str, Any],
) -> dict[str, Any]:
    """激进策略：技术 50% + 情绪 30% + 基本面 15% + 风险 5%。"""
    tech_score = _score_technical(technical)
    fund_score = fundamental.get("score", 50)
    sentiment_score = _score_sentiment(technical)
    risk_score = risk.get("risk_level", {}).get("score", 50)

    overall = (
        tech_score * 0.50
        + fund_score * 0.15
        + sentiment_score * 0.30
        + risk_score * 0.05
    )

    return {
        "strategy": "aggressive",
        "name": "激进增长策略",
        "weights": {"technical": 0.50, "fundamental": 0.15, "sentiment": 0.30, "risk": 0.05},
        "scores": {
            "technical": round(tech_score, 1),
            "fundamental": round(fund_score, 1),
            "sentiment": round(sentiment_score, 1),
            "risk": round(risk_score, 1),
        },
        "overall": round(overall, 1),
        "signal": _signal(overall),
    }


# ------------------------------------------------------------------
# 评分计算
# ------------------------------------------------------------------
def _score_technical(tech: dict[str, Any]) -> float:
    """从技术指标摘要计算技术评分（0-100）。"""
    score = 50.0

    trend = tech.get("trend", "震荡")
    if trend == "上升":
        score += 15
    elif trend == "下降":
        score -= 15

    if tech.get("macd_status") == "金叉":
        score += 10
    else:
        score -= 5

    rsi = tech.get("rsi", 50)
    if 40 <= rsi <= 60:
        score += 5
    elif rsi > 80:
        score -= 10
    elif rsi < 20:
        score += 10  # 超卖可能是机会

    kdj_status = tech.get("kdj_status", "中性")
    if kdj_status == "超卖":
        score += 5

    vol_ratio = tech.get("volume_ratio", 1.0)
    if vol_ratio > 1.5:
        score += 5
    elif vol_ratio < 0.5:
        score -= 5

    return max(0, min(100, score))


def _score_sentiment(tech: dict[str, Any]) -> float:
    """从技术指标推断市场情绪评分（0-100）。"""
    score = 50.0

    rsi = tech.get("rsi", 50)
    if 50 <= rsi <= 70:
        score += 10
    elif rsi > 70:
        score += 5  # 强势但可能过热

    if tech.get("macd_status") == "金叉":
        score += 10

    if tech.get("bollinger_position") == "上轨上方":
        score += 5
    elif tech.get("bollinger_position") == "下轨下方":
        score -= 10

    vol_ratio = tech.get("volume_ratio", 1.0)
    if vol_ratio > 1.2:
        score += 5
    elif vol_ratio < 0.7:
        score -= 5

    return max(0, min(100, score))


def _signal(overall: float) -> dict[str, str]:
    """生成信号。"""
    if overall >= 70:
        return {"type": "强势", "action": "关注", "description": "综合评分较高，多方面表现良好"}
    elif overall >= 55:
        return {"type": "中性偏强", "action": "观察", "description": "整体表现尚可，存在改善空间"}
    elif overall >= 40:
        return {"type": "中性偏弱", "action": "谨慎", "description": "存在一定风险，需要更深入分析"}
    else:
        return {"type": "弱势", "action": "回避", "description": "综合评分偏低，多维度存在风险"}


# ------------------------------------------------------------------
# 策略工厂
# ------------------------------------------------------------------
STRATEGIES = {
    "default": evaluate_default,
    "conservative": evaluate_conservative,
    "aggressive": evaluate_aggressive,
}


def evaluate(strategy: str, **kwargs: Any) -> dict[str, Any]:
    """策略评估入口。

    Args:
        strategy: 策略名称 (default | conservative | aggressive)
        **kwargs: technical, fundamental, risk 字典

    Returns:
        策略评估结果
    """
    func = STRATEGIES.get(strategy, evaluate_default)
    return func(**kwargs)