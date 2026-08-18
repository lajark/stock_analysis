"""价格水平分析单元测试。"""

import pandas as pd
import pytest

from src.analysis.indicators import calc_all_indicators
from src.analysis.price_levels import analyze_price_levels


class TestPriceLevels:
    @pytest.fixture
    def daily_with_indicators(self, sample_ohlc):
        return calc_all_indicators(sample_ohlc)

    def test_structure(self, daily_with_indicators):
        result = analyze_price_levels(daily_with_indicators)
        assert "current_price" in result
        assert "trend" in result
        assert "supports" in result
        assert "resistances" in result
        assert "targets" in result
        assert "confidence" in result

    def test_current_price(self, daily_with_indicators):
        result = analyze_price_levels(daily_with_indicators)
        expected = round(float(daily_with_indicators["close"].iloc[-1]), 2)
        assert result["current_price"] == expected

    def test_supports_below_current(self, daily_with_indicators):
        result = analyze_price_levels(daily_with_indicators)
        current = result["current_price"]
        for s in result["supports"]:
            assert s["price"] < current, (
                f"支撑 {s['type']} 价格 {s['price']} 应低于当前价 {current}"
            )

    def test_resistances_above_current(self, daily_with_indicators):
        result = analyze_price_levels(daily_with_indicators)
        current = result["current_price"]
        for r in result["resistances"]:
            assert r["price"] > current, (
                f"阻力 {r['type']} 价格 {r['price']} 应高于当前价 {current}"
            )

    def test_confidence_range(self, daily_with_indicators):
        result = analyze_price_levels(daily_with_indicators)
        conf = result["confidence"]
        assert 0 <= conf["buy_confidence"] <= 100
        assert 0 <= conf["sell_confidence"] <= 100
        assert conf["buy_label"] in ["高", "中", "低", "极低"]
        assert conf["sell_label"] in ["高", "中", "低", "极低"]

    def test_trend_structure(self, daily_with_indicators):
        result = analyze_price_levels(daily_with_indicators)
        trend = result["trend"]
        assert "direction" in trend
        assert "alignment" in trend
        assert "strength_score" in trend
        assert "strength_label" in trend
        assert 0 <= trend["strength_score"] <= 100

    def test_targets_have_horizon(self, daily_with_indicators):
        result = analyze_price_levels(daily_with_indicators)
        assert result["targets"]["horizon"] == "3-6个月"
        assert len(result["targets"]["buy_targets"]) > 0
        assert len(result["targets"]["sell_targets"]) > 0

    def test_empty_df(self):
        result = analyze_price_levels(pd.DataFrame())
        assert result == {"status": "无数据"}

    def test_downtrend_support_resistance(self, sample_ohlc_downtrend):
        df = calc_all_indicators(sample_ohlc_downtrend)
        result = analyze_price_levels(df)
        # 下降趋势中应该有明确的阻力位
        assert len(result["resistances"]) > 0
        assert result["trend"]["direction"] in ["上升", "下降", "震荡"]
