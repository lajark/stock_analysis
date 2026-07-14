"""基本面分析单元测试。"""

import pandas as pd
import pytest

from src.analysis.fundamentals import analyze_fundamentals


class TestFundamentals:
    def test_structure(self, sample_income, sample_balance, sample_cashflow, sample_fina_indicator):
        result = analyze_fundamentals(
            sample_income, sample_balance, sample_cashflow, sample_fina_indicator
        )
        assert "revenue_trend" in result
        assert "profitability" in result
        assert "profit_quality" in result
        assert "solvency" in result
        assert "growth" in result
        assert "score" in result
        assert "score_label" in result

    def test_score_range(self, sample_income, sample_balance, sample_cashflow, sample_fina_indicator):
        result = analyze_fundamentals(
            sample_income, sample_balance, sample_cashflow, sample_fina_indicator
        )
        assert 0 <= result["score"] <= 100

    def test_revenue_trend_growth(self, sample_income, sample_balance, sample_cashflow, sample_fina_indicator):
        result = analyze_fundamentals(
            sample_income, sample_balance, sample_cashflow, sample_fina_indicator
        )
        rev = result["revenue_trend"]
        assert rev["periods"] > 0
        assert "latest_revenue_yi" in rev

    def test_profitability(self, sample_income, sample_balance, sample_cashflow, sample_fina_indicator):
        result = analyze_fundamentals(
            sample_income, sample_balance, sample_cashflow, sample_fina_indicator
        )
        prof = result["profitability"]
        assert prof["roe"] > 0
        assert prof["gross_margin"] > 0
        assert prof["roe_level"] in ["优秀", "良好", "一般", "偏低", "亏损"]

    def test_empty_data(self):
        result = analyze_fundamentals(
            pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame()
        )
        assert result["revenue_trend"]["status"] == "无数据"
        assert result["profitability"]["status"] == "无数据"