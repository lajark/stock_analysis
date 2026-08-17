"""Merge filtered official-financial extraction results into a snapshot.

Only rows present in the patch directories replace rows with the same
``ts_code + period_end`` key.  Unchanged rows and their source hashes remain
in the base snapshot, which makes an incremental parser run auditable and
avoids reparsing the other reports.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

DATASETS = ("income", "balance_sheet", "cashflow", "fina_indicator")
MERGE_VERSION = "official-financials-merge-v1"


def _read_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path) if path.exists() else pd.DataFrame()


def _merge_frames(base: pd.DataFrame, patches: list[pd.DataFrame]) -> pd.DataFrame:
    frames = [frame for frame in [base, *patches] if not frame.empty]
    if not frames:
        return pd.DataFrame()
    merged = pd.concat(frames, ignore_index=True, sort=False)
    if {"ts_code", "period_end"}.issubset(merged.columns):
        key_columns = ["ts_code", "period_end"]
    elif {"ts_code", "end_date"}.issubset(merged.columns):
        key_columns = ["ts_code", "end_date"]
    elif "period_end" in merged.columns:
        key_columns = ["period_end"]
    elif "end_date" in merged.columns:
        key_columns = ["end_date"]
    else:
        key_columns = []
    if key_columns:
        merged = merged.drop_duplicates(subset=key_columns, keep="last")
        merged = merged.sort_values(key_columns, kind="mergesort")
    return merged.reset_index(drop=True)


def _manifest(base_dir: Path) -> dict[str, Any]:
    path = base_dir / "extraction_manifest.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _status_counts(wide: pd.DataFrame) -> dict[str, int]:
    if "status" not in wide.columns:
        return {}
    return {str(key): int(value) for key, value in wide["status"].fillna("").value_counts().items()}


def _missing_counts(wide: pd.DataFrame) -> dict[str, int]:
    counts: dict[str, int] = {}
    if "missing_fields" not in wide.columns:
        return counts
    for value in wide["missing_fields"].fillna(""):
        for field in (item.strip() for item in str(value).split(",")):
            if field:
                counts[field] = counts.get(field, 0) + 1
    return dict(sorted(counts.items()))


def merge_snapshots(base_dir: Path, patch_dirs: list[Path], output_dir: Path) -> dict[str, Any]:
    """Merge one or more filtered extraction directories into ``output_dir``."""
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(f"输出目录非空，为避免覆盖已有快照：{output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)

    wide = _merge_frames(
        _read_csv(base_dir / "official_financials.csv"),
        [_read_csv(path / "official_financials.csv") for path in patch_dirs],
    )
    if wide.empty:
        raise ValueError("基础快照缺少 official_financials.csv，无法合并")
    wide.to_csv(output_dir / "official_financials.csv", index=False, encoding="utf-8-sig")

    for dataset in DATASETS:
        merged = _merge_frames(
            _read_csv(base_dir / f"{dataset}.csv"),
            [_read_csv(path / f"{dataset}.csv") for path in patch_dirs],
        )
        if not merged.empty:
            merged.to_csv(output_dir / f"{dataset}.csv", index=False, encoding="utf-8-sig")

    # Keep auxiliary evidence files (source PDFs are referenced by path), but
    # never copy stale derived ratios or the old extraction manifest.
    excluded = {
        "official_financials.csv",
        "official-ratios.csv",
        "extraction_manifest.json",
        *(f"{dataset}.csv" for dataset in DATASETS),
    }
    for item in base_dir.iterdir():
        if item.is_file() and item.name not in excluded:
            shutil.copy2(item, output_dir / item.name)

    base_manifest = _manifest(base_dir)
    patch_manifests = [_manifest(path) for path in patch_dirs]
    latest_parser = next(
        (
            item.get("parser_version")
            for item in reversed(patch_manifests)
            if item.get("parser_version")
        ),
        base_manifest.get("parser_version", ""),
    )
    manifest: dict[str, Any] = {
        **base_manifest,
        "parser_version": latest_parser,
        "merge_version": MERGE_VERSION,
        "report_count": len(wide),
        "status_counts": _status_counts(wide),
        "missing_field_counts": _missing_counts(wide),
        "base_snapshot": str(base_dir.resolve()),
        "incremental_patches": [
            {
                "directory": str(path.resolve()),
                "report_count": len(_read_csv(path / "official_financials.csv")),
                "parser_version": _manifest(path).get("parser_version", ""),
            }
            for path in patch_dirs
        ],
        "pit_note": base_manifest.get(
            "pit_note",
            "公告日期未从资料包中提取；本批仅做报告期覆盖和值字段比对。",
        ),
    }
    (output_dir / "extraction_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="合并官方 PDF 增量抽取结果")
    parser.add_argument("--base-dir", type=Path, required=True)
    parser.add_argument("--patch-dir", type=Path, action="append", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    result = merge_snapshots(args.base_dir, args.patch_dir, args.output_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
