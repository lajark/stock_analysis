"""Compare two exported financial-data directories at one as-of date.

Example:
    python scripts/reconcile_financial_data.py \
        --left samples/tushare --right samples/reference \
        --as-of 2025-12-31 --output reconciliation.json

Each directory may contain income, balance_sheet, cashflow and
fina_indicator as CSV or Parquet files. The script never calls a provider
and never changes the configured primary data source.
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

from src.data.reconciliation import reconcile_financial_sets  # noqa: E402

DATASETS = ("income", "balance_sheet", "cashflow", "fina_indicator")


def _load_dataset(directory: Path, dataset: str) -> pd.DataFrame | None:
    for suffix, reader in (
        (".parquet", pd.read_parquet),
        (".csv", pd.read_csv),
    ):
        path = directory / f"{dataset}{suffix}"
        if path.exists():
            return reader(path)
    return None


def _load_directory(directory: Path, datasets: tuple[str, ...]) -> dict[str, pd.DataFrame | None]:
    return {dataset: _load_dataset(directory, dataset) for dataset in datasets}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="财务数据分层对账工具")
    parser.add_argument("--left", type=Path, required=True, help="左侧数据目录，默认标记为 Tushare")
    parser.add_argument("--right", type=Path, required=True, help="右侧参考数据目录")
    parser.add_argument("--as-of", required=True, help="分析时点，格式 YYYY-MM-DD")
    parser.add_argument(
        "--datasets",
        default=",".join(DATASETS),
        help="逗号分隔的数据集名称，默认四张财务表",
    )
    parser.add_argument("--left-source", default="tushare")
    parser.add_argument("--right-source", default="reference")
    parser.add_argument("--output", type=Path, help="可选 JSON 输出路径；不填则打印到 stdout")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    datasets = tuple(item.strip() for item in args.datasets.split(",") if item.strip())
    invalid = sorted(set(datasets) - set(DATASETS))
    if invalid:
        raise SystemExit(f"不支持的数据集：{', '.join(invalid)}")
    report: dict[str, Any] = reconcile_financial_sets(
        _load_directory(args.left, datasets),
        _load_directory(args.right, datasets),
        as_of=args.as_of,
        datasets=datasets,
        left_source=args.left_source,
        right_source=args.right_source,
    )
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n", encoding="utf-8")
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
