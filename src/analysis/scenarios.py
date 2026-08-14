"""基于既有证据的确定性情景和失效条件。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def build_scenarios(
    *,
    technical: Mapping[str, Any],
    valuation: Mapping[str, Any],
    risk: Mapping[str, Any],
    price_levels: Mapping[str, Any],
    validation: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build optimistic/base/pessimistic views from existing local evidence.

    This function does not assign probabilities or invent prices. A scenario
    only references an already calculated support, resistance, or target.
    """
    validation = validation or {}
    trend = technical.get("trend")
    if not isinstance(trend, Mapping):
        trend = price_levels.get("trend", {})
    trend = trend if isinstance(trend, Mapping) else {}

    current_price = price_levels.get("current_price")
    supports = _levels(price_levels.get("supports"))
    resistances = _levels(price_levels.get("resistances"))
    targets = price_levels.get("targets")
    targets = targets if isinstance(targets, Mapping) else {}
    buy_targets = _levels(targets.get("buy_targets"))
    sell_targets = _levels(targets.get("sell_targets"))

    direction = str(trend.get("direction") or "震荡")
    strength = str(trend.get("strength_label") or trend.get("strength") or "未知")
    valuation_level = _valuation_level(valuation)
    risk_level = _risk_level(risk)
    confidence_cap = _confidence_cap(validation)
    quality = str(validation.get("status") or "pass")

    optimistic_reference = sell_targets[0] if sell_targets else _first(resistances)
    pessimistic_reference = buy_targets[0] if buy_targets else _first(supports)
    base_reference = optimistic_reference if direction == "上升" else pessimistic_reference

    invalidation_conditions = build_invalidation_conditions(
        supports=supports,
        resistances=resistances,
        risk=risk,
        validation=validation,
    )

    common = {
        "confidence_cap": confidence_cap,
        "quality": quality,
        "source_refs": ["technical", "valuation", "risk", "price_levels", "validation"],
    }
    scenarios = {
        "optimistic": {
            **common,
            "label": "乐观",
            "direction": "上升",
            "reference_price": optimistic_reference,
            "drivers": [
                f"当前趋势：{direction}/{strength}",
                f"估值状态：{valuation_level}",
                f"风险状态：{risk_level}",
            ],
            "trigger_conditions": ["趋势保持，且收盘价向上突破最近阻力或目标价位"],
            "invalidation_conditions": _scenario_invalidations(
                invalidation_conditions, exclude="resistance"
            ),
        },
        "base": {
            **common,
            "label": "基准",
            "direction": direction,
            "reference_price": base_reference or _price_reference(current_price, "当前价"),
            "drivers": [
                f"趋势维持{direction}状态",
                f"估值状态：{valuation_level}",
                f"风险状态：{risk_level}",
            ],
            "trigger_conditions": ["价格在最近支撑与阻力区间内运行"],
            "invalidation_conditions": invalidation_conditions,
        },
        "pessimistic": {
            **common,
            "label": "悲观",
            "direction": "下降",
            "reference_price": pessimistic_reference,
            "drivers": [
                "趋势转弱或跌破最近支撑",
                f"风险状态：{risk_level}",
            ],
            "trigger_conditions": ["收盘价跌破最近支撑位，且趋势强度继续下降"],
            "invalidation_conditions": _scenario_invalidations(
                invalidation_conditions, exclude="support"
            ),
        },
    }

    return {
        "method_version": "scenario-v1",
        "quality": quality,
        "confidence_cap": confidence_cap,
        "scenarios": scenarios,
        "invalidation_conditions": invalidation_conditions,
    }


def build_invalidation_conditions(
    *,
    supports: list[dict[str, Any]],
    resistances: list[dict[str, Any]],
    risk: Mapping[str, Any],
    validation: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Return observable conditions that invalidate the current framing."""
    conditions: list[dict[str, Any]] = []
    support = _first(supports)
    resistance = _first(resistances)
    if support:
        conditions.append(
            {
                "id": "price.break_support",
                "condition": f"收盘价跌破最近支撑 {support['price']} 元",
                "severity": "high",
                "source_refs": ["price_levels.supports[0]"],
            }
        )
    if resistance:
        conditions.append(
            {
                "id": "price.break_resistance_context",
                "condition": (
                    f"收盘价站上最近阻力 {resistance['price']} 元后，"
                    "原震荡判断需要重新评估"
                ),
                "severity": "medium",
                "source_refs": ["price_levels.resistances[0]"],
            }
        )
    if not support and not resistance:
        conditions.append(
            {
                "id": "price.levels_missing",
                "condition": "缺少有效支撑和阻力，无法定义价格失效条件",
                "severity": "medium",
                "source_refs": ["price_levels"],
            }
        )

    risk_level = _risk_level(risk)
    if risk_level in {"高风险", "中高风险"}:
        conditions.append(
            {
                "id": "risk.reassessment",
                "condition": "风险等级处于中高或高风险时，任何情景均需重新评估",
                "severity": "high",
                "source_refs": ["risk.risk_level"],
            }
        )

    if str(validation.get("status") or "pass") != "pass":
        conditions.append(
            {
                "id": "data.quality_change",
                "condition": "数据质量未达到 pass 时，不得提高本证据包的置信度",
                "severity": "high",
                "source_refs": ["validation"],
            }
        )
    return conditions


def _levels(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping) and "price" in item]


def _first(levels: list[dict[str, Any]]) -> dict[str, Any] | None:
    return levels[0] if levels else None


def _price_reference(price: Any, reason: str) -> dict[str, Any] | None:
    if price is None:
        return None
    return {"price": price, "reason": reason, "confidence": "低"}


def _valuation_level(valuation: Mapping[str, Any]) -> str:
    percentiles = valuation.get("percentiles")
    if isinstance(percentiles, Mapping):
        return str(percentiles.get("level") or "无法判断")
    return "无法判断"


def _risk_level(risk: Mapping[str, Any]) -> str:
    value = risk.get("risk_level")
    if isinstance(value, Mapping):
        return str(value.get("label") or "未知")
    return "未知"


def _confidence_cap(validation: Mapping[str, Any]) -> int:
    value = validation.get("confidence_cap", 80)
    if not isinstance(value, (int, float)):
        return 80
    return max(0, min(100, int(value)))


def _scenario_invalidations(
    conditions: list[dict[str, Any]],
    *,
    exclude: str,
) -> list[dict[str, Any]]:
    if exclude == "resistance":
        return [
            item for item in conditions if item["id"] != "price.break_resistance_context"
        ]
    if exclude == "support":
        return [item for item in conditions if item["id"] != "price.break_support"]
    return list(conditions)
