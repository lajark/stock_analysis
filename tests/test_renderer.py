"""Regression test for structured change rendering."""

from src.reports.renderer import render_report


def test_renderer_displays_structured_changes(tmp_path) -> None:
    package = {
        "stock": {"name": "测试股票", "code": "600519.SH"},
        "meta": {
            "analysis_date": "2026-08-14",
            "generated_at": "2026-08-14T12:00:00",
            "data_provider": "tushare",
            "data_date": "2026-08-14",
        },
        "technical": {
            "close": 10,
            "trend": "上升",
            "macd_status": "金叉",
            "macd": 1,
            "rsi": 50,
            "rsi_status": "中性",
            "kdj_k": 50,
            "kdj_d": 50,
            "kdj_j": 50,
            "kdj_status": "中性",
        },
        "valuation": {"percentiles": {"price_percentile_1y": 50, "level": "中等"}},
        "fundamental": {"score": 60, "score_label": "一般"},
        "risk": {"risk_level": {"label": "中等风险", "score": 50}},
        "price_levels": {},
        "changes": {
            "changed_count": 1,
            "changes": [{"path": "technical.close", "before": 9, "after": 10}],
        },
    }

    output_path = tmp_path / "report.md"
    render_report(
        package,
        "结论",
        "test-model",
        {"input_tokens": 1, "output_tokens": 2},
        str(output_path),
    )

    report = output_path.read_text(encoding="utf-8")
    assert "与上次结构化分析的变化" in report
    assert "technical.close" in report
