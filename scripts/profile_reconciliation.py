"""Profile field- and security-level differences in two financial snapshots.

This is a read-only companion to ``reconcile_financial_data.py``.  It keeps
the reconciliation result auditable by reporting every numeric field pair,
not only the first bounded list of examples in the JSON reconciler output.
The script does not select a primary provider or infer which side is correct.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.data.financials import filter_financial_as_of, normalize_financial_frame  # noqa: E402

DATASETS = ("income", "balance_sheet", "cashflow", "fina_indicator")
METADATA_COLUMNS = {
    "ts_code",
    "end_date",
    "ann_date",
    "f_ann_date",
    "period",
    "report_type",
    "comp_type",
    "update_flag",
}
BUCKETS = (
    "exact",
    "minor_lt_1pct",
    "moderate_1_to_10pct",
    "large_10_to_50pct",
    "very_large_50pct_or_more",
    "missing_left",
    "missing_right",
    "missing_both",
)


def _load_dataset(directory: Path, dataset: str) -> pd.DataFrame | None:
    for suffix, reader in ((".parquet", pd.read_parquet), (".csv", pd.read_csv)):
        path = directory / f"{dataset}{suffix}"
        if path.exists():
            return reader(path)
    return None


def _key_columns(left: pd.DataFrame, right: pd.DataFrame) -> list[str]:
    if "ts_code" in left.columns and "ts_code" in right.columns:
        return ["ts_code", "end_date"]
    return ["end_date"]


def _numeric_columns(left: pd.DataFrame, right: pd.DataFrame) -> list[str]:
    columns = sorted((set(left.columns) & set(right.columns)) - METADATA_COLUMNS)
    return [
        column
        for column in columns
        if pd.to_numeric(left[column], errors="coerce").notna().any()
        or pd.to_numeric(right[column], errors="coerce").notna().any()
    ]


def _bucket(left: float, right: float, *, abs_tol: float, rel_tol: float) -> str:
    difference = abs(left - right)
    scale = max(abs(left), abs(right), 1.0)
    relative = difference / scale
    if difference <= abs_tol + rel_tol * scale:
        return "exact"
    if relative < 0.01:
        return "minor_lt_1pct"
    if relative < 0.10:
        return "moderate_1_to_10pct"
    if relative < 0.50:
        return "large_10_to_50pct"
    return "very_large_50pct_or_more"


def _empty_counts() -> dict[str, int]:
    return {bucket: 0 for bucket in BUCKETS}


def profile_dataset(
    left: pd.DataFrame | None,
    right: pd.DataFrame | None,
    *,
    dataset: str,
    as_of: str,
    abs_tol: float = 1e-6,
    rel_tol: float = 1e-4,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, int]]:
    """Return field and security profiles after applying the shared PIT policy."""
    left_raw_rows = 0 if left is None else len(left)
    right_raw_rows = 0 if right is None else len(right)
    left_filtered = filter_financial_as_of(left, as_of) if left is not None else pd.DataFrame()
    right_filtered = filter_financial_as_of(right, as_of) if right is not None else pd.DataFrame()
    left_canonical = normalize_financial_frame(left_filtered, as_of=as_of)
    right_canonical = normalize_financial_frame(right_filtered, as_of=as_of)
    if left_canonical.empty or right_canonical.empty:
        return (
            pd.DataFrame(),
            pd.DataFrame(),
            {
                "left_raw_rows": left_raw_rows,
                "right_raw_rows": right_raw_rows,
                "left_canonical_rows": len(left_canonical),
                "right_canonical_rows": len(right_canonical),
                "matched_positions": 0,
            },
        )

    key_columns = _key_columns(left_canonical, right_canonical)
    left_indexed = left_canonical.set_index(key_columns)
    right_indexed = right_canonical.set_index(key_columns)
    keys = sorted(set(left_indexed.index) & set(right_indexed.index))
    fields = _numeric_columns(left_canonical, right_canonical)
    field_counts: dict[str, dict[str, Any]] = {}
    security_counts: dict[str, dict[str, Any]] = {}
    for field in fields:
        counts = _empty_counts()
        relative_values: list[float] = []
        absolute_values: list[float] = []
        sign_flip_count = 0
        for key in keys:
            left_value = pd.to_numeric(
                pd.Series([left_indexed.at[key, field]]), errors="coerce"
            ).iloc[0]
            right_value = pd.to_numeric(
                pd.Series([right_indexed.at[key, field]]), errors="coerce"
            ).iloc[0]
            if pd.isna(left_value) and pd.isna(right_value):
                bucket = "missing_both"
            elif pd.isna(left_value):
                bucket = "missing_left"
            elif pd.isna(right_value):
                bucket = "missing_right"
            else:
                bucket = _bucket(
                    float(left_value),
                    float(right_value),
                    abs_tol=abs_tol,
                    rel_tol=rel_tol,
                )
                if bucket != "exact":
                    difference = abs(float(left_value) - float(right_value))
                    scale = max(abs(float(left_value)), abs(float(right_value)), 1.0)
                    absolute_values.append(difference)
                    relative_values.append(difference / scale)
                    if float(left_value) * float(right_value) < 0:
                        sign_flip_count += 1
            counts[bucket] += 1
            security = key[0] if len(key_columns) == 2 else ""
            security_record = security_counts.setdefault(security, {"ts_code": security})
            security_record[f"{field}_{bucket}"] = security_record.get(f"{field}_{bucket}", 0) + 1
            security_record[f"{field}_pairs"] = security_record.get(f"{field}_pairs", 0) + 1
        field_counts[field] = {
            "dataset": dataset,
            "field": field,
            "matched_positions": len(keys),
            "comparable_pairs": sum(counts[bucket] for bucket in BUCKETS[:5]),
            "mismatch_pairs": sum(counts[bucket] for bucket in BUCKETS[1:5]),
            "sign_flip_count": sign_flip_count,
            "median_relative_diff": (
                float(pd.Series(relative_values).median()) if relative_values else 0.0
            ),
            "max_relative_diff": float(max(relative_values, default=0.0)),
            "median_absolute_diff": (
                float(pd.Series(absolute_values).median()) if absolute_values else 0.0
            ),
            "max_absolute_diff": float(max(absolute_values, default=0.0)),
            **counts,
        }

    security_rows = []
    for security, values in sorted(security_counts.items()):
        row: dict[str, Any] = {"dataset": dataset, "ts_code": security}
        row.update(values)
        row["mismatch_pairs"] = sum(
            value
            for key, value in row.items()
            if key.endswith(
                (
                    "_minor_lt_1pct",
                    "_moderate_1_to_10pct",
                    "_large_10_to_50pct",
                    "_very_large_50pct_or_more",
                )
            )
        )
        security_rows.append(row)
    metadata = {
        "left_raw_rows": left_raw_rows,
        "right_raw_rows": right_raw_rows,
        "left_canonical_rows": len(left_canonical),
        "right_canonical_rows": len(right_canonical),
        "matched_positions": len(keys),
    }
    return pd.DataFrame(field_counts.values()), pd.DataFrame(security_rows), metadata


def _markdown(field_profile: pd.DataFrame, metadata: dict[str, Any]) -> str:
    lines = [
        "# 财务对账差异分层摘要",
        "",
        f"分析时点：`{metadata['as_of']}`；左源：`{metadata['left_source']}`；右源：`{metadata['right_source']}`。",
        "",
        (
            "本报告仅描述差异，不判断哪一方正确；`fina_indicator.roa` 等右侧缺失字段"
            "应结合独立公式审计解释。"
        ),
        "",
        "## 字段分层",
        "",
        "| 数据集 | 字段 | 可比对 | 不一致 | <1% | 1%-10% | 10%-50% | ≥50% | "
        "缺左 | 缺右 | 符号反转 |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    sorted_fields = field_profile.sort_values(
        ["mismatch_pairs", "dataset", "field"], ascending=[False, True, True]
    )
    for row in sorted_fields.itertuples():
        lines.append(
            f"| {row.dataset} | {row.field} | {row.comparable_pairs} | {row.mismatch_pairs} | "
            f"{row.minor_lt_1pct} | {row.moderate_1_to_10pct} | {row.large_10_to_50pct} | "
            f"{row.very_large_50pct_or_more} | {row.missing_left} | "
            f"{row.missing_right} | {row.sign_flip_count} |"
        )
    lines.extend(
        [
            "",
            "## 解读边界",
            "",
            "- 大比例差异可能来自修订版本、累计值/单季值口径、单位或 PDF 解析错位，"
            "不能直接作为换源证据。",
            "- 负值与正值反转需要优先人工抽查原始报告页和字段标签。",
            "- 仅当同一字段在多股票、多报告期排除口径与解析因素后仍稳定偏差，"
            "才进入主源替换决策门。",
            "",
        ]
    )
    return "\n".join(lines)


def profile_directories(
    left_dir: Path,
    right_dir: Path,
    *,
    as_of: str,
    output_dir: Path,
    left_source: str = "tushare",
    right_source: str = "cninfo_pdf",
    datasets: tuple[str, ...] = DATASETS,
) -> dict[str, Any]:
    field_frames: list[pd.DataFrame] = []
    security_frames: list[pd.DataFrame] = []
    dataset_metadata: dict[str, Any] = {}
    for dataset in datasets:
        fields, securities, metadata = profile_dataset(
            _load_dataset(left_dir, dataset),
            _load_dataset(right_dir, dataset),
            dataset=dataset,
            as_of=as_of,
        )
        if not fields.empty:
            field_frames.append(fields)
        if not securities.empty:
            security_frames.append(securities)
        dataset_metadata[dataset] = metadata
    field_profile = pd.concat(field_frames, ignore_index=True) if field_frames else pd.DataFrame()
    security_profile = (
        pd.concat(security_frames, ignore_index=True)
        if security_frames
        else pd.DataFrame()
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    field_path = output_dir / "reconciliation-field-profile.csv"
    security_path = output_dir / "reconciliation-security-profile.csv"
    markdown_path = output_dir / "reconciliation-profile.md"
    manifest_path = output_dir / "reconciliation-profile-manifest.json"
    field_profile.to_csv(field_path, index=False, encoding="utf-8-sig")
    security_profile.to_csv(security_path, index=False, encoding="utf-8-sig")
    metadata = {
        "as_of": as_of,
        "left_source": left_source,
        "right_source": right_source,
        "left_dir": str(left_dir.resolve()),
        "right_dir": str(right_dir.resolve()),
        "datasets": dataset_metadata,
        "field_count": len(field_profile),
        "security_rows": len(security_profile),
    }
    markdown_path.write_text(_markdown(field_profile, metadata), encoding="utf-8")
    manifest_path.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return {
        **metadata,
        "field_profile": str(field_path.resolve()),
        "security_profile": str(security_path.resolve()),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="输出财务对账字段/股票分层差异")
    parser.add_argument("--left", type=Path, required=True)
    parser.add_argument("--right", type=Path, required=True)
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--left-source", default="tushare")
    parser.add_argument("--right-source", default="cninfo_pdf")
    parser.add_argument("--datasets", default=",".join(DATASETS))
    args = parser.parse_args()
    datasets = tuple(item.strip() for item in args.datasets.split(",") if item.strip())
    invalid = sorted(set(datasets) - set(DATASETS))
    if invalid:
        raise SystemExit(f"不支持的数据集：{', '.join(invalid)}")
    result = profile_directories(
        args.left,
        args.right,
        as_of=args.as_of,
        output_dir=args.output_dir,
        left_source=args.left_source,
        right_source=args.right_source,
        datasets=datasets,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
