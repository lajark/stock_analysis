"""Prepare an official-disclosure collection checklist from an audit snapshot."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

REPORT_TYPES = {
    "03-31": "q1",
    "06-30": "semiannual",
    "09-30": "q3",
    "12-31": "annual",
}


def _report_type(period_end: str) -> str:
    suffix = period_end[4:].lstrip("-")
    try:
        return REPORT_TYPES[suffix]
    except KeyError as error:
        raise ValueError(f"不支持的报告期：{period_end}") from error


def build_checklist(audit_dir: Path) -> list[dict[str, str]]:
    sample_path = audit_dir / "sample_manifest.csv"
    export_path = audit_dir / "export_manifest.json"
    with sample_path.open(encoding="utf-8-sig", newline="") as handle:
        samples = list(csv.DictReader(handle))
    export = json.loads(export_path.read_text(encoding="utf-8"))
    periods = [str(value) for value in export["fiscal_periods"]]
    rows: list[dict[str, str]] = []
    for sample in samples:
        for period_end in periods:
            rows.append(
                {
                    "ts_code": sample["ts_code"],
                    "name": sample["name"],
                    "market": sample["market"],
                    "report_type": _report_type(period_end),
                    "period_end": period_end,
                    "announce_date": "",
                    "revision": "original",
                    "status": "pending",
                    "file_type": "pdf_or_xbrl",
                    "preferred_source": "cninfo_or_exchange",
                    "source_url": "",
                    "local_path": "",
                    "sha256": "",
                    "notes": "保留公告日；如有修订版，另行增加一行并标记 revision",
                }
            )
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    rows = build_checklist(args.audit_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0]) if rows else []
    with args.output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"generated {len(rows)} rows -> {args.output}")


if __name__ == "__main__":
    main()
