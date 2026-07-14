"""基本面分析 — 财务比率计算和盈利质量评估。

纯本地计算，零 Token 消耗。
"""

from typing import Any

import numpy as np
import pandas as pd


def analyze_fundamentals(
    income: pd.DataFrame,
    balance_sheet: pd.DataFrame,
    cashflow: pd.DataFrame,
    fina_indicator: pd.DataFrame,
) -> dict[str, Any]:
    """分析基本面，返回结构化摘要。

    Args:
        income: 利润表
        balance_sheet: 资产负债表
        cashflow: 现金流量表
        fina_indicator: 财务指标

    Returns:
        结构化基本面分析摘要，供 LLM 消费。
    """
    result: dict[str, Any] = {
        "revenue_trend": _analyze_revenue_trend(income),
        "profitability": _analyze_profitability(fina_indicator),
        "profit_quality": _analyze_profit_quality(income, cashflow),
        "solvency": _analyze_solvency(balance_sheet, fina_indicator),
        "growth": _analyze_growth(income),
    }

    # 综合评分
    score = _calculate_fundamental_score(result)
    result["score"] = score
    result["score_label"] = _score_label(score)

    return result


def _analyze_revenue_trend(income: pd.DataFrame) -> dict:
    """收入趋势分析。"""
    if income.empty:
        return {"status": "无数据", "periods": 0}

    income = income.sort_values("end_date")
    revenues = income["revenue"].dropna().values

    if len(revenues) < 2:
        return {"status": "数据不足", "periods": len(revenues)}

    # 计算同比增长率
    yoy_growth = []
    for i in range(len(revenues)):
        if i >= 4 and revenues[i - 4] > 0:  # 同比（4个季度前）
            yoy = (revenues[i] - revenues[i - 4]) / revenues[i - 4] * 100
            yoy_growth.append(round(float(yoy), 1))

    latest_revenue = float(revenues[-1]) / 1e8  # 转为亿

    if yoy_growth:
        avg_growth = np.mean(yoy_growth)
        if avg_growth > 20:
            status = "高速增长"
        elif avg_growth > 10:
            status = "稳健增长"
        elif avg_growth > 0:
            status = "低速增长"
        elif avg_growth > -10:
            status = "轻微下滑"
        else:
            status = "显著下滑"
    else:
        avg_growth = 0.0
        status = "无法判断"

    return {
        "latest_revenue_yi": round(latest_revenue, 2),
        "yoy_growth_pct": round(float(avg_growth), 1),
        "status": status,
        "periods": len(revenues),
    }


def _analyze_profitability(fina: pd.DataFrame) -> dict:
    """盈利能力分析。"""
    if fina.empty:
        return {"status": "无数据"}

    latest = fina.sort_values("end_date").iloc[-1]

    roe = float(latest.get("roe", 0) or 0)
    gross_margin = float(latest.get("gross_margin", 0) or 0)
    net_margin = float(latest.get("net_margin", 0) or 0)

    # ROE 评级
    if roe > 20:
        roe_level = "优秀"
    elif roe > 15:
        roe_level = "良好"
    elif roe > 10:
        roe_level = "一般"
    elif roe > 0:
        roe_level = "偏低"
    else:
        roe_level = "亏损"

    return {
        "roe": round(roe, 1),
        "roe_level": roe_level,
        "gross_margin": round(gross_margin, 1),
        "net_margin": round(net_margin, 1),
    }


def _analyze_profit_quality(income: pd.DataFrame, cashflow: pd.DataFrame) -> dict:
    """盈利质量分析（经营现金流/净利润）。"""
    if income.empty or cashflow.empty:
        return {"status": "无数据"}

    income = income.sort_values("end_date")
    cashflow = cashflow.sort_values("end_date")

    latest_income = income.iloc[-1]
    latest_cf = cashflow.iloc[-1]

    net_profit = float(latest_income.get("net_profit", 0) or 0)
    operating_cf = float(latest_cf.get("operating_cf", 0) or 0)

    if net_profit > 0:
        cf_ratio = operating_cf / net_profit
        if cf_ratio > 1.0:
            quality = "优秀（现金流充裕）"
        elif cf_ratio > 0.5:
            quality = "良好"
        elif cf_ratio > 0:
            quality = "一般（现金流偏紧）"
        else:
            quality = "差（经营现金流为负）"
    else:
        cf_ratio = 0.0
        quality = "亏损（不适用）"

    return {
        "operating_cf_yi": round(operating_cf / 1e8, 2),
        "cf_to_profit_ratio": round(float(cf_ratio), 2),
        "quality": quality,
    }


def _analyze_solvency(balance_sheet: pd.DataFrame, fina: pd.DataFrame) -> dict:
    """偿债能力分析。"""
    if fina.empty:
        return {"status": "无数据"}

    latest = fina.sort_values("end_date").iloc[-1]
    debt_ratio = float(latest.get("debt_to_assets", 0) or 0)
    current_ratio = float(latest.get("current_ratio", 0) or 0)

    if debt_ratio < 30:
        debt_level = "低杠杆"
    elif debt_ratio < 60:
        debt_level = "适中"
    else:
        debt_level = "高杠杆"

    return {
        "debt_to_assets": round(debt_ratio, 1),
        "debt_level": debt_level,
        "current_ratio": round(current_ratio, 2),
    }


def _analyze_growth(income: pd.DataFrame) -> dict:
    """成长性分析。"""
    if income.empty:
        return {"status": "无数据"}

    income = income.sort_values("end_date")
    net_profits = income["net_profit"].dropna().values

    if len(net_profits) < 5:
        return {"status": "数据不足"}

    # 最近一期同比
    if net_profits[-5] > 0:
        latest_yoy = (net_profits[-1] - net_profits[-5]) / abs(net_profits[-5]) * 100
    else:
        latest_yoy = 0.0

    if latest_yoy > 30:
        growth_status = "高速增长"
    elif latest_yoy > 10:
        growth_status = "稳健增长"
    elif latest_yoy > 0:
        growth_status = "低速增长"
    else:
        growth_status = "下滑"

    return {
        "net_profit_yoy_pct": round(float(latest_yoy), 1),
        "status": growth_status,
    }


def _calculate_fundamental_score(analysis: dict) -> int:
    """计算基本面综合评分（0-100）。"""
    score = 50

    # 收入趋势
    rev = analysis.get("revenue_trend", {})
    if rev.get("status") == "高速增长":
        score += 15
    elif rev.get("status") == "稳健增长":
        score += 10
    elif rev.get("status") == "显著下滑":
        score -= 15

    # 盈利
    prof = analysis.get("profitability", {})
    roe = prof.get("roe", 0)
    if roe > 20:
        score += 15
    elif roe > 15:
        score += 10
    elif roe > 10:
        score += 5
    elif roe <= 0:
        score -= 15

    # 盈利质量
    quality = analysis.get("profit_quality", {})
    cf_ratio = quality.get("cf_to_profit_ratio", 0)
    if cf_ratio > 1.0:
        score += 10
    elif cf_ratio > 0.5:
        score += 5
    elif cf_ratio <= 0:
        score -= 10

    # 负债
    solv = analysis.get("solvency", {})
    if solv.get("debt_level") == "高杠杆":
        score -= 10

    return max(0, min(100, score))


def _score_label(score: int) -> str:
    """评分标签。"""
    if score >= 70:
        return "优秀"
    elif score >= 55:
        return "良好"
    elif score >= 40:
        return "一般"
    else:
        return "较差"