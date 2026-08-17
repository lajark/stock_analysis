"""Index a periodic-report ZIP against the 30-stock audit checklist.

The command does not extract or modify the source archive. It records each
matched PDF member, a SHA-256 digest of the decompressed PDF bytes, and an
explicit status for known structural gaps such as a delisted issuer.

Example::

    python scripts/index_official_reports.py \
        --audit-dir .workspace/tmp/tushare-audit-20260815 \
        --archive ../periodic_reports_20260815.zip \
        --exceptions .workspace/tmp/official-disclosure-exceptions-20260815.json \
        --output .workspace/tmp/official-disclosure-checklist-indexed-20260815.csv \
        --manifest-output .workspace/tmp/official-report-manifest-20260815.json
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import zipfile
from pathlib import Path
from typing import Any

REPORT_TYPES = {
    "03-31": "q1",
    "06-30": "semiannual",
    "09-30": "q3",
    "12-31": "annual",
}
CHUNK_SIZE = 1024 * 1024


def _report_type(period_end: str) -> str:
    suffix = period_end[4:].lstrip("-")
    try:
        return REPORT_TYPES[suffix]
    except KeyError as error:
        raise ValueError(f"不支持的报告期：{period_end}") from error


def _member_name(row: dict[str, str]) -> str:
    return (
        f"{row['ts_code']}_{row['period_end']}_"
        f"{_report_type(row['period_end'])}_original.pdf"
    )


def _sha256_member(archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> str:
    digest = hashlib.sha256()
    with archive.open(info, "r") as handle:
        while chunk := handle.read(CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def _load_checklist(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _load_exceptions(path: Path | None) -> dict[str, dict[str, str]]:
    if path is None:
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("exceptions 文件必须是 JSON 对象")
    exceptions = payload.get("exceptions", payload)
    if not isinstance(exceptions, dict):
        raise ValueError("exceptions 字段必须是对象")
    return {
        str(key): {str(k): str(v) for k, v in value.items()}
        for key, value in exceptions.items()
        if isinstance(value, dict)
    }


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0]) if rows else []
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def index_reports(
    checklist_path: Path,
    archive_path: Path,
    *,
    exceptions_path: Path | None = None,
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    """Return an indexed checklist and a compact archive manifest."""
    rows = _load_checklist(checklist_path)
    exceptions = _load_exceptions(exceptions_path)
    archive_path = archive_path.resolve()

    with zipfile.ZipFile(archive_path) as archive:
        members: dict[str, zipfile.ZipInfo] = {}
        duplicate_names: list[str] = []
        for info in archive.infolist():
            if info.is_dir():
                continue
            name = Path(info.filename).name
            if name in members:
                duplicate_names.append(name)
            members[name] = info

        indexed: list[dict[str, str]] = []
        matched_members: set[str] = set()
        for row in rows:
            key = f"{row['ts_code']}|{row['period_end']}"
            member_name = _member_name(row)
            exception = exceptions.get(key)
            updated = dict(row)
            updated["file_type"] = "pdf"
            updated["preferred_source"] = "cninfo"
            if exception:
                updated["status"] = exception.get("status", "structural_missing")
                updated["local_path"] = ""
                updated["sha256"] = ""
                updated["notes"] = exception.get("reason", "结构性缺失")
            elif member_name in members:
                info = members[member_name]
                updated["status"] = "provided"
                updated["local_path"] = f"{archive_path}!{member_name}"
                updated["sha256"] = _sha256_member(archive, info)
                updated["notes"] = (
                    f"archive_member={member_name}; size={info.file_size}; "
                    "PDF 已入包，字段抽取待完成"
                )
                matched_members.add(member_name)
            else:
                updated["status"] = "missing"
                updated["local_path"] = ""
                updated["sha256"] = ""
                updated["notes"] = "清单项未在资料包中找到，需人工复核"
            indexed.append(updated)

        manifest: dict[str, Any] = {
            "archive": str(archive_path),
            "archive_size": archive_path.stat().st_size,
            "checklist": str(checklist_path.resolve()),
            "member_count": len(members),
            "duplicate_member_names": sorted(duplicate_names),
            "matched_member_count": len(matched_members),
            "unmatched_members": sorted(set(members) - matched_members),
            "status_counts": {
                status: sum(row["status"] == status for row in indexed)
                for status in sorted({row["status"] for row in indexed})
            },
            "structural_exceptions": exceptions,
            "hash_algorithm": "sha256",
            "hash_scope": "decompressed PDF bytes",
        }
    return indexed, manifest


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checklist", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--exceptions", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest-output", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    rows, manifest = index_reports(
        args.checklist,
        args.archive,
        exceptions_path=args.exceptions,
    )
    _write_csv(args.output, rows)
    args.manifest_output.parent.mkdir(parents=True, exist_ok=True)
    args.manifest_output.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        f"indexed {len(rows)} checklist rows; "
        f"{manifest['matched_member_count']} archive members matched -> {args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

