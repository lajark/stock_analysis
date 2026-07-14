"""结构化分析包构建 — 将各模块分析结果组装为精简 JSON。

这是传给 LLM 的唯一数据格式。不包含原始数据，仅包含计算后的摘要。
"""

import json
from datetime import datetime
from typing import Any

from src.analysis.fundamentals import analyze_fundamentals
from src.analysis.indicators import calc_all_indicators, summarize_indicators
from src.analysis.price_levels import analyze_price_levels
from src.analysis.risk import analyze_risk
from src.analysis.valuation import analyze_valuation


def build_analysis_package(
    stock_info: dict[str, Any],
    daily: "pd.DataFrame",  # noqa: F821
    daily_basic: dict[str, Any],
    income: "pd.DataFrame",  # noqa: F821
    balance_sheet: "pd.DataFrame",  # noqa: F821
    cashflow: "pd.DataFrame",  # noqa: F821
    fina_indicator: "pd.DataFrame",  # noqa: F821
    analysis_date: str,
) -> dict[str, Any]:
    """构建结构化分析包。

    所有计算在本地完成，LLM 仅消费此 JSON 生成报告。

    Args:
        stock_info: 股票基本信息
        daily: 日线行情
        daily_basic: 当日每日指标
        income: 利润表
        balance_sheet: 资产负债表
        cashflow: 现金流量表
        fina_indicator: 财务指标
        analysis_date: 分析日期

    Returns:
        结构化分析包 JSON，大小通常 < 5KB。
    """
    # 计算技术指标
    daily_with_indicators = calc_all_indicators(daily)
    technical = summarize_indicators(daily_with_indicators)

    # 基本面
    fundamental = analyze_fundamentals(income, balance_sheet, cashflow, fina_indicator)

    # 估值
    valuation = analyze_valuation(daily, daily_basic, fina_indicator)

    # 风险
    risk = analyze_risk(daily)

    # 价格水平（支撑/阻力/目标价/置信度）
    price_levels = analyze_price_levels(daily_with_indicators)

    # 组装
    package = {
        "meta": {
            "analysis_date": analysis_date,
            "generated_at": datetime.now().isoformat(),
            "data_provider": "tushare",
            "data_date": daily["trade_date"].max().strftime("%Y-%m-%d") if not daily.empty else "",
        },
        "stock": {
            "code": stock_info.get("code", ""),
            "name": stock_info.get("name", ""),
            "industry": stock_info.get("industry", ""),
            "market": stock_info.get("market", ""),
        },
        "technical": technical,
        "fundamental": fundamental,
        "valuation": valuation,
        "risk": risk,
        "price_levels": price_levels,
    }

    return package


def package_to_json(package: dict[str, Any]) -> str:
    """将分析包序列化为 JSON 字符串。"""
    return json.dumps(package, ensure_ascii=False, indent=2)


def package_size(package: dict[str, Any]) -> int:
    """估算分析包大小（字节），用于成本控制。"""
    return len(package_to_json(package).encode("utf-8"))