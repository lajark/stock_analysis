"""多股票对比分析 — 横向比较关键指标。"""

from typing import Any

import pandas as pd

from src.analysis.indicators import calc_all_indicators, summarize_indicators
from src.analysis.price_levels import analyze_price_levels


def compare_stocks(
    stocks_data: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """多股票横向对比。

    Args:
        stocks_data: {股票代码: {info, daily, daily_basic, ...}}

    Returns:
        结构化对比结果。
    """
    comparison = {
        "stocks": [],
        "ranking": {},
    }

    metrics = {
        "pe_ttm": [],
        "roe": [],
        "rsi": [],
        "trend": [],
        "buy_confidence": [],
        "sell_confidence": [],
    }

    for code, data in stocks_data.items():
        daily = data["daily"]
        if daily.empty:
            continue

        df_with_ind = calc_all_indicators(daily)
        tech = summarize_indicators(df_with_ind)
        price_levels = analyze_price_levels(df_with_ind)
        daily_basic = data.get("daily_basic", {})

        row = {
            "code": code,
            "name": data["info"].get("name", code),
            "industry": data["info"].get("industry", ""),
            "close": tech["close"],
            "trend": tech["trend"],
            "rsi": tech["rsi"],
            "macd_status": tech["macd_status"],
            "pe_ttm": daily_basic.get("pe_ttm"),
            "pb": daily_basic.get("pb"),
            "buy_confidence": price_levels["confidence"]["buy_confidence"],
            "sell_confidence": price_levels["confidence"]["sell_confidence"],
            "supports": price_levels["supports"][:2],
            "resistances": price_levels["resistances"][:2],
        }

        # 处理 NaN
        for k in ["pe_ttm", "pb"]:
            if row[k] is not None and (isinstance(row[k], float) and pd.isna(row[k])):
                row[k] = None

        comparison["stocks"].append(row)

        # 收集排名数据
        if row["pe_ttm"] is not None:
            metrics["pe_ttm"].append((code, row["pe_ttm"]))
        metrics["rsi"].append((code, row["rsi"]))
        metrics["buy_confidence"].append((code, row["buy_confidence"]))
        metrics["sell_confidence"].append((code, row["sell_confidence"]))

    # 排名：PE 从低到高，其他从高到低
    if metrics["pe_ttm"]:
        comparison["ranking"]["pe_lowest"] = sorted(metrics["pe_ttm"], key=lambda x: x[1])[:3]
    comparison["ranking"]["rsi_strongest"] = sorted(metrics["rsi"], key=lambda x: -x[1])[:3]
    comparison["ranking"]["buy_confidence_highest"] = sorted(metrics["buy_confidence"], key=lambda x: -x[1])[:3]

    return comparison