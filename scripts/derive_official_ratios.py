"""Derive ROA/ROE from the extracted official-report base fields.

The script intentionally does not read provider ROA/ROE values.  It uses each
security's prior year-end and report-date balances plus the report-period
profit, annualized for quarterly YTD reports.  Missing opening balances remain
missing instead of being replaced by the current balance.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

FORMULA_VERSION = "average_balance_v1_official_pdf"
ANNUALIZATION = {3: 4.0, 6: 2.0, 9: 4.0 / 3.0, 12: 1.0}


def _number(value: object) -> float | None:
    if value is None or pd.isna(value):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if pd.notna(number) else None


def derive_ratios(input_path: Path, output_dir: Path) -> dict[str, object]:
    frame = pd.read_csv(input_path)
    required = {
        "ts_code",
        "period_end",
        "net_profit",
        "net_profit_attributable",
        "total_assets",
        "shareholders_equity",
    }
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"输入缺少字段：{', '.join(sorted(missing))}")

    frame["period_end"] = pd.to_datetime(frame["period_end"], errors="coerce")
    frame = frame.loc[frame["period_end"].notna()].copy()
    frame = frame.sort_values(["ts_code", "period_end"]).reset_index(drop=True)
    lookup = frame.set_index(["ts_code", "period_end"])
    rows: list[dict[str, object]] = []
    for _, row in frame.iterrows():
        period = row["period_end"]
        opening_period = pd.Timestamp(year=period.year - 1, month=12, day=31)
        opening_key = (row["ts_code"], opening_period)
        opening = lookup.loc[opening_key] if opening_key in lookup.index else None
        net_profit = _number(row["net_profit"])
        attributable = _number(row["net_profit_attributable"])
        ending_assets = _number(row["total_assets"])
        ending_equity = _number(row["shareholders_equity"])
        opening_assets = _number(opening["total_assets"]) if opening is not None else None
        opening_equity = _number(opening["shareholders_equity"]) if opening is not None else None
        average_assets = (
            (opening_assets + ending_assets) / 2
            if opening_assets is not None and ending_assets is not None
            else None
        )
        average_equity = (
            (opening_equity + ending_equity) / 2
            if opening_equity is not None and ending_equity is not None
            else None
        )
        factor = ANNUALIZATION.get(period.month, 1.0)
        derived_roa = (
            net_profit * factor / average_assets * 100
            if net_profit is not None and average_assets
            else None
        )
        derived_roe = (
            attributable * factor / average_equity * 100
            if attributable is not None and average_equity
            else None
        )
        missing_fields = []
        if derived_roa is None:
            missing_fields.append("roa")
        if derived_roe is None:
            missing_fields.append("roe")
        rows.append(
            {
                "ts_code": row["ts_code"],
                "period_end": period.strftime("%Y-%m-%d"),
                "report_type": row.get("report_type", ""),
                "annualization_factor": factor,
                "net_profit": net_profit,
                "net_profit_attributable": attributable,
                "opening_assets": opening_assets,
                "ending_assets": ending_assets,
                "average_assets": average_assets,
                "derived_roa": derived_roa,
                "opening_equity": opening_equity,
                "ending_equity": ending_equity,
                "average_equity": average_equity,
                "derived_roe": derived_roe,
                "formula_version": FORMULA_VERSION,
                "status": "calculated" if not missing_fields else "partial",
                "missing_fields": ",".join(missing_fields),
            }
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / "official-ratios.csv"
    pd.DataFrame(rows).to_csv(output, index=False, encoding="utf-8-sig")
    result = {
        "source": "cninfo_pdf",
        "input": str(input_path.resolve()),
        "output": str(output.resolve()),
        "formula_version": FORMULA_VERSION,
        "row_count": len(rows),
        "status_counts": pd.Series([row["status"] for row in rows]).value_counts().to_dict(),
        "coverage": {
            "derived_roa": sum(row["derived_roa"] is not None for row in rows),
            "derived_roe": sum(row["derived_roe"] is not None for row in rows),
        },
        "note": "按 ts_code 分组；期初取上一年年末，季度报告按 YTD 利润年化；缺失不补齐。",
    }
    (output_dir / "ratios_manifest.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="从官方 PDF 基础字段推导 ROA/ROE")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(derive_ratios(args.input, args.output_dir), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
