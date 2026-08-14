"""Tests for deterministic scenarios and invalidation conditions."""

from src.analysis.scenarios import build_scenarios


def test_scenarios_reference_existing_levels_without_probabilities() -> None:
    result = build_scenarios(
        technical={"trend": "上升"},
        valuation={"percentiles": {"level": "中等"}},
        risk={"risk_level": {"label": "中等风险", "score": 50}},
        price_levels={
            "current_price": 10.0,
            "trend": {"direction": "上升", "strength_label": "偏强"},
            "supports": [{"price": 9.0, "type": "MA20"}],
            "resistances": [{"price": 12.0, "type": "前高"}],
            "targets": {"buy_targets": [], "sell_targets": [{"price": 12.0}]},
        },
        validation={"status": "pass", "confidence_cap": 80},
    )

    assert result["method_version"] == "scenario-v1"
    assert result["scenarios"]["optimistic"]["reference_price"]["price"] == 12.0
    assert result["scenarios"]["pessimistic"]["reference_price"]["price"] == 9.0
    assert "probability" not in result["scenarios"]["base"]
    assert result["invalidation_conditions"]


def test_scenarios_mark_missing_levels_as_unresolved() -> None:
    result = build_scenarios(
        technical={},
        valuation={},
        risk={"risk_level": {"label": "高风险"}},
        price_levels={},
        validation={"status": "degraded", "confidence_cap": 60},
    )

    ids = {item["id"] for item in result["invalidation_conditions"]}
    assert "price.levels_missing" in ids
    assert "risk.reassessment" in ids
    assert result["confidence_cap"] == 60
