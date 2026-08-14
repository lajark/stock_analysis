"""基于结构化证据的规则式知识上下文路由。"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any

from src.reports.knowledge_retriever import retrieve_fragments

CONTEXT_ROUTER_VERSION = "context-router-v1"
DEFAULT_MAX_CHARS = 5000
DEFAULT_MAX_FRAGMENTS = 8


def route_context(
    mode: str,
    package: Mapping[str, Any],
    *,
    max_chars: int = DEFAULT_MAX_CHARS,
    max_fragments: int = DEFAULT_MAX_FRAGMENTS,
) -> dict[str, Any]:
    """Select relevant knowledge fragments and return auditable metadata."""
    dimensions = select_dimensions(mode, package)
    fragments = retrieve_fragments(dimensions, max_chars=max_chars)
    fragments = _fit_fragments(fragments, max_chars, max_fragments)
    text = "\n\n---\n\n".join(fragment["text"] for fragment in fragments)
    content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    prompt_text = ""
    if text:
        prompt_text = (
            "\n\n## 规则路由参考知识库\n\n"
            f"{text}\n\n---\n"
            "请只将以上内容作为辅助框架，并以当前结构化证据和数据质量为准。"
        )

    return {
        "router_version": CONTEXT_ROUTER_VERSION,
        "mode": mode,
        "dimensions": dimensions,
        "fragments": [
            {
                "id": fragment["id"],
                "dimension": fragment["dimension"],
                "content_hash": hashlib.sha256(
                    fragment["text"].encode("utf-8")
                ).hexdigest(),
                "char_count": len(fragment["text"]),
                "truncated": fragment["truncated"],
            }
            for fragment in fragments
        ],
        "content_hash": content_hash,
        "char_count": len(text),
        "max_chars": max_chars,
        "max_fragments": max_fragments,
        "prompt_text": prompt_text,
    }


def select_dimensions(mode: str, package: Mapping[str, Any]) -> list[str]:
    """Select dimensions from mode plus current evidence signals."""
    dimensions: list[str] = []
    base = {
        "quick": [],
        "deep": ["trend", "technical_risk", "risk_control"],
        "value": ["peg_strategy", "risk_control"],
        "trade": ["trend", "support_resistance", "risk_control"],
    }
    for dimension in base.get(mode, []):
        _append_unique(dimensions, dimension)

    technical = _mapping(package.get("technical"))
    valuation = _mapping(package.get("valuation"))
    risk = _mapping(package.get("risk"))
    price_levels = _mapping(package.get("price_levels"))
    quality = str(package.get("quality") or "ok")
    data_gaps = package.get("data_gaps")

    trend = technical.get("trend")
    if isinstance(trend, str) and trend in {"上升", "下降", "震荡"}:
        _append_unique(dimensions, "trend")
    if technical.get("macd_status") in {"金叉", "死叉"}:
        _append_unique(dimensions, "macd")
    if technical.get("rsi_status") in {"超买", "超卖"}:
        _append_unique(dimensions, "rsi")
    if technical.get("kdj_status") in {"超买", "超卖"}:
        _append_unique(dimensions, "kdj")
    if technical.get("bollinger_position") not in {None, "轨道内"}:
        _append_unique(dimensions, "bollinger")
    volume_ratio = technical.get("volume_ratio")
    if isinstance(volume_ratio, (int, float)) and (volume_ratio > 1.5 or volume_ratio < 0.7):
        _append_unique(dimensions, "volume")

    if price_levels.get("supports") or price_levels.get("resistances"):
        _append_unique(dimensions, "support_resistance")
    confidence = _mapping(price_levels.get("confidence"))
    buy_confidence = confidence.get("buy_confidence")
    sell_confidence = confidence.get("sell_confidence")
    if isinstance(buy_confidence, (int, float)) and buy_confidence >= 50:
        _append_unique(dimensions, "buy_signal")
    if isinstance(sell_confidence, (int, float)) and sell_confidence >= 50:
        _append_unique(dimensions, "sell_signal")

    risk_level = _mapping(risk.get("risk_level"))
    if risk_level.get("label") in {"高风险", "中高风险"}:
        _append_unique(dimensions, "risk_analysis")
        _append_unique(dimensions, "risk_control")
    elif risk_level:
        _append_unique(dimensions, "risk_analysis")

    percentiles = _mapping(valuation.get("percentiles"))
    valuation_level = percentiles.get("level")
    if valuation_level:
        _append_unique(dimensions, "market_patterns")
    if mode in {"value", "trade"} and valuation_level in {"偏低", "中等偏下"}:
        _append_unique(dimensions, "investment_cases")

    if quality != "ok" or (isinstance(data_gaps, (list, tuple)) and data_gaps):
        _append_unique(dimensions, "risk_control")

    return dimensions


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _append_unique(values: list[str], value: str) -> None:
    if value not in values:
        values.append(value)


def _fit_fragments(
    fragments: list[dict[str, Any]],
    max_chars: int,
    max_fragments: int,
) -> list[dict[str, Any]]:
    """Fit separators and fragment text inside the final prompt budget."""
    result: list[dict[str, Any]] = []
    used = 0
    for fragment in fragments[:max_fragments]:
        separator_chars = 7 if result else 0
        remaining = max_chars - used - separator_chars
        if remaining <= 0:
            break
        text = str(fragment["text"])
        truncated = bool(fragment["truncated"])
        if len(text) > remaining:
            text = text[:remaining]
            truncated = True
        copied = dict(fragment)
        copied["text"] = text
        copied["truncated"] = truncated
        result.append(copied)
        used += separator_chars + len(text)
    return result
