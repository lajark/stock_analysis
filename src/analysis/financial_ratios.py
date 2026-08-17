"""基于基础财务报表的可审计财务比率计算。

供应商的 ``fina_indicator`` 只作为对照证据，不作为 ROA/ROE 的唯一来源。
本模块参考 DD Workbench 中“Decimal + 显式公式 + 状态/警告”的实现原则，
但不依赖外部项目代码。输入必须是已按报告期规范化的利润表和资产负债表。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

import pandas as pd

RatioStatus = Literal["calculated", "missing", "division_by_zero"]

_ROA_PROFIT_COLUMNS = ("net_profit", "n_income")
_ROE_PROFIT_COLUMNS = (
    "net_profit_attributable",
    "n_income_attr_p",
    "net_profit",
    "n_income",
)
_ASSET_COLUMNS = ("total_assets",)
_EQUITY_COLUMNS = ("shareholders_equity",)
_PROVIDER_COLUMNS = {"roa": "roa", "roe": "roe"}


@dataclass(frozen=True)
class RatioResult:
    """单个报告期的比率计算结果，保留公式和输入证据。"""

    metric: str
    period: str
    value: Decimal | None
    formula: str
    inputs: dict[str, Decimal | None]
    status: RatioStatus = "calculated"
    unit: str = "%"
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """转换为可直接放入分析包的 JSON 兼容结构。"""
        return {
            "metric": self.metric,
            "period": self.period,
            "value": float(self.value) if self.value is not None else None,
            "unit": self.unit,
            "formula": self.formula,
            "inputs": {
                key: float(value) if value is not None else None
                for key, value in self.inputs.items()
            },
            "status": self.status,
            "warnings": list(self.warnings),
        }


def _decimal(value: Any) -> Decimal | None:
    """将供应商数值转换为 Decimal；NaN、空值和非法值视为缺失。"""
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, str) and not value.strip():
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return result if result.is_finite() else None


def _column_value(row: pd.Series, candidates: tuple[str, ...]) -> tuple[str | None, Decimal | None]:
    for column in candidates:
        if column in row.index:
            value = _decimal(row[column])
            if value is not None:
                return column, value
    return None, None


def _clean_frame(frame: pd.DataFrame, columns: tuple[str, ...]) -> pd.DataFrame:
    """清理日期并保留每个报告期最后一行，兼容未经过网关规范化的调用。"""
    if frame is None or frame.empty or "end_date" not in frame.columns:
        return pd.DataFrame()
    result = frame.copy()
    result["end_date"] = pd.to_datetime(result["end_date"], errors="coerce")
    result = result.loc[result["end_date"].notna()].copy()
    if result.empty:
        return result
    for column in columns:
        if column in result.columns:
            result[column] = pd.to_numeric(result[column], errors="coerce")
    # 网关通常已经完成修订去重；这里仍按公告日和 update_flag 保证独立调用确定。
    sort_columns = ["end_date"]
    temporary_columns: list[str] = []
    announcement_columns = [
        column for column in ("f_ann_date", "ann_date") if column in result.columns
    ]
    if announcement_columns:
        result["_ratio_announcement"] = pd.NaT
        for column in announcement_columns:
            result["_ratio_announcement"] = result["_ratio_announcement"].fillna(
                pd.to_datetime(result[column], errors="coerce")
            )
        sort_columns.append("_ratio_announcement")
        temporary_columns.append("_ratio_announcement")
    if "update_flag" in result.columns:
        result["_ratio_update_rank"] = (
            result["update_flag"].astype("string").eq("1").astype(int)
        )
        sort_columns.append("_ratio_update_rank")
        temporary_columns.append("_ratio_update_rank")
    return (
        result.sort_values(sort_columns, kind="mergesort", na_position="first")
        .drop_duplicates(subset=["end_date"], keep="last")
        .drop(columns=temporary_columns, errors="ignore")
        .reset_index(drop=True)
    )


def _opening_period(period: pd.Timestamp) -> pd.Timestamp:
    """取报告期所属财年的上年年末作为平均资产/权益的期初。"""
    return pd.Timestamp(year=period.year - 1, month=12, day=31)


def _annualization_factor(period: pd.Timestamp, annualize: bool) -> Decimal:
    if not annualize:
        return Decimal("1")
    # 利润表按 YTD 累计口径取数时，季度利润需要年化。
    return {
        3: Decimal("4"),
        6: Decimal("2"),
        9: Decimal("1.333333333333333333333333333"),
        12: Decimal("1"),
    }.get(period.month, Decimal("1"))


def calculate_ratio(
    *,
    metric: str,
    period: str,
    numerator: Decimal | None,
    denominator: Decimal | None,
    formula: str,
    inputs: dict[str, Decimal | None],
    scale: Decimal = Decimal("100"),
    unit: str = "%",
) -> RatioResult:
    """按统一规则计算比率，显式区分缺失与除零。"""
    if numerator is None or denominator is None:
        return RatioResult(
            metric=metric,
            period=period,
            value=None,
            formula=formula,
            inputs=inputs,
            status="missing",
            unit=unit,
            warnings=("计算所需基础字段缺失",),
        )
    if denominator == 0:
        return RatioResult(
            metric=metric,
            period=period,
            value=None,
            formula=formula,
            inputs=inputs,
            status="division_by_zero",
            unit=unit,
            warnings=("分母为 0，未生成比率",),
        )
    return RatioResult(
        metric=metric,
        period=period,
        value=numerator / denominator * scale,
        formula=formula,
        inputs=inputs,
        status="calculated",
        unit=unit,
    )


def derive_ratio_results(
    income: pd.DataFrame,
    balance_sheet: pd.DataFrame,
    *,
    annualize: bool = True,
) -> list[RatioResult]:
    """从基础报表推导 ROA、ROE。

    ROA 使用总净利润，ROE 在有字段时使用归母净利润；二者均为
    ``年化利润 / 平均期初期末资产（或权益） * 100``。期初取报告期所属
    财年的上年年末；缺少期初证据时不使用当前期余额替代。
    """
    income_frame = _clean_frame(
        income,
        _ROA_PROFIT_COLUMNS + _ROE_PROFIT_COLUMNS,
    )
    balance_frame = _clean_frame(
        balance_sheet,
        _ASSET_COLUMNS + _EQUITY_COLUMNS,
    )
    if income_frame.empty:
        return []

    balance_by_date = balance_frame.set_index("end_date") if not balance_frame.empty else None
    results: list[RatioResult] = []
    for _, income_row in income_frame.iterrows():
        period_timestamp = pd.Timestamp(income_row["end_date"])
        period = period_timestamp.strftime("%Y-%m-%d")
        opening_date = _opening_period(period_timestamp)
        opening_row = (
            balance_by_date.loc[opening_date]
            if balance_by_date is not None and opening_date in balance_by_date.index
            else None
        )
        ending_row = (
            balance_by_date.loc[period_timestamp]
            if balance_by_date is not None and period_timestamp in balance_by_date.index
            else None
        )
        annualization = _annualization_factor(period_timestamp, annualize)

        for metric, columns, label, profit_columns in (
            ("roa", _ASSET_COLUMNS, "平均总资产", _ROA_PROFIT_COLUMNS),
            ("roe", _EQUITY_COLUMNS, "平均股东权益", _ROE_PROFIT_COLUMNS),
        ):
            net_profit_column, net_profit = _column_value(income_row, profit_columns)
            annualized_profit = (
                net_profit * annualization if net_profit is not None else None
            )
            opening_column, opening_value = (
                _column_value(opening_row, columns) if opening_row is not None else (None, None)
            )
            ending_column, ending_value = (
                _column_value(ending_row, columns) if ending_row is not None else (None, None)
            )
            average_value = (
                (opening_value + ending_value) / Decimal("2")
                if opening_value is not None and ending_value is not None
                else None
            )
            formula = (
                f"{net_profit_column or 'net_profit'} * {annualization} / "
                f"(({opening_column or label}期初 + {ending_column or label}期末) / 2) * 100"
            )
            result = calculate_ratio(
                metric=metric,
                period=period,
                numerator=annualized_profit,
                denominator=average_value,
                formula=formula,
                inputs={
                    "net_profit": net_profit,
                    "annualization_factor": annualization,
                    "opening_value": opening_value,
                    "ending_value": ending_value,
                    "average_value": average_value,
                },
            )
            if result.status == "missing":
                missing = []
                if net_profit is None:
                    missing.append("净利润")
                if opening_value is None:
                    missing.append("期初基础值")
                if ending_value is None:
                    missing.append("期末基础值")
                result = replace(result, warnings=("缺少" + "、".join(missing),))
            results.append(result)
    return results


def _latest_provider_value(
    fina_indicator: pd.DataFrame, metric: str, period: str
) -> Decimal | None:
    if fina_indicator is None or fina_indicator.empty or "end_date" not in fina_indicator.columns:
        return None
    frame = _clean_frame(fina_indicator, (metric,))
    if frame.empty:
        return None
    frame["end_date"] = pd.to_datetime(frame["end_date"], errors="coerce")
    frame = frame.loc[frame["end_date"].dt.strftime("%Y-%m-%d") == period]
    if frame.empty:
        return None
    return _decimal(frame.iloc[-1].get(_PROVIDER_COLUMNS[metric]))


def summarize_derived_ratios(
    income: pd.DataFrame,
    balance_sheet: pd.DataFrame,
    fina_indicator: pd.DataFrame,
    *,
    annualize: bool = True,
) -> dict[str, Any]:
    """返回最新可用推导值，并附供应商对照和差异信息。"""
    results = derive_ratio_results(income, balance_sheet, annualize=annualize)
    if not results:
        return {
            "status": "无数据",
            "method": "基础财务报表本地推导",
            "formula_version": "average_balance_v1",
            "metrics": {},
        }
    latest_period = max(result.period for result in results)
    metrics: dict[str, Any] = {}
    for metric in ("roa", "roe"):
        candidates = [
            result
            for result in results
            if result.metric == metric and result.period == latest_period
        ]
        if not candidates:
            continue
        derived = candidates[-1]
        item = derived.to_dict()
        provider_value = _latest_provider_value(fina_indicator, metric, latest_period)
        item["provider_value"] = float(provider_value) if provider_value is not None else None
        if derived.value is not None and provider_value is not None:
            difference = derived.value - provider_value
            item["difference_pct_points"] = float(difference)
            tolerance = max(Decimal("0.1"), abs(provider_value) * Decimal("0.05"))
            item["comparison_status"] = (
                "接近" if abs(difference) <= tolerance else "口径差异"
            )
        else:
            item["difference_pct_points"] = None
            item["comparison_status"] = (
                "供应商缺失" if derived.value is not None else derived.status
            )
        metrics[metric] = item
    return {
        "status": (
            "有数据"
            if any(item.get("status") == "calculated" for item in metrics.values())
            else "数据不足"
        ),
        "period": latest_period,
        "method": "基础财务报表本地推导",
        "definition": "ROA=年化总净利润/平均总资产；ROE=年化归母净利润/平均股东权益；均×100",
        "formula_version": "average_balance_v1",
        "metrics": metrics,
    }
