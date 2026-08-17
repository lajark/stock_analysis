"""基本面分析 — 财务比率计算和盈利质量评估。

所有计算均在本地完成。财务表按报告期对齐，避免把不同季度或不同
公告时点的数值拼成一个结论；缺失证据不会再被当成负面证据扣分。
"""

from collections.abc import Mapping
from typing import Any

import pandas as pd

from src.analysis.financial_ratios import summarize_derived_ratios


def analyze_fundamentals(
    income: pd.DataFrame,
    balance_sheet: pd.DataFrame,
    cashflow: pd.DataFrame,
    fina_indicator: pd.DataFrame,
) -> dict[str, Any]:
    """分析基本面，返回结构化摘要。"""
    derived_ratios = summarize_derived_ratios(income, balance_sheet, fina_indicator)
    result: dict[str, Any] = {
        "revenue_trend": _analyze_revenue_trend(income),
        "profitability": _analyze_profitability(fina_indicator, derived_ratios),
        "derived_ratios": derived_ratios,
        "profit_quality": _analyze_profit_quality(income, cashflow),
        "solvency": _analyze_solvency(balance_sheet, fina_indicator),
        "growth": _analyze_growth(income),
    }

    score, evidence_count = _calculate_fundamental_score(result)
    result["score"] = score
    result["score_status"] = "有证据" if evidence_count else "无可评分证据"
    result["score_label"] = _score_label(score, evidence_count)
    result["evidence_count"] = evidence_count
    return result


def _as_frame(frame: pd.DataFrame, *value_columns: str) -> pd.DataFrame:
    """Return a clean, date-sorted frame with the requested numeric columns."""
    if frame is None or frame.empty or "end_date" not in frame.columns:
        return pd.DataFrame()
    result = frame.copy()
    result["end_date"] = pd.to_datetime(result["end_date"], errors="coerce")
    result = result.loc[result["end_date"].notna()].copy()
    for column in value_columns:
        if column in result.columns:
            result[column] = pd.to_numeric(result[column], errors="coerce")
    return result.sort_values("end_date").reset_index(drop=True)


def _number(value: Any) -> float | None:
    """Convert provider values while treating NaN as missing evidence."""
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if pd.notna(number) else None


def _year_ago_growth(frame: pd.DataFrame, column: str) -> list[float]:
    """Calculate year-over-year growth by matching actual reporting dates."""
    if frame.empty or column not in frame.columns:
        return []
    values = frame.dropna(subset=[column]).set_index("end_date")[column]
    growth: list[float] = []
    for date, current in values.items():
        prior_date = pd.Timestamp(date) - pd.DateOffset(years=1)
        prior = values.get(prior_date)
        current_number = _number(current)
        prior_number = _number(prior)
        if current_number is None or prior_number is None or prior_number == 0:
            continue
        growth.append((current_number - prior_number) / abs(prior_number) * 100)
    return growth


def _growth_status(growth: float | None) -> str:
    if growth is None:
        return "无法判断"
    if growth > 20:
        return "高速增长"
    if growth > 10:
        return "稳健增长"
    if growth > 0:
        return "低速增长"
    if growth > -10:
        return "轻微下滑"
    return "显著下滑"


def _analyze_revenue_trend(income: pd.DataFrame) -> dict[str, Any]:
    """收入趋势分析，按报告期而不是行号计算同比。"""
    frame = _as_frame(income, "revenue")
    if frame.empty or "revenue" not in frame.columns:
        return {"status": "无数据", "periods": 0}
    frame = frame.dropna(subset=["revenue"])
    if frame.empty:
        return {"status": "无数据", "periods": 0}

    latest = _number(frame.iloc[-1]["revenue"])
    growth = _year_ago_growth(frame, "revenue")
    latest_growth = growth[-1] if growth else None
    return {
        "latest_revenue_yi": round(latest / 1e8, 2) if latest is not None else None,
        "yoy_growth_pct": round(latest_growth, 1) if latest_growth is not None else None,
        "avg_yoy_growth_pct": round(sum(growth) / len(growth), 1) if growth else None,
        "status": _growth_status(latest_growth),
        "periods": len(frame),
    }


def _analyze_profitability(
    fina: pd.DataFrame,
    derived_ratios: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """盈利能力分析，优先使用基础报表推导的 ROE。

    ``fina_indicator.roe`` 保留为对照证据。当基础报表具备期初、期末
    权益和利润时，不让供应商指标口径直接决定评分；若推导失败，才
    明确降级到供应商值。
    """
    frame = _as_frame(fina, "roe", "gross_margin", "net_margin")
    latest = frame.iloc[-1] if not frame.empty else None
    provider_roe = _number(latest.get("roe")) if latest is not None else None
    gross_margin = _number(latest.get("gross_margin")) if latest is not None else None
    net_margin = _number(latest.get("net_margin")) if latest is not None else None

    ratio_metrics = derived_ratios.get("metrics", {}) if derived_ratios else {}
    derived_roe_item = ratio_metrics.get("roe", {})
    derived_roe = (
        _number(derived_roe_item.get("value"))
        if derived_roe_item.get("status") == "calculated"
        else None
    )
    roe = derived_roe if derived_roe is not None else provider_roe
    roe_source = (
        "基础报表本地推导"
        if derived_roe is not None
        else "fina_indicator.roe"
        if provider_roe is not None
        else "缺失"
    )
    roe_period = derived_roe_item.get("period") if derived_roe is not None else None
    if derived_roe is not None and provider_roe is not None:
        comparison_status = derived_roe_item.get("comparison_status", "未比较")
    elif derived_roe is not None:
        comparison_status = "供应商缺失"
    else:
        comparison_status = "未推导"

    if roe is None and gross_margin is None and net_margin is None:
        return {"status": "无数据", "periods": len(frame)}

    if roe is None:
        roe_level = "数据缺失"
    elif roe > 20:
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
        "roe": round(roe, 1) if roe is not None else None,
        "roe_source": roe_source,
        "provider_roe": round(provider_roe, 1) if provider_roe is not None else None,
        "roe_difference_pct_points": (
            round(derived_roe - provider_roe, 2)
            if derived_roe is not None and provider_roe is not None
            else None
        ),
        "roe_comparison_status": comparison_status,
        "roe_period": roe_period,
        "roe_level": roe_level,
        "gross_margin": round(gross_margin, 1) if gross_margin is not None else None,
        "net_margin": round(net_margin, 1) if net_margin is not None else None,
        "period": (
            latest["end_date"].strftime("%Y-%m-%d")
            if latest is not None
            else roe_period
        ),
    }


def _analyze_profit_quality(income: pd.DataFrame, cashflow: pd.DataFrame) -> dict[str, Any]:
    """盈利质量分析（经营现金流/净利润），仅使用共同报告期。"""
    income_frame = _as_frame(income, "net_profit")
    cashflow_frame = _as_frame(cashflow, "operating_cf")
    if income_frame.empty or cashflow_frame.empty:
        return {"status": "无数据"}
    common_dates = sorted(
        set(income_frame["end_date"]).intersection(cashflow_frame["end_date"])
    )
    if not common_dates:
        return {"status": "报告期不一致"}
    latest_date = common_dates[-1]
    income_row = income_frame.loc[income_frame["end_date"] == latest_date].iloc[-1]
    cashflow_row = cashflow_frame.loc[cashflow_frame["end_date"] == latest_date].iloc[-1]
    net_profit = _number(income_row.get("net_profit"))
    operating_cf = _number(cashflow_row.get("operating_cf"))
    if net_profit is None or operating_cf is None:
        return {"status": "数据缺失", "period": latest_date.strftime("%Y-%m-%d")}

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
        cf_ratio = None
        quality = "亏损（不适用）"

    return {
        "operating_cf_yi": round(operating_cf / 1e8, 2),
        "cf_to_profit_ratio": round(cf_ratio, 2) if cf_ratio is not None else None,
        "quality": quality,
        "period": latest_date.strftime("%Y-%m-%d"),
    }


def _analyze_solvency(balance_sheet: pd.DataFrame, fina: pd.DataFrame) -> dict[str, Any]:
    """偿债能力分析，优先使用指标，缺失时从资产负债表派生负债率。"""
    fina_frame = _as_frame(fina, "debt_to_assets", "current_ratio")
    balance_frame = _as_frame(balance_sheet, "total_assets", "total_liabilities")
    latest_fina = fina_frame.iloc[-1] if not fina_frame.empty else None
    debt_ratio = _number(latest_fina.get("debt_to_assets")) if latest_fina is not None else None
    current_ratio = _number(latest_fina.get("current_ratio")) if latest_fina is not None else None
    period = latest_fina.get("end_date") if latest_fina is not None else None

    if debt_ratio is None and not balance_frame.empty:
        latest_balance = balance_frame.iloc[-1]
        assets = _number(latest_balance.get("total_assets"))
        liabilities = _number(latest_balance.get("total_liabilities"))
        if assets and liabilities is not None:
            debt_ratio = liabilities / assets * 100
            period = latest_balance.get("end_date")
    if debt_ratio is None and current_ratio is None:
        return {"status": "无数据"}

    debt_level = None
    if debt_ratio is not None:
        debt_level = "低杠杆" if debt_ratio < 30 else "适中" if debt_ratio < 60 else "高杠杆"
    period_text = None
    if period is not None:
        formatter = getattr(period, "strftime", None)
        if callable(formatter):
            period_text = formatter("%Y-%m-%d")
    return {
        "debt_to_assets": round(debt_ratio, 1) if debt_ratio is not None else None,
        "debt_level": debt_level,
        "current_ratio": round(current_ratio, 2) if current_ratio is not None else None,
        "period": period_text,
    }


def _analyze_growth(income: pd.DataFrame) -> dict[str, Any]:
    """净利润成长性，按报告期匹配上一年同期。"""
    frame = _as_frame(income, "net_profit")
    if frame.empty or "net_profit" not in frame.columns:
        return {"status": "无数据"}
    frame = frame.dropna(subset=["net_profit"])
    growth = _year_ago_growth(frame, "net_profit")
    if not growth:
        return {"status": "数据不足", "periods": len(frame)}
    latest_yoy = growth[-1]
    if latest_yoy > 30:
        status = "高速增长"
    elif latest_yoy > 10:
        status = "稳健增长"
    elif latest_yoy > 0:
        status = "低速增长"
    else:
        status = "下滑"
    return {
        "net_profit_yoy_pct": round(latest_yoy, 1),
        "status": status,
        "periods": len(frame),
    }


def _calculate_fundamental_score(analysis: dict) -> tuple[int, int]:
    """计算基本面评分，并返回参与评分的证据维度数量。"""
    score = 50
    evidence_count = 0
    rev = analysis.get("revenue_trend", {})
    if rev.get("status") in {"高速增长", "稳健增长", "显著下滑"}:
        evidence_count += 1
        score += {"高速增长": 15, "稳健增长": 10, "显著下滑": -15}[rev["status"]]

    prof = analysis.get("profitability", {})
    roe = _number(prof.get("roe"))
    if roe is not None:
        evidence_count += 1
        score += 15 if roe > 20 else 10 if roe > 15 else 5 if roe > 10 else -15 if roe <= 0 else 0

    quality = analysis.get("profit_quality", {})
    cf_ratio = _number(quality.get("cf_to_profit_ratio"))
    if cf_ratio is not None:
        evidence_count += 1
        score += 10 if cf_ratio > 1.0 else 5 if cf_ratio > 0.5 else -10 if cf_ratio <= 0 else 0

    solvency = analysis.get("solvency", {})
    if solvency.get("debt_level") is not None:
        evidence_count += 1
        if solvency.get("debt_level") == "高杠杆":
            score -= 10
    return max(0, min(100, int(round(score)))), evidence_count


def _score_label(score: int, evidence_count: int) -> str:
    """评分标签；没有证据时避免误报为负面结论。"""
    if evidence_count == 0:
        return "无法判断"
    if score >= 70:
        return "优秀"
    if score >= 55:
        return "良好"
    if score >= 40:
        return "一般"
    return "较差"
