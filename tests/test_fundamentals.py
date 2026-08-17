"""基本面分析单元测试。"""

import pandas as pd

from src.analysis.fundamentals import analyze_fundamentals


class TestFundamentals:
    def test_structure(self, sample_income, sample_balance, sample_cashflow, sample_fina_indicator):
        result = analyze_fundamentals(
            sample_income, sample_balance, sample_cashflow, sample_fina_indicator
        )
        assert "revenue_trend" in result
        assert "profitability" in result
        assert "derived_ratios" in result
        assert "profit_quality" in result
        assert "solvency" in result
        assert "growth" in result
        assert "score" in result
        assert "score_label" in result

    def test_score_range(
        self, sample_income, sample_balance, sample_cashflow, sample_fina_indicator
    ):
        result = analyze_fundamentals(
            sample_income, sample_balance, sample_cashflow, sample_fina_indicator
        )
        assert 0 <= result["score"] <= 100

    def test_revenue_trend_growth(
        self, sample_income, sample_balance, sample_cashflow, sample_fina_indicator
    ):
        result = analyze_fundamentals(
            sample_income, sample_balance, sample_cashflow, sample_fina_indicator
        )
        rev = result["revenue_trend"]
        assert rev["periods"] > 0
        assert "latest_revenue_yi" in rev

    def test_profitability(
        self, sample_income, sample_balance, sample_cashflow, sample_fina_indicator
    ):
        result = analyze_fundamentals(
            sample_income, sample_balance, sample_cashflow, sample_fina_indicator
        )
        prof = result["profitability"]
        assert prof["roe"] > 0
        assert prof["roe_source"] == "基础报表本地推导"
        assert prof["provider_roe"] == 24.0
        assert prof["gross_margin"] > 0
        assert prof["roe_level"] in ["优秀", "良好", "一般", "偏低", "亏损"]

    def test_profitability_falls_back_to_provider_when_derived_roe_is_missing(
        self, sample_income, sample_cashflow, sample_fina_indicator
    ):
        result = analyze_fundamentals(
            sample_income,
            pd.DataFrame(),
            sample_cashflow,
            sample_fina_indicator,
        )

        prof = result["profitability"]
        assert prof["roe"] == 24.0
        assert prof["roe_source"] == "fina_indicator.roe"
        assert prof["roe_comparison_status"] == "未推导"

    def test_empty_data(self):
        result = analyze_fundamentals(
            pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame()
        )
        assert result["revenue_trend"]["status"] == "无数据"
        assert result["profitability"]["status"] == "无数据"
        assert result["score_status"] == "无可评分证据"
        assert result["score_label"] == "无法判断"

    def test_profit_quality_uses_common_reporting_period(self):
        income = pd.DataFrame(
            {
                "end_date": pd.to_datetime(["2024-12-31", "2025-12-31"]),
                "net_profit": [100.0, 200.0],
            }
        )
        cashflow = pd.DataFrame(
            {
                "end_date": pd.to_datetime(["2024-12-31", "2026-12-31"]),
                "operating_cf": [120.0, 999.0],
            }
        )
        result = analyze_fundamentals(income, pd.DataFrame(), cashflow, pd.DataFrame())
        assert result["profit_quality"]["period"] == "2024-12-31"
        assert result["profit_quality"]["cf_to_profit_ratio"] == 1.2
