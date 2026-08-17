"""Multi-dimensional evidence collection and cautious decision adjudication."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

import numpy as np
import pandas as pd

from src.data.cninfo import classify_official_event_title

Polarity = Literal["positive", "negative", "neutral"]
OFFICIAL_EVENT_METHOD_VERSION = "official-disclosure-events-v1"
OFFICIAL_EVENT_POLARITY: dict[str, Polarity] = {
    "业绩预增": "positive",
    "业绩快报增长": "positive",
    "重大合同": "positive",
    "重大订单": "positive",
    "中标": "positive",
    "股份回购": "positive",
    "股东增持": "positive",
    "分红": "positive",
    "业绩预减": "negative",
    "业绩预亏": "negative",
    "业绩快报下降": "negative",
    "风险提示": "negative",
    "监管问询": "negative",
    "行政处罚": "negative",
    "股东减持": "negative",
    "重大诉讼": "negative",
    "停产": "negative",
    "退市风险": "negative",
}
DecisionState = Literal[
    "insufficient",
    "conflicted",
    "conditional_positive",
    "neutral",
    "conditional_negative",
]


@dataclass(frozen=True)
class EvidenceItem:
    """One auditable argument, never a free-form ungrounded opinion."""

    evidence_id: str
    dimension: str
    polarity: Polarity
    claim: str
    source: str
    as_of: str
    strength: float
    reliability: float
    scope: str = "single_stock"
    quality: float = 1.0
    independence_group: str = ""
    method_version: str = "evidence-item-v1"
    source_ref: str = ""

    def __post_init__(self) -> None:
        if not 0 <= self.strength <= 1:
            raise ValueError("evidence strength 必须在 0 到 1 之间")
        if not 0 <= self.reliability <= 1:
            raise ValueError("evidence reliability 必须在 0 到 1 之间")
        if not 0 <= self.quality <= 1:
            raise ValueError("evidence quality 必须在 0 到 1 之间")

    @property
    def weight(self) -> float:
        return self.strength * self.reliability

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["weight"] = round(self.weight, 4)
        return payload


@dataclass(frozen=True)
class DecisionResult:
    """Cautious decision state with explicit dissent and invalidation rules."""

    state: DecisionState
    confidence: int
    rationale: str
    supporting_evidence: tuple[dict[str, Any], ...]
    opposing_evidence: tuple[dict[str, Any], ...]
    unresolved_conflicts: tuple[str, ...]
    conditions: tuple[str, ...]
    invalidation_conditions: tuple[str, ...]
    method_version: str = "evidence-adjudication-v1"
    evidence_quality: dict[str, float] = field(default_factory=dict)
    independence_groups: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _as_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if np.isfinite(number) else None


def _moneyflow_evidence(
    moneyflow: pd.DataFrame | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Build independent market-behavior evidence without inventing a score."""
    source = "tushare_moneyflow-v1"
    if moneyflow is None or moneyflow.empty or "trade_date" not in moneyflow.columns:
        return [], {"status": "insufficient", "source": source}
    frame = moneyflow.copy()
    frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="coerce")
    amount_column = "net_mf_amount" if "net_mf_amount" in frame.columns else "net_mf_vol"
    if amount_column not in frame.columns:
        return [], {"status": "insufficient", "source": source}
    frame[amount_column] = pd.to_numeric(frame[amount_column], errors="coerce")
    frame = frame.dropna(subset=["trade_date", amount_column]).sort_values("trade_date")
    if frame.empty:
        return [], {"status": "insufficient", "source": source}
    recent = frame.tail(5)
    net_flow = float(recent[amount_column].sum())
    as_of = recent["trade_date"].iloc[-1].strftime("%Y-%m-%d")
    if net_flow > 0:
        polarity: Polarity = "positive"
        claim = f"近 5 个交易日资金流净额为正（接口原始单位合计 {net_flow:.2f}）"
    elif net_flow < 0:
        polarity = "negative"
        claim = f"近 5 个交易日资金流净额为负（接口原始单位合计 {net_flow:.2f}）"
    else:
        polarity = "neutral"
        claim = "近 5 个交易日资金流净额接近零"
    evidence = EvidenceItem(
        evidence_id="sentiment.moneyflow.net_5d",
        dimension="market_behavior",
        polarity=polarity,
        claim=claim,
        source=source,
        as_of=as_of,
        strength=0.5 if polarity != "neutral" else 0.25,
        reliability=0.55,
        quality=0.65,
        independence_group="tushare_moneyflow",
    ).to_dict()
    return [evidence], {
        "status": "ok",
        "source": source,
        "as_of": as_of,
        "net_flow_5d": round(net_flow, 4),
        "unit": "provider_raw",
    }


def normalize_official_event_evidence(
    records: Sequence[Mapping[str, Any]] | None,
    *,
    as_of: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Normalize official disclosure events using a fixed, non-LLM taxonomy."""
    normalized: list[dict[str, Any]] = []
    if records:
        for raw in records:
            try:
                source = str(raw["source"]).strip()
                source_key = source.lower()
                if not any(
                    token in source_key
                    for token in ("cninfo", "sse", "szse", "cninfo.com.cn")
                ):
                    raise ValueError
                item_id = str(raw["item_id"])
                published_at = str(raw["published_at"])
                title = str(raw.get("title") or raw.get("claim") or "").strip()
                if not title:
                    raise ValueError
                event_type = str(
                    raw.get("event_type") or classify_official_event_title(title)
                ).strip()
                polarity = OFFICIAL_EVENT_POLARITY.get(event_type, "neutral")
                normalized.append(
                    {
                        "evidence_id": f"official_event.{item_id}",
                        "dimension": "official_event",
                        "polarity": polarity,
                        "claim": title,
                        "source": source,
                        "as_of": published_at,
                        "strength": float(
                            raw.get("strength", 0.55 if polarity != "neutral" else 0.1)
                        ),
                        "reliability": float(raw.get("reliability", 0.85)),
                        "quality": float(raw.get("quality", 0.85)),
                        "independence_group": f"official_disclosure:{source}",
                        "method_version": OFFICIAL_EVENT_METHOD_VERSION,
                        "source_ref": str(raw.get("source_url", "")),
                    }
                )
            except (KeyError, TypeError, ValueError):
                continue
    return normalize_external_evidence(normalized, as_of=as_of)


def normalize_external_evidence(
    records: Sequence[Mapping[str, Any]] | None,
    *,
    as_of: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Validate caller-supplied news/survey evidence without fetching data.

    Records must carry their own source and as-of date. Future observations and
    the existing price-volume/moneyflow proxies are rejected so a later source
    cannot be mislabeled as an independent signal.
    """
    if not records:
        return [], {
            "status": "insufficient",
            "sources": [],
            "source_count": 0,
            "independence_groups": [],
            "coverage_start": "",
            "coverage_end": "",
            "freshness_days": None,
            "quality_min": None,
            "quality_mean": None,
            "accepted_count": 0,
            "rejected_count": 0,
            "method_version": "external-evidence-v1",
        }
    cutoff = pd.to_datetime(as_of, errors="coerce") if as_of else None
    rejected = 0
    accepted: list[dict[str, Any]] = []
    blocked_sources = {"price_volume_proxy-v1", "tushare_moneyflow-v1"}
    for raw in records:
        try:
            source = str(raw["source"]).strip()
            evidence_as_of = pd.to_datetime(raw["as_of"], errors="coerce")
            if not source or source in blocked_sources or pd.isna(evidence_as_of):
                raise ValueError
            if cutoff is not None and pd.notna(cutoff) and evidence_as_of > cutoff:
                raise ValueError
            item = EvidenceItem(
                evidence_id=str(raw["evidence_id"]),
                dimension=str(raw.get("dimension", "sentiment")),
                polarity=raw["polarity"],
                claim=str(raw["claim"]),
                source=source,
                as_of=evidence_as_of.strftime("%Y-%m-%d"),
                strength=float(raw["strength"]),
                reliability=float(raw["reliability"]),
                scope=str(raw.get("scope", "single_stock")),
                quality=float(raw.get("quality", 0.5)),
                independence_group=str(
                    raw.get("independence_group") or f"external:{source}"
                ),
                method_version=str(raw.get("method_version", "external-evidence-v1")),
                source_ref=str(raw.get("source_ref", "")),
            )
        except (KeyError, TypeError, ValueError):
            rejected += 1
            continue
        accepted.append(item.to_dict())

    accepted.sort(key=lambda item: (item["as_of"], item["source"], item["evidence_id"]))
    deduplicated: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in accepted:
        key = (item["source"], item["evidence_id"])
        if key in seen:
            rejected += 1
            continue
        seen.add(key)
        deduplicated.append(item)
    sources = sorted({item["source"] for item in deduplicated})
    item_dates = [pd.Timestamp(item["as_of"]) for item in deduplicated]
    quality_values = [float(item["quality"]) for item in deduplicated]
    groups = sorted(
        {
            item["independence_group"]
            for item in deduplicated
            if item["independence_group"]
        }
    )
    freshness_days = None
    if cutoff is not None and pd.notna(cutoff) and item_dates:
        freshness_days = int((pd.Timestamp(cutoff) - max(item_dates)).days)
    return deduplicated, {
        "status": "ok" if deduplicated else "insufficient",
        "sources": sources,
        "source_count": len(sources),
        "independence_groups": groups,
        "as_of": max((item["as_of"] for item in deduplicated), default=""),
        "coverage_start": min((item["as_of"] for item in deduplicated), default=""),
        "coverage_end": max((item["as_of"] for item in deduplicated), default=""),
        "freshness_days": freshness_days,
        "quality_min": min(quality_values) if quality_values else None,
        "quality_mean": float(np.mean(quality_values)) if quality_values else None,
        "accepted_count": len(deduplicated),
        "rejected_count": rejected,
        "method_version": "external-evidence-v1",
    }


def analyze_market_sentiment(
    daily: pd.DataFrame,
    moneyflow: pd.DataFrame | None = None,
    external_evidence: Sequence[Mapping[str, Any]] | None = None,
    official_event_records: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a transparent price-volume sentiment proxy.

    This is not news or investor-survey sentiment. It is deliberately labelled
    as a proxy so downstream decisions cannot mistake it for an independent
    information source.
    """
    source = "price_volume_proxy-v1"
    analysis_cutoff = None
    if not daily.empty and "trade_date" in daily.columns:
        dates = pd.to_datetime(daily["trade_date"], errors="coerce").dropna()
        if not dates.empty:
            analysis_cutoff = dates.max().strftime("%Y-%m-%d")
    external_rows, external_summary = normalize_external_evidence(
        external_evidence,
        as_of=analysis_cutoff,
    )
    official_rows, official_summary = normalize_official_event_evidence(
        official_event_records,
        as_of=analysis_cutoff,
    )
    flow_evidence, flow_summary = _moneyflow_evidence(moneyflow)
    independent_sources = (
        ([flow_summary["source"]] if flow_evidence else [])
        + list(external_summary["sources"])
        + list(official_summary["sources"])
    )
    independent_source = independent_sources[0] if independent_sources else None
    required = {"trade_date", "close", "volume"}
    if daily.empty or not required.issubset(daily.columns):
        return {
            "status": (
                "ok"
                if flow_evidence or external_rows or official_rows
                else "insufficient"
            ),
            "source": source,
            "sources": [source] + independent_sources,
            "independent_source": independent_source,
            "evidence": flow_evidence + external_rows + official_rows,
            "moneyflow": flow_summary,
            "external": external_summary,
            "official_events": official_summary,
        }
    frame = daily.copy()
    frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="coerce")
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    frame["volume"] = pd.to_numeric(frame["volume"], errors="coerce")
    frame = frame.dropna(subset=["trade_date", "close", "volume"]).sort_values("trade_date")
    if len(frame) < 20:
        return {
            "status": (
                "ok"
                if flow_evidence or external_rows or official_rows
                else "insufficient"
            ),
            "source": source,
            "sources": [source] + independent_sources,
            "independent_source": independent_source,
            "evidence": flow_evidence + external_rows + official_rows,
            "moneyflow": flow_summary,
            "external": external_summary,
            "official_events": official_summary,
        }

    recent_return = float(frame["close"].iloc[-1] / frame["close"].iloc[-20] - 1.0)
    volume_base = float(frame["volume"].iloc[-20:].mean())
    volume_ratio = float(frame["volume"].iloc[-1] / volume_base) if volume_base > 0 else None
    returns = frame["close"].pct_change().dropna()
    volatility = float(returns.tail(20).std(ddof=0) * np.sqrt(252)) if not returns.empty else None
    score = 50.0 + max(-20.0, min(20.0, recent_return * 100.0))
    if volume_ratio is not None:
        score += max(-10.0, min(10.0, (volume_ratio - 1.0) * 10.0))
    score = max(0.0, min(100.0, score))
    as_of = frame["trade_date"].iloc[-1].strftime("%Y-%m-%d")

    evidence: list[EvidenceItem] = []
    if recent_return > 0:
        evidence.append(
            EvidenceItem(
                evidence_id="sentiment.momentum.positive",
                dimension="sentiment",
                polarity="positive",
                claim=f"近 20 个交易日价格变化为 {recent_return:.1%}",
                source=source,
                as_of=as_of,
                strength=min(1.0, abs(recent_return) * 5),
                reliability=0.4,
                quality=0.55,
                independence_group="price_volume_proxy",
            )
        )
    elif recent_return < 0:
        evidence.append(
            EvidenceItem(
                evidence_id="sentiment.momentum.negative",
                dimension="sentiment",
                polarity="negative",
                claim=f"近 20 个交易日价格变化为 {recent_return:.1%}",
                source=source,
                as_of=as_of,
                strength=min(1.0, abs(recent_return) * 5),
                reliability=0.4,
                quality=0.55,
                independence_group="price_volume_proxy",
            )
        )
    if volume_ratio is not None and volume_ratio > 1.2:
        polarity: Polarity = "positive" if recent_return >= 0 else "negative"
        evidence.append(
            EvidenceItem(
                evidence_id=f"sentiment.activity.{polarity}",
                dimension="sentiment",
                polarity=polarity,
                claim=f"最新成交量约为 20 日均量的 {volume_ratio:.2f} 倍",
                source=source,
                as_of=as_of,
                strength=min(1.0, (volume_ratio - 1.0) / 1.5),
                reliability=0.35,
                quality=0.55,
                independence_group="price_volume_proxy",
            )
        )
    all_evidence = (
        [item.to_dict() for item in evidence]
        + flow_evidence
        + external_rows
        + official_rows
    )
    sources = [source] + independent_sources
    return {
        "status": "ok" if all_evidence else "neutral",
        "source": source,
        "sources": sources,
        "independent_source": independent_source,
        "as_of": as_of,
        "score": round(score, 1),
        "recent_return_pct": round(recent_return * 100, 2),
        "volume_ratio": round(volume_ratio, 2) if volume_ratio is not None else None,
        "annualized_volatility": round(volatility, 4) if volatility is not None else None,
        "moneyflow": flow_summary,
        "external": external_summary,
        "official_events": official_summary,
        "evidence": all_evidence,
    }


def _analysis_evidence(
    fundamental: dict[str, Any],
    technical: dict[str, Any],
    sentiment: dict[str, Any],
    *,
    as_of: str,
) -> list[EvidenceItem]:
    evidence: list[EvidenceItem] = []
    score = _as_float(fundamental.get("score"))
    score_status = fundamental.get("score_status")
    if score is not None and score_status != "无可评分证据":
        polarity: Polarity = "positive" if score > 55 else "negative" if score < 45 else "neutral"
        if polarity != "neutral":
            evidence.append(
                EvidenceItem(
                    evidence_id="fundamental.score",
                    dimension="fundamental",
                    polarity=polarity,
                    claim=f"基本面综合评分为 {score:.0f}",
                    source="local_fundamental_analysis-v2",
                    as_of=as_of,
                    strength=min(1.0, abs(score - 50) / 30),
                    reliability=0.75,
                    quality=0.9,
                    independence_group="fundamental_local",
                )
            )

    trend = technical.get("trend")
    if trend in {"上升", "下降"}:
        evidence.append(
            EvidenceItem(
                evidence_id="technical.trend",
                dimension="technical",
                polarity="positive" if trend == "上升" else "negative",
                claim=f"均线趋势为{trend}",
                source="local_technical_analysis-v1",
                as_of=as_of,
                strength=0.45,
                reliability=0.65,
                quality=0.85,
                independence_group="technical_local",
            )
        )
    macd_status = technical.get("macd_status")
    if macd_status in {"金叉", "死叉"}:
        evidence.append(
            EvidenceItem(
                evidence_id="technical.macd",
                dimension="technical",
                polarity="positive" if macd_status == "金叉" else "negative",
                claim=f"MACD 状态为{macd_status}",
                source="local_technical_analysis-v1",
                as_of=as_of,
                strength=0.3,
                reliability=0.6,
                quality=0.85,
                independence_group="technical_local",
            )
        )
    rsi = _as_float(technical.get("rsi"))
    if rsi is not None and rsi > 80:
        evidence.append(
            EvidenceItem(
                evidence_id="technical.rsi.overbought",
                dimension="technical",
                polarity="negative",
                claim=f"RSI 为 {rsi:.1f}，处于显著超买区",
                source="local_technical_analysis-v1",
                as_of=as_of,
                strength=0.35,
                reliability=0.55,
                quality=0.85,
                independence_group="technical_local",
            )
        )
    elif rsi is not None and rsi < 20:
        evidence.append(
            EvidenceItem(
                evidence_id="technical.rsi.oversold",
                dimension="technical",
                polarity="positive",
                claim=f"RSI 为 {rsi:.1f}，处于显著超卖区",
                source="local_technical_analysis-v1",
                as_of=as_of,
                strength=0.25,
                reliability=0.45,
                quality=0.85,
                independence_group="technical_local",
            )
        )

    for raw_item in sentiment.get("evidence", []):
        try:
            evidence.append(
                EvidenceItem(
                    evidence_id=str(raw_item["evidence_id"]),
                    dimension=str(raw_item["dimension"]),
                    polarity=raw_item["polarity"],
                    claim=str(raw_item["claim"]),
                    source=str(raw_item["source"]),
                    as_of=str(raw_item["as_of"]),
                    strength=float(raw_item["strength"]),
                    reliability=float(raw_item["reliability"]),
                    scope=str(raw_item.get("scope", "single_stock")),
                    quality=float(raw_item.get("quality", 1.0)),
                    independence_group=str(raw_item.get("independence_group", "")),
                    method_version=str(raw_item.get("method_version", "evidence-item-v1")),
                    source_ref=str(raw_item.get("source_ref", "")),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return evidence


def build_investment_decision(
    fundamental: dict[str, Any],
    technical: dict[str, Any],
    sentiment: dict[str, Any],
    *,
    as_of: str,
) -> dict[str, Any]:
    """Adjudicate evidence without converting disagreement into a buy/sell order."""
    evidence = _analysis_evidence(fundamental, technical, sentiment, as_of=as_of)
    supporting = [item for item in evidence if item.polarity == "positive"]
    opposing = [item for item in evidence if item.polarity == "negative"]
    positive_weight = sum(item.weight for item in supporting)
    negative_weight = sum(item.weight for item in opposing)
    total_weight = positive_weight + negative_weight
    quality_values = [item.quality for item in evidence]
    quality_summary = {
        "min": round(min(quality_values), 4) if quality_values else 0.0,
        "mean": round(float(np.mean(quality_values)), 4) if quality_values else 0.0,
        "evidence_count": len(evidence),
    }
    independence_groups = tuple(
        sorted({item.independence_group for item in evidence if item.independence_group})
    )
    if total_weight == 0:
        result = DecisionResult(
            state="insufficient",
            confidence=0,
            rationale="当前没有足够且可追溯的正负证据，暂不形成方向性结论",
            supporting_evidence=(),
            opposing_evidence=(),
            unresolved_conflicts=(),
            conditions=("补充有效的基本面、技术面或独立情绪证据后再评估",),
            invalidation_conditions=(),
            evidence_quality=quality_summary,
            independence_groups=independence_groups,
        )
        return result.to_dict()

    net = (positive_weight - negative_weight) / total_weight
    conflict_ratio = min(positive_weight, negative_weight) / total_weight
    if conflict_ratio >= 0.3 and abs(net) < 0.25:
        state: DecisionState = "conflicted"
        rationale = "正反证据权重接近，冲突尚未被可靠证据裁决"
    elif net >= 0.25:
        state = "conditional_positive"
        rationale = "支持证据占优，但仍需满足条件并持续验证"
    elif net <= -0.25:
        state = "conditional_negative"
        rationale = "反向证据占优，当前不支持积极结论"
    else:
        state = "neutral"
        rationale = "证据方向不够集中，保持中性观察"
    confidence = int(
        round(
            min(80.0, abs(net) * 100.0 * min(1.0, total_weight / 1.5))
            * quality_summary["mean"]
        )
    )
    conflicts = tuple(item.claim for item in supporting + opposing) if state == "conflicted" else ()
    conditions = tuple(item.claim for item in opposing)
    invalidations = tuple(item.claim for item in supporting)
    result = DecisionResult(
        state=state,
        confidence=confidence,
        rationale=rationale,
        supporting_evidence=tuple(item.to_dict() for item in supporting),
        opposing_evidence=tuple(item.to_dict() for item in opposing),
        unresolved_conflicts=conflicts,
        conditions=conditions,
        invalidation_conditions=invalidations,
        evidence_quality=quality_summary,
        independence_groups=independence_groups,
    )
    return result.to_dict()
