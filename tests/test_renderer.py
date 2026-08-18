"""Regression test for structured change rendering."""

from src.reports.renderer import render_report


def _base_package() -> dict:
    return {
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
        "changes": {"changed_count": 0, "changes": []},
    }


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
            "changed_count": 2,
            "changes": [
                {"path": "technical.close", "before": 9, "after": 10},
                {"path": "decision.supporting_evidence", "before": "x" * 500, "after": "y" * 500},
            ],
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
    # Oversized change values must be truncated so the table stays readable.
    assert "x" * 500 not in report
    assert "y" * 500 not in report


def test_price_levels_table_has_no_blank_rows_between_header_and_body(
    tmp_path,
) -> None:
    """Regression: Jinja for-loop blank lines used to split the Markdown table."""
    package = _base_package()
    package["price_levels"] = {
        "supports": [
            {"type": "MA60", "price": 10, "distance_pct": -2, "strength": "中"},
            {"type": "前低", "price": 9, "distance_pct": -3, "strength": "强"},
        ],
        "resistances": [{"type": "MA20", "price": 12, "distance_pct": 3, "strength": "中"}],
        "confidence": {
            "buy_confidence": 60,
            "buy_label": "中",
            "sell_confidence": 40,
            "sell_label": "低",
        },
        "targets": {
            "buy_targets": [{"price": 8, "reason": "支撑位", "confidence": "中"}],
            "sell_targets": [{"price": 14, "reason": "压力位", "confidence": "低"}],
        },
    }
    package["changes"] = {
        "changed_count": 1,
        "changes": [{"path": "technical.rsi", "before": 40, "after": 50}],
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

    # 关键价位表：表头、分隔行、支撑/阻力行之间不得有空行
    table_block = report.split("## 关键价位")[1].split("### 交易信号")[0]
    raw_lines = [line for line in table_block.splitlines() if line.strip()]
    assert raw_lines[0].startswith("| 类型")
    assert raw_lines[1].startswith("|------")
    assert raw_lines[2].startswith("| 支撑: MA60")
    assert raw_lines[3].startswith("| 支撑: 前低")
    assert raw_lines[4].startswith("| 阻力: MA20")
    assert len(raw_lines) == 5, "表格体内部出现多余空行导致割裂"

    # 目标价列表项必须连续（无空行分割）
    assert "\n- 8 (支撑位" in report
    assert "\n- 14 (压力位" in report
