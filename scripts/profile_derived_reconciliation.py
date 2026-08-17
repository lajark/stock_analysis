"""对已分流的派生指标差异做公式与口径归因。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd


def _latest_fina_rows(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path, dtype={"ts_code": str, "end_date": str})
    required = {"ts_code", "end_date", "ann_date"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"Tushare fina_indicator 缺少字段：{', '.join(sorted(missing))}")
    frame["ann_date"] = pd.to_datetime(frame["ann_date"], errors="coerce")
    return (
        frame.sort_values("ann_date")
        .drop_duplicates(["ts_code", "end_date"], keep="last")
        .rename(columns={"end_date": "period_end"})
    )


def profile_derived_reconciliation(
    audit_path: Path,
    ratios_path: Path,
    fina_path: Path,
    output_dir: Path,
    *,
    tolerance: float = 0.2,
) -> dict[str, object]:
    """输出 ROE/EPS 差异的公式匹配、缺失和口径复核分类。"""
    audit = pd.read_csv(audit_path)
    required_audit = {"dataset", "ts_code", "period_end", "field", "left_value", "right_value"}
    missing = required_audit - set(audit.columns)
    if missing:
        raise ValueError(f"审计队列缺少字段：{', '.join(sorted(missing))}")
    audit = audit.loc[audit["dataset"].eq("fina_indicator")].copy()

    ratios = pd.read_csv(ratios_path)
    ratio_columns = [
        "ts_code",
        "period_end",
        "annualization_factor",
        "derived_roa",
        "derived_roe",
        "formula_version",
    ]
    missing = set(ratio_columns) - set(ratios.columns)
    if missing:
        raise ValueError(f"官方公式快照缺少字段：{', '.join(sorted(missing))}")
    ratios = ratios[ratio_columns]

    fina = _latest_fina_rows(fina_path)
    provider_columns = [
        column
        for column in ("eps", "dt_eps", "roe", "roe_yearly", "roa", "roa_yearly")
        if column in fina.columns
    ]
    merged = audit.merge(ratios, on=["ts_code", "period_end"], how="left").merge(
        fina[["ts_code", "period_end", *provider_columns]],
        on=["ts_code", "period_end"],
        how="left",
        suffixes=("", "_provider"),
    )
    rows: list[dict[str, object]] = []
    for record in merged.to_dict(orient="records"):
        field = str(record["field"])
        formula_field = f"derived_{field}" if field in {"roa", "roe"} else ""
        provider_annualized_field = f"{field}_yearly" if field in {"roa", "roe"} else ""
        formula_value = record.get(formula_field) if formula_field else None
        provider_annualized = (
            record.get(provider_annualized_field) if provider_annualized_field else None
        )
        formula_value = pd.to_numeric(pd.Series([formula_value]), errors="coerce").iloc[0]
        provider_annualized = pd.to_numeric(
            pd.Series([provider_annualized]), errors="coerce"
        ).iloc[0]
        factor = pd.to_numeric(
            pd.Series([record.get("annualization_factor")]), errors="coerce"
        ).iloc[0]
        raw_formula_value = (
            float(formula_value) / float(factor)
            if not pd.isna(formula_value) and not pd.isna(factor) and factor
            else None
        )
        right_value = pd.to_numeric(
            pd.Series([record.get("right_value")]), errors="coerce"
        ).iloc[0]
        if field in {"roa", "roe"}:
            if pd.isna(formula_value):
                resolution = "unresolved_missing_formula"
            elif pd.isna(provider_annualized):
                resolution = "unresolved_provider_missing"
            elif abs(float(formula_value) - float(provider_annualized)) <= tolerance:
                resolution = "resolved_formula_matches_annualized_provider"
            elif (
                raw_formula_value is not None
                and not pd.isna(right_value)
                and abs(raw_formula_value - float(right_value)) <= 1.0
            ):
                resolution = "definition_review_nonannualized_candidate"
            else:
                resolution = "manual_definition_review"
        elif field == "eps":
            resolution = "manual_eps_share_count_review"
        else:
            resolution = "manual_review"
        rows.append(
            {
                "dataset": record["dataset"],
                "ts_code": record["ts_code"],
                "period_end": record["period_end"],
                "field": field,
                "left_value": record.get("left_value"),
                "right_value": record.get("right_value"),
                "formula_value": None if pd.isna(formula_value) else float(formula_value),
                "raw_formula_value": raw_formula_value,
                "raw_abs_error_to_official": (
                    None
                    if raw_formula_value is None or pd.isna(right_value)
                    else abs(raw_formula_value - float(right_value))
                ),
                "provider_annualized_value": (
                    None if pd.isna(provider_annualized) else float(provider_annualized)
                ),
                "formula_abs_error_to_provider": (
                    None
                    if pd.isna(formula_value) or pd.isna(provider_annualized)
                    else abs(float(formula_value) - float(provider_annualized))
                ),
                "formula_version": record.get("formula_version", ""),
                "resolution": resolution,
            }
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    output = pd.DataFrame(rows)
    output.to_csv(output_dir / "derived-resolution.csv", index=False, encoding="utf-8-sig")
    counts = output["resolution"].value_counts().to_dict() if not output.empty else {}
    result = {
        "audit": str(audit_path.resolve()),
        "ratios": str(ratios_path.resolve()),
        "fina_indicator": str(fina_path.resolve()),
        "output": str((output_dir / "derived-resolution.csv").resolve()),
        "tolerance": tolerance,
        "row_count": len(output),
        "resolution_counts": {str(key): int(value) for key, value in counts.items()},
        "note": (
            "ROA/ROE 先比较年化公式与供应商对应的 *_yearly；若官方报告更接近未年化公式，"
            "仅标记为口径候选，不自动替换。EPS 保留股本/口径人工复核。"
        ),
    }
    (output_dir / "derived-resolution.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="归因官方公式与供应商派生指标差异")
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--ratios", type=Path, required=True)
    parser.add_argument("--fina-indicator", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--tolerance", type=float, default=0.2)
    args = parser.parse_args()
    print(
        json.dumps(
            profile_derived_reconciliation(
                args.audit,
                args.ratios,
                args.fina_indicator,
                args.output_dir,
                tolerance=args.tolerance,
            ),
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
