"""基础财务报表比率推导测试。"""

from decimal import Decimal

import pandas as pd

from src.analysis.financial_ratios import (
    calculate_ratio,
    derive_ratio_results,
    summarize_derived_ratios,
)


def test_quarterly_roa_roe_use_average_balance_and_annualization() -> None:
    income = pd.DataFrame(
        {"end_date": ["2024-03-31"], "net_profit": [100]},
    )
    balance = pd.DataFrame(
        {
            "end_date": ["2023-12-31", "2024-03-31"],
            "total_assets": [1900, 2100],
            "shareholders_equity": [900, 1100],
        }
    )

    results = derive_ratio_results(income, balance)
    values = {result.metric: result.value for result in results}

    assert values["roa"] == Decimal("20")
    assert values["roe"] == Decimal("40")
    assert all(result.status == "calculated" for result in results)
    assert "annualization_factor" in results[0].inputs


def test_roe_prefers_attributable_profit_when_available() -> None:
    income = pd.DataFrame(
        {
            "end_date": ["2024-12-31"],
            "net_profit": [100],
            "net_profit_attributable": [80],
        }
    )
    balance = pd.DataFrame(
        {
            "end_date": ["2023-12-31", "2024-12-31"],
            "total_assets": [1900, 2100],
            "shareholders_equity": [900, 1100],
        }
    )

    values = {result.metric: result.value for result in derive_ratio_results(income, balance)}

    assert values["roa"] == Decimal("5")
    assert values["roe"] == Decimal("8")


def test_missing_opening_balance_is_not_replaced_by_current_value() -> None:
    income = pd.DataFrame({"end_date": ["2024-03-31"], "net_profit": [100]})
    balance = pd.DataFrame(
        {
            "end_date": ["2024-03-31"],
            "total_assets": [2100],
            "shareholders_equity": [1100],
        }
    )

    results = derive_ratio_results(income, balance)

    assert {result.status for result in results} == {"missing"}
    assert all("期初基础值" in result.warnings[0] for result in results)


def test_zero_denominator_has_explicit_status() -> None:
    result = calculate_ratio(
        metric="roe",
        period="2024-12-31",
        numerator=Decimal("10"),
        denominator=Decimal("0"),
        formula="net_profit / average_equity * 100",
        inputs={"net_profit": Decimal("10")},
    )

    assert result.value is None
    assert result.status == "division_by_zero"


def test_summary_keeps_provider_value_for_comparison() -> None:
    income = pd.DataFrame({"end_date": ["2024-12-31"], "net_profit": [100]})
    balance = pd.DataFrame(
        {
            "end_date": ["2023-12-31", "2024-12-31"],
            "total_assets": [1900, 2100],
            "shareholders_equity": [900, 1100],
        }
    )
    fina = pd.DataFrame({"end_date": ["2024-12-31"], "roa": [5.0], "roe": [10.0]})

    summary = summarize_derived_ratios(income, balance, fina)

    assert summary["status"] == "有数据"
    assert summary["metrics"]["roa"]["value"] == 5.0
    assert summary["metrics"]["roa"]["provider_value"] == 5.0
    assert summary["metrics"]["roe"]["provider_value"] == 10.0
