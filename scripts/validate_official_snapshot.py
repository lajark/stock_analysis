"""Validate an official-PDF snapshot before it is promoted as an audit baseline."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pandas as pd

DATASETS = ("income", "balance_sheet", "cashflow", "fina_indicator")
KEYS = ["ts_code", "period_end"]
NORMALIZED_FIELDS = {
    "income": {
        "revenue": "revenue",
        "operate_profit": "operating_profit",
        "n_income": "net_profit",
        "n_income_attr_p": "net_profit_attributable",
        "basic_eps": "eps",
    },
    "balance_sheet": {
        "total_assets": "total_assets",
        "total_liab": "total_liabilities",
        "total_hldr_eqy_exc_min_int": "shareholders_equity",
    },
    "cashflow": {
        "n_cashflow_act": "operating_cf",
        "n_cashflow_inv_act": "investing_cf",
        "n_cash_flows_fnc_act": "financing_cf",
    },
    "fina_indicator": {
        "eps": "eps",
        "roe": "roe",
        "roa": "roa",
    },
}


def _key_set(frame: pd.DataFrame, date_column: str) -> set[tuple[str, str]]:
    return {
        (str(row.ts_code), str(getattr(row, date_column)))
        for row in frame.itertuples(index=False)
    }


def _numeric_equal(left: Any, right: Any) -> bool:
    left_number = pd.to_numeric(pd.Series([left]), errors="coerce").iloc[0]
    right_number = pd.to_numeric(pd.Series([right]), errors="coerce").iloc[0]
    if pd.isna(left_number) and pd.isna(right_number):
        return True
    if pd.isna(left_number) or pd.isna(right_number):
        return False
    return bool(abs(float(left_number) - float(right_number)) <= 1e-9)


def validate_snapshot(
    snapshot_dir: Path,
    index_path: Path,
    *,
    expected_parser_version: str | None = None,
) -> dict[str, object]:
    wide_path = snapshot_dir / "official_financials.csv"
    if not wide_path.exists():
        raise FileNotFoundError(f"快照缺少 {wide_path}")
    wide = pd.read_csv(wide_path, dtype=str).fillna("")
    index = pd.read_csv(index_path, dtype=str).fillna("")
    manifest_path = snapshot_dir / "extraction_manifest.json"
    manifest = (
        json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest_path.exists()
        else {}
    )
    required_wide = {
        "ts_code",
        "period_end",
        "source_file",
        "source_sha256",
        "parser_version",
        "status",
        "missing_fields",
    }
    required_index = {"ts_code", "period_end", "local_path", "sha256", "status"}
    errors: list[str] = []
    missing = required_wide - set(wide.columns)
    if missing:
        errors.append(f"wide 缺少字段：{', '.join(sorted(missing))}")
    missing = required_index - set(index.columns)
    if missing:
        errors.append(f"索引缺少字段：{', '.join(sorted(missing))}")
    if errors:
        return {"status": "fail", "errors": errors, "row_count": len(wide)}
    if expected_parser_version and manifest.get("parser_version") != expected_parser_version:
        errors.append(
            f"manifest parser_version 不为 {expected_parser_version}："
            f"{manifest.get('parser_version', '')}"
        )

    expected = index.loc[index["status"].eq("provided"), KEYS]
    expected_keys = _key_set(expected, "period_end")
    wide_keys = _key_set(wide, "period_end")
    if wide_keys != expected_keys:
        errors.append(
            "报告期覆盖不一致："
            f"缺少 {len(expected_keys - wide_keys)}，多出 {len(wide_keys - expected_keys)}"
        )
    if wide.duplicated(KEYS).any():
        errors.append("wide 存在重复 ts_code + period_end")

    index_lookup = index.set_index(KEYS)
    for row in wide.itertuples(index=False):
        key = (str(row.ts_code), str(row.period_end))
        if key not in index_lookup.index:
            continue
        expected_row = index_lookup.loc[key]
        source_name = str(row.source_file).split("!")[-1]
        indexed_name = str(expected_row.local_path).split("!")[-1]
        if source_name != indexed_name:
            errors.append(f"{key} source_file 与索引不一致")
        if str(row.source_sha256) != str(expected_row.sha256):
            errors.append(f"{key} source_sha256 与索引不一致")
        missing_fields = {
            item.strip()
            for item in str(row.missing_fields).split(",")
            if item.strip()
        }
        if str(row.status) == "parsed" and missing_fields:
            errors.append(f"{key} 标记 parsed 但仍有缺失字段")
        if str(row.status) == "partial" and not missing_fields:
            errors.append(f"{key} 标记 partial 但缺失字段为空")

    for dataset, mapping in NORMALIZED_FIELDS.items():
        path = snapshot_dir / f"{dataset}.csv"
        if not path.exists():
            errors.append(f"快照缺少 {dataset}.csv")
            continue
        frame = pd.read_csv(path, dtype=str).fillna("")
        if _key_set(frame, "end_date") != wide_keys:
            errors.append(f"{dataset} 覆盖期与 wide 不一致")
        if frame.duplicated(["ts_code", "end_date"]).any():
            errors.append(f"{dataset} 存在重复 ts_code + end_date")
        wide_fields = list(set(mapping.values()))
        wide_subset = wide[KEYS + wide_fields].rename(
            columns={field: f"{field}__wide" for field in wide_fields}
        )
        merged = frame.merge(
            wide_subset,
            left_on=["ts_code", "end_date"],
            right_on=KEYS,
            how="left",
            validate="one_to_one",
        )
        for source_field, wide_field in mapping.items():
            right_field = f"{wide_field}__wide"
            for row in merged[[source_field, right_field]].itertuples(index=False):
                if not _numeric_equal(row[0], row[1]):
                    errors.append(f"{dataset}.{source_field} 与 wide.{wide_field} 不一致")
                    break

    result = {
        "status": "pass" if not errors else "fail",
        "snapshot": str(snapshot_dir.resolve()),
        "index": str(index_path.resolve()),
        "row_count": int(len(wide)),
        "status_counts": {str(k): int(v) for k, v in wide["status"].value_counts().items()},
        "row_parser_versions": {
            str(k): int(v) for k, v in wide["parser_version"].value_counts().items()
        },
        "missing_field_counts": {
            field: sum(field in str(value).split(",") for value in wide["missing_fields"])
            for field in sorted(
                {
                    field
                    for value in wide["missing_fields"]
                    for field in str(value).split(",")
                    if field
                }
            )
        },
        "errors": errors,
    }
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="验证官方 PDF 快照是否满足晋升门禁")
    parser.add_argument("--snapshot-dir", type=Path, required=True)
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--expected-parser-version")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = validate_snapshot(
        args.snapshot_dir,
        args.index,
        expected_parser_version=args.expected_parser_version,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
