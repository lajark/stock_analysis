"""Render the checked-in deterministic example report without network access."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.reports.renderer import render_report  # noqa: E402

DEFAULT_PACKAGE = PROJECT_ROOT / "docs" / "examples" / "sample-analysis-package.json"
DEFAULT_OUTPUT = PROJECT_ROOT / "docs" / "examples" / "sample-report.md"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, default=DEFAULT_PACKAGE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    package = json.loads(args.package.read_text(encoding="utf-8"))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    render_report(
        package,
        "结构化指标显示样本处于震荡偏强状态。此固定叙述只用于展示报告形态，数字来自随附分析包，不由模型计算。",
        "example-static-narrative",
        {"input_tokens": 0, "output_tokens": 0},
        str(args.output),
        run_id="example-600519-20260814",
    )
    print(f"rendered example report -> {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
