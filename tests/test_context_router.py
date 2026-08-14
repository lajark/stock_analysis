"""Tests for deterministic evidence-driven knowledge routing."""

from __future__ import annotations

from src.reports.context_router import CONTEXT_ROUTER_VERSION, route_context, select_dimensions


def test_select_dimensions_responds_to_evidence_signals() -> None:
    package = {
        "technical": {
            "trend": "下降",
            "macd_status": "死叉",
            "rsi_status": "超买",
            "kdj_status": "超卖",
            "bollinger_position": "上轨",
            "volume_ratio": 1.8,
        },
        "price_levels": {
            "supports": [{"price": 90}],
            "resistances": [{"price": 110}],
            "confidence": {"buy_confidence": 55, "sell_confidence": 70},
        },
        "risk": {"risk_level": {"label": "高风险"}},
        "valuation": {"percentiles": {"level": "偏低"}},
        "quality": "partial",
        "data_gaps": ["income"],
    }

    dimensions = select_dimensions("trade", package)

    assert dimensions[:3] == ["trend", "support_resistance", "risk_control"]
    assert {
        "macd",
        "rsi",
        "kdj",
        "bollinger",
        "volume",
        "buy_signal",
        "sell_signal",
        "risk_analysis",
        "market_patterns",
        "investment_cases",
    }.issubset(dimensions)
    assert len(dimensions) == len(set(dimensions))


def test_route_context_records_stable_ids_hashes_and_budget(monkeypatch) -> None:
    fragments = [
        {
            "id": "guide.md#趋势",
            "dimension": "trend",
            "filename": "guide.md",
            "section": "趋势",
            "text": "趋势判断规则" * 20,
            "truncated": False,
        },
        {
            "id": "guide.md#风险",
            "dimension": "risk_control",
            "filename": "guide.md",
            "section": "风险",
            "text": "风险控制规则",
            "truncated": False,
        },
    ]
    monkeypatch.setattr(
        "src.reports.context_router.retrieve_fragments",
        lambda *args, **kwargs: fragments,
    )

    result = route_context(
        "trade",
        {"technical": {"trend": "上升"}},
        max_chars=30,
        max_fragments=1,
    )

    assert result["router_version"] == CONTEXT_ROUTER_VERSION
    assert result["char_count"] <= 30
    assert len(result["fragments"]) == 1
    assert result["fragments"][0]["id"] == "guide.md#趋势"
    assert result["fragments"][0]["truncated"] is True
    assert len(result["fragments"][0]["content_hash"]) == 64
    assert result["content_hash"]
    assert result["prompt_text"].startswith("\n\n## 规则路由参考知识库")


def test_quick_mode_without_signals_does_not_inject_context() -> None:
    result = route_context("quick", {})

    assert result["dimensions"] == []
    assert result["fragments"] == []
    assert result["prompt_text"] == ""
    assert result["char_count"] == 0
