"""多维证据、价量情绪代理和冲突裁决测试。"""

import pandas as pd

from src.analysis.evidence_decision import (
    analyze_market_sentiment,
    build_investment_decision,
    normalize_external_evidence,
    normalize_official_event_evidence,
)


def _daily() -> pd.DataFrame:
    dates = pd.date_range("2025-01-01", periods=30, freq="B")
    close = [100 + index * 0.5 for index in range(30)]
    return pd.DataFrame(
        {
            "trade_date": dates,
            "close": close,
            "volume": [1000] * 29 + [1800],
        }
    )


def test_sentiment_proxy_is_explicit_and_has_provenance() -> None:
    result = analyze_market_sentiment(_daily())

    assert result["status"] == "ok"
    assert result["source"] == "price_volume_proxy-v1"
    assert result["as_of"] == "2025-02-11"
    assert result["evidence"]
    assert result["evidence"][0]["independence_group"] == "price_volume_proxy"
    assert result["evidence"][0]["quality"] == 0.55


def test_sentiment_adds_independent_moneyflow_evidence_without_replacing_proxy() -> None:
    moneyflow = pd.DataFrame(
        {
            "trade_date": pd.date_range("2025-02-05", periods=5, freq="B"),
            "net_mf_amount": [10, 20, 15, 5, 30],
        }
    )

    result = analyze_market_sentiment(_daily(), moneyflow)

    assert result["source"] == "price_volume_proxy-v1"
    assert result["independent_source"] == "tushare_moneyflow-v1"
    assert "tushare_moneyflow-v1" in result["sources"]
    assert any(item["dimension"] == "market_behavior" for item in result["evidence"])


def test_external_evidence_requires_source_and_rejects_future_rows() -> None:
    records = [
        {
            "evidence_id": "news.positive",
            "dimension": "sentiment",
            "polarity": "positive",
            "claim": "公司披露订单增长",
            "source": "licensed_news-v1",
            "as_of": "2025-02-10",
            "strength": 0.6,
            "reliability": 0.7,
            "quality": 0.8,
        },
        {
            "evidence_id": "news.future",
            "dimension": "sentiment",
            "polarity": "negative",
            "claim": "未来日期内容不应进入历史分析",
            "source": "licensed_news-v1",
            "as_of": "2025-02-12",
            "strength": 0.8,
            "reliability": 0.8,
        },
    ]

    accepted, summary = normalize_external_evidence(records, as_of="2025-02-11")

    assert [item["evidence_id"] for item in accepted] == ["news.positive"]
    assert summary["accepted_count"] == 1
    assert summary["rejected_count"] == 1
    assert summary["coverage_start"] == "2025-02-10"
    assert summary["coverage_end"] == "2025-02-10"
    assert summary["freshness_days"] == 1
    assert summary["source_count"] == 1

    result = analyze_market_sentiment(_daily(), external_evidence=records)
    assert result["external"]["accepted_count"] == 1
    assert result["external"]["freshness_days"] == 1
    assert result["independent_source"] == "licensed_news-v1"
    assert len(result["evidence"]) >= 2


def test_official_events_use_fixed_taxonomy_and_keep_source_reference() -> None:
    records = [
        {
            "item_id": "a1",
            "source": "cninfo",
            "published_at": "2025-02-10",
            "event_type": "业绩预增",
            "title": "年度业绩预告",
            "source_url": "https://www.cninfo.com.cn/a1",
        },
        {
            "item_id": "a2",
            "source": "sse",
            "published_at": "2025-02-10",
            "event_type": "无法判断的公告类型",
            "title": "股东大会通知",
        },
        {
            "item_id": "future",
            "source": "szse",
            "published_at": "2025-02-12",
            "event_type": "风险提示",
            "title": "未来公告",
        },
    ]

    accepted, summary = normalize_official_event_evidence(
        records,
        as_of="2025-02-11",
    )

    assert [item["polarity"] for item in accepted] == ["positive", "neutral"]
    assert accepted[0]["source_ref"].endswith("/a1")
    assert accepted[0]["method_version"] == "official-disclosure-events-v1"
    assert summary["accepted_count"] == 2
    assert summary["rejected_count"] == 1

    result = analyze_market_sentiment(_daily(), official_event_records=records)
    assert result["official_events"]["accepted_count"] == 2
    assert "cninfo" in result["official_events"]["sources"]


def test_decision_stays_insufficient_without_evidence() -> None:
    result = build_investment_decision(
        {"score": 50, "score_status": "无可评分证据"},
        {},
        {"status": "insufficient", "evidence": []},
        as_of="2025-02-11",
    )

    assert result["state"] == "insufficient"
    assert result["confidence"] == 0
    assert result["conditions"]


def test_decision_marks_close_positive_negative_evidence_as_conflicted() -> None:
    result = build_investment_decision(
        {"score": 80, "score_status": "有证据"},
        {"trend": "下降", "macd_status": "死叉"},
        {"evidence": []},
        as_of="2025-02-11",
    )

    assert result["state"] == "conflicted"
    assert result["unresolved_conflicts"]
    assert result["supporting_evidence"]
    assert result["opposing_evidence"]
    assert result["evidence_quality"]["mean"] < 1.0
    assert "fundamental_local" in result["independence_groups"]
