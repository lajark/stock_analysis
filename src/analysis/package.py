"""结构化分析包构建 — 将各模块分析结果组装为精简 JSON。

这是传给 LLM 的唯一数据格式。不包含原始数据，仅包含计算后的摘要。
"""

import json
from collections.abc import Mapping
from datetime import datetime
from typing import Any, cast

import pandas as pd  # type: ignore[import-untyped]

from src.analysis.contracts import (
    DatasetDescriptor,
    DataSnapshot,
    EvidencePackage,
    QualityStatus,
    new_run_id,
)
from src.analysis.evidence_diff import compare_evidence_packages
from src.analysis.fundamentals import analyze_fundamentals
from src.analysis.indicators import calc_all_indicators, summarize_indicators
from src.analysis.price_levels import analyze_price_levels
from src.analysis.risk import analyze_risk
from src.analysis.scenarios import build_scenarios
from src.analysis.valuation import analyze_valuation


def _dataset_row_count(value: Any) -> int:
    """Return a meaningful row count for frames and one-row mappings."""
    if isinstance(value, dict):
        return 1 if value else 0
    try:
        return len(value)
    except TypeError:
        return 1 if value else 0


def _dataset_quality(
    name: str,
    value: Any,
    quality_map: Mapping[str, str],
) -> QualityStatus:
    candidate = quality_map.get(name)
    if candidate in {"ok", "partial", "stale", "invalid"}:
        return cast(QualityStatus, candidate)
    return "ok" if _dataset_row_count(value) > 0 else "partial"


def build_analysis_package(
    stock_info: dict[str, Any],
    daily: pd.DataFrame,
    daily_basic: dict[str, Any],
    income: pd.DataFrame,
    balance_sheet: pd.DataFrame,
    cashflow: pd.DataFrame,
    fina_indicator: pd.DataFrame,
    analysis_date: str,
    *,
    run_id: str | None = None,
    provider_name: str = "tushare",
    dataset_providers: Mapping[str, str] | None = None,
    dataset_quality: Mapping[str, str] | None = None,
    data_warnings: tuple[str, ...] | list[str] = (),
    validation: Mapping[str, Any] | None = None,
    previous_package: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """构建结构化分析包。

    所有计算在本地完成，LLM 仅消费此 JSON 生成报告。

    Args:
        stock_info: 股票基本信息
        daily: 日线行情
        daily_basic: 当日每日指标
        income: 利润表
        balance_sheet: 资产负债表
        cashflow: 现金流量表
        fina_indicator: 财务指标
        analysis_date: 分析日期
        dataset_providers: 数据集级来源；缺省时使用 provider_name
        dataset_quality: 数据集级质量状态；用于保留降级或过期标记
        data_warnings: 数据获取阶段产生的安全告警
        validation: LLM 调用前的确定性校验结果
        previous_package: 上一次同股票的结构化证据包，可选

    Returns:
        结构化分析包 JSON，大小通常 < 5KB。
    """
    # 计算技术指标
    daily_with_indicators = calc_all_indicators(daily)
    technical = summarize_indicators(daily_with_indicators)

    # 基本面
    fundamental = analyze_fundamentals(income, balance_sheet, cashflow, fina_indicator)

    # 估值
    valuation = analyze_valuation(daily, daily_basic, fina_indicator)

    # 风险
    risk = analyze_risk(daily)

    # 价格水平（支撑/阻力/目标价/置信度）
    price_levels = analyze_price_levels(daily_with_indicators)
    scenario_result = build_scenarios(
        technical=technical,
        valuation=valuation,
        risk=risk,
        price_levels=price_levels,
        validation=validation,
    )

    current_run_id = run_id or new_run_id()
    dataset_providers = dataset_providers or {}
    dataset_quality = dataset_quality or {}
    data_warnings = tuple(data_warnings)
    validation = dict(validation or {})
    data_date = daily["trade_date"].max().strftime("%Y-%m-%d") if not daily.empty else ""
    raw_datasets = {
        "daily": daily,
        "daily_basic": daily_basic,
        "income": income,
        "balance_sheet": balance_sheet,
        "cashflow": cashflow,
        "financial_indicators": fina_indicator,
    }
    descriptors = {
        name: DatasetDescriptor(
            name=name,
            provider=dataset_providers.get(name, provider_name),
            as_of=data_date,
            row_count=_dataset_row_count(value),
            quality=_dataset_quality(name, value, dataset_quality),
            adjustment="none" if name == "daily" else None,
        )
        for name, value in raw_datasets.items()
    }
    missing_datasets = tuple(
        name for name, descriptor in descriptors.items() if descriptor.quality != "ok"
    )
    snapshot = DataSnapshot(
        run_id=current_run_id,
        ticker=str(stock_info.get("code", "")),
        requested_date=analysis_date,
        effective_trade_date=data_date,
        stock={
            "code": stock_info.get("code", ""),
            "name": stock_info.get("name", ""),
            "industry": stock_info.get("industry", ""),
            "market": stock_info.get("market", ""),
        },
        datasets=descriptors,
        quality="partial" if missing_datasets else "ok",
        missing_datasets=missing_datasets,
        warnings=data_warnings,
    )

    # Keep the legacy flat shape and add versioned contract metadata beside it.
    package = {
        "meta": {
            "analysis_date": analysis_date,
            "generated_at": datetime.now().isoformat(),
            "data_provider": provider_name,
            "data_date": data_date,
        },
        "stock": {
            "code": stock_info.get("code", ""),
            "name": stock_info.get("name", ""),
            "industry": stock_info.get("industry", ""),
            "market": stock_info.get("market", ""),
        },
        "technical": technical,
        "fundamental": fundamental,
        "valuation": valuation,
        "risk": risk,
        "price_levels": price_levels,
        "scenarios": scenario_result["scenarios"],
        "scenario_method_version": scenario_result["method_version"],
        "invalidation_conditions": scenario_result["invalidation_conditions"],
        "validation": validation,
    }
    evidence_package = EvidencePackage.from_legacy(
        package,
        run_id=current_run_id,
        snapshot_ref=snapshot.reference(),
        quality=snapshot.quality,
        data_gaps=missing_datasets,
        data_warnings=data_warnings,
    ).to_dict()
    if previous_package is not None:
        evidence_package["changes"] = compare_evidence_packages(
            previous_package,
            evidence_package,
        )
    return evidence_package


def package_to_json(package: dict[str, Any]) -> str:
    """将分析包序列化为 JSON 字符串。"""
    return json.dumps(package, ensure_ascii=False, indent=2)


def package_size(package: dict[str, Any]) -> int:
    """估算分析包大小（字节），用于成本控制。"""
    return len(package_to_json(package).encode("utf-8"))
