"""Run the public, offline benchmark against user-supplied source materials."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.validate_official_snapshot import validate_snapshot  # noqa: E402
from src.data.adjustments import (  # noqa: E402
    ADJUSTMENT_APPLICATION_VERSION,
    AdjustmentError,
    apply_price_adjustment,
)

SCHEMA_VERSION = "stock-analysis-benchmark/v1"
REQUIRED_INDEX_COLUMNS = {
    "ts_code",
    "period_end",
    "announce_date",
    "revision",
    "status",
    "sha256",
    "announcement_source_url",
    "announcement_candidate_count",
}
OHLC_COLUMNS = ("open", "high", "low", "close")


class BenchmarkInputError(ValueError):
    """Raised when a manifest or required local material is invalid."""


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BenchmarkInputError(f"无法读取 JSON 清单：{path.name}") from exc
    if not isinstance(value, dict):
        raise BenchmarkInputError("benchmark 清单顶层必须是对象")
    return value


def _safe_path(root: Path, relative: str) -> Path:
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise BenchmarkInputError(f"路径越过 source-dir：{relative}") from exc
    return candidate


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _check_file(
    root: Path,
    relative: str,
    expected_sha256: str,
    checks: list[dict[str, str]],
    *,
    check_id: str,
) -> bool:
    try:
        path = _safe_path(root, relative)
    except BenchmarkInputError as exc:
        checks.append({"id": check_id, "status": "fail", "message": str(exc)})
        return False
    if not path.is_file():
        checks.append(
            {
                "id": check_id,
                "status": "fail",
                "message": f"缺少本地材料：{relative}",
            }
        )
        return False
    actual = _sha256(path)
    if actual.lower() != expected_sha256.lower():
        checks.append(
            {
                "id": check_id,
                "status": "fail",
                "message": f"SHA-256 不匹配：{relative}",
            }
        )
        return False
    checks.append({"id": check_id, "status": "pass", "message": f"哈希通过：{relative}"})
    return True


def _parse_date(value: Any) -> pd.Timestamp | None:
    if value is None or str(value).strip() == "":
        return None
    text = str(value).strip()
    if text.isdigit() and len(text) == 8:
        parsed = pd.to_datetime(text, format="%Y%m%d", errors="coerce")
    else:
        parsed = pd.to_datetime(text, errors="coerce")
    return None if pd.isna(parsed) else pd.Timestamp(parsed)


def _load_frame(path: Path, *, date_format: str | None = None) -> pd.DataFrame:
    if path.suffix.lower() == ".parquet":
        frame = pd.read_parquet(path)
    else:
        frame = pd.read_csv(path)
    if "trade_date" in frame.columns:
        raw_dates = frame["trade_date"].astype("string")
        if date_format:
            parsed = pd.to_datetime(raw_dates, format=date_format, errors="coerce")
            fallback = pd.to_datetime(raw_dates, errors="coerce")
            frame["trade_date"] = parsed.fillna(fallback)
        else:
            frame["trade_date"] = pd.to_datetime(raw_dates, errors="coerce")
    return frame


def _validate_manifest(manifest: dict[str, Any]) -> None:
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise BenchmarkInputError(
            f"不支持的 benchmark schema：{manifest.get('schema_version')!r}"
        )
    if _parse_date(manifest.get("as_of")) is None:
        raise BenchmarkInputError("清单缺少有效 as_of")
    if not isinstance(manifest.get("prepared"), dict):
        raise BenchmarkInputError("清单缺少 prepared 配置")
    for key in ("snapshot_dir", "index"):
        value = manifest["prepared"].get(key)
        if not isinstance(value, str) or not value.strip():
            raise BenchmarkInputError(f"prepared.{key} 必须是相对路径")
        path = Path(value)
        if path.is_absolute() or ".." in path.parts:
            raise BenchmarkInputError(f"prepared.{key} 必须位于 source-dir 内")
    if not isinstance(manifest.get("financial_cases"), list) or not manifest["financial_cases"]:
        raise BenchmarkInputError("financial_cases 不能为空")
    if not isinstance(manifest.get("adjustment_cases"), list) or not manifest["adjustment_cases"]:
        raise BenchmarkInputError("adjustment_cases 不能为空")
    for case in manifest["financial_cases"]:
        if not isinstance(case, dict):
            raise BenchmarkInputError("financial case 必须是对象")
        code = case.get("ts_code")
        periods = case.get("periods")
        if not isinstance(code, str) or not code.strip():
            raise BenchmarkInputError("financial case 缺少 ts_code")
        if not isinstance(periods, list) or not periods or not all(
            isinstance(period, str) and period.strip() for period in periods
        ):
            raise BenchmarkInputError(f"{code} 的 periods 必须是非空日期字符串列表")
        documents = case.get("documents")
        if not isinstance(documents, list):
            raise BenchmarkInputError(f"{code} 缺少 documents")
        document_periods: set[str] = set()
        for document in documents:
            if not isinstance(document, dict):
                raise BenchmarkInputError(f"{code} 的 document 必须是对象")
            path = document.get("path")
            digest = document.get("sha256")
            if not isinstance(path, str) or not path.strip():
                raise BenchmarkInputError(f"{code} 的 document 缺少 path")
            document_path = Path(path)
            if document_path.is_absolute() or ".." in document_path.parts:
                raise BenchmarkInputError(f"{code} 的 document path 必须位于 source-dir 内")
            if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
                raise BenchmarkInputError(f"{code} 的 document sha256 无效")
            period_end = document.get("period_end")
            if not isinstance(period_end, str) or not period_end.strip():
                raise BenchmarkInputError(f"{code} 的 document 缺少 period_end")
            if period_end in document_periods:
                raise BenchmarkInputError(f"{code} 的 document period_end 重复")
            document_periods.add(period_end)
            if not str(document.get("announcement_id", "")).strip():
                raise BenchmarkInputError(f"{code} 的 document 缺少 announcement_id")
            source_url = document.get("source_url")
            if not isinstance(source_url, str) or not source_url.startswith("https://"):
                raise BenchmarkInputError(f"{code} 的 document 缺少官方 source_url")
        if set(periods) != document_periods:
            raise BenchmarkInputError(f"{code} 的 documents 未覆盖全部 periods")
    for case in manifest["adjustment_cases"]:
        if not isinstance(case, dict):
            raise BenchmarkInputError("adjustment case 必须是对象")
        case_id = case.get("case_id")
        if not isinstance(case_id, str) or not case_id.strip():
            raise BenchmarkInputError("adjustment case 缺少 case_id")
        for field in ("raw_path", "factor_path", "reference_path"):
            value = case.get(field)
            if not isinstance(value, str) or not value.strip():
                raise BenchmarkInputError(f"{case_id} 缺少 {field}")
            path = Path(value)
            if path.is_absolute() or ".." in path.parts:
                raise BenchmarkInputError(f"{case_id} 的 {field} 必须位于 source-dir 内")
        for field in ("raw_sha256", "factor_sha256", "reference_sha256"):
            value = case.get(field)
            if not isinstance(value, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", value):
                raise BenchmarkInputError(f"{case_id} 的 {field} 无效")
        try:
            tolerance = float(case.get("tolerance", 1e-6))
        except (TypeError, ValueError) as exc:
            raise BenchmarkInputError(f"{case_id} 的 tolerance 无效") from exc
        if tolerance < 0:
            raise BenchmarkInputError(f"{case_id} 的 tolerance 不能为负数")


def _check_financial_cases(
    manifest: dict[str, Any],
    source_root: Path,
    checks: list[dict[str, str]],
) -> None:
    prepared = manifest["prepared"]
    index_path = _safe_path(source_root, str(prepared["index"]))
    snapshot_dir = _safe_path(source_root, str(prepared["snapshot_dir"]))
    if not index_path.is_file():
        raise BenchmarkInputError(f"缺少 PIT 清单：{prepared['index']}")
    if not snapshot_dir.is_dir():
        raise BenchmarkInputError(f"缺少标准化快照目录：{prepared['snapshot_dir']}")

    index = pd.read_csv(index_path, dtype="string").fillna("")
    missing = sorted(REQUIRED_INDEX_COLUMNS - set(index.columns))
    if missing:
        raise BenchmarkInputError(f"PIT 清单缺少字段：{', '.join(missing)}")

    as_of = _parse_date(manifest["as_of"])
    assert as_of is not None
    for case in manifest["financial_cases"]:
        code = str(case.get("ts_code", ""))
        periods = [str(item) for item in case.get("periods", [])]
        if not code or not periods:
            raise BenchmarkInputError("financial case 缺少 ts_code 或 periods")
        expected = case.get("expected") or {}
        documents = {str(item.get("period_end")): item for item in case.get("documents", [])}
        for period in periods:
            rows = index[(index["ts_code"] == code) & (index["period_end"] == period)]
            check_id = f"financial.{code}.{period}"
            if len(rows) != 1:
                checks.append(
                    {
                        "id": check_id,
                        "status": "fail",
                        "message": f"PIT 清单应有一行，实际 {len(rows)} 行",
                    }
                )
                continue
            row = rows.iloc[0]
            announcement = _parse_date(row["announce_date"])
            failures: list[str] = []
            if announcement is None or announcement > as_of:
                failures.append("公告日期缺失或晚于 as_of")
            if expected.get("revision") and row["revision"] != expected["revision"]:
                failures.append("revision 不符合清单预期")
            if expected.get("status") and row["status"] != expected["status"]:
                failures.append("status 不符合清单预期")
            if expected.get("announcement_match"):
                actual_match = str(row.get("announcement_match", ""))
                if actual_match != str(expected["announcement_match"]):
                    failures.append("公告匹配状态不符合清单预期")
            if expected.get("announcement_candidate_count") is not None:
                actual_count = int(row["announcement_candidate_count"] or 0)
                if actual_count != int(expected["announcement_candidate_count"]):
                    failures.append("公告候选数量不符合预期")
            source_url = str(row["announcement_source_url"])
            if not source_url.startswith("https://"):
                failures.append("缺少官方公告 URL")
            row_hash = str(row["sha256"])
            if len(row_hash) != 64:
                failures.append("报告 SHA-256 缺失或格式错误")
            document = documents.get(period)
            if document is None:
                failures.append("manifest 缺少对应 document")
            else:
                if str(document.get("sha256")) != row_hash:
                    failures.append("manifest document 与 PIT 清单哈希不一致")
                if str(document.get("announcement_id")) != str(row["announcement_id"]):
                    failures.append("manifest document 与公告 ID 不一致")
                document_date = _parse_date(document.get("announce_date"))
                if document_date is not None and announcement is not None:
                    if document_date != announcement:
                        failures.append("manifest document 与 PIT 清单公告日期不一致")
                document_url = str(document.get("source_url", ""))
                if document_url and document_url != source_url:
                    failures.append("manifest document 与 PIT 清单官方 URL 不一致")
                _check_file(
                    source_root,
                    str(document.get("path", "")),
                    str(document.get("sha256", "")),
                    checks,
                    check_id=f"document.{code}.{period}",
                )
            if failures:
                checks.append({"id": check_id, "status": "fail", "message": "；".join(failures)})
            else:
                checks.append({"id": check_id, "status": "pass", "message": "PIT 元数据通过"})

    snapshot_path = snapshot_dir / "official_financials.csv"
    if not snapshot_path.is_file():
        raise BenchmarkInputError("标准化快照缺少 official_financials.csv")
    try:
        validation = validate_snapshot(
            snapshot_dir,
            index_path,
            expected_parser_version=str(manifest.get("parser_version") or "") or None,
        )
    except (OSError, TypeError, ValueError, KeyError, AttributeError) as exc:
        checks.append(
            {"id": "financial.snapshot", "status": "fail", "message": f"快照验证异常：{exc}"}
        )
    else:
        status = "pass" if validation.get("status") == "pass" else "fail"
        checks.append(
            {
                "id": "financial.snapshot",
                "status": status,
                "message": f"官方快照验证：{validation.get('status', 'unknown')}",
            }
        )


def _check_adjustment_case(
    case: dict[str, Any],
    source_root: Path,
    checks: list[dict[str, str]],
) -> None:
    case_id = str(case.get("case_id", "unknown"))
    if case.get("method_version") != ADJUSTMENT_APPLICATION_VERSION:
        checks.append(
            {
                "id": f"adjustment.{case_id}.method",
                "status": "fail",
                "message": "复权方法版本与当前实现不一致",
            }
        )
    paths = {
        "raw": str(case.get("raw_path", "")),
        "factor": str(case.get("factor_path", "")),
        "reference": str(case.get("reference_path", "")),
    }
    hashes = {
        "raw": str(case.get("raw_sha256", "")),
        "factor": str(case.get("factor_sha256", "")),
        "reference": str(case.get("reference_sha256", "")),
    }
    for role in paths:
        _check_file(
            source_root,
            paths[role],
            hashes[role],
            checks,
            check_id=f"adjustment.{case_id}.{role}.hash",
        )
    try:
        raw = _load_frame(_safe_path(source_root, paths["raw"]), date_format="%Y%m%d")
        factors = _load_frame(_safe_path(source_root, paths["factor"]), date_format="%Y%m%d")
        reference = _load_frame(
            _safe_path(source_root, paths["reference"]), date_format="%Y%m%d"
        )
        if not set(OHLC_COLUMNS).issubset(raw.columns):
            raise AdjustmentError("原始行情缺少 OHLC 字段")
        if not set(OHLC_COLUMNS).issubset(reference.columns):
            raise AdjustmentError("参考行情缺少 OHLC 字段")
        calculated = apply_price_adjustment(raw, factors, "qfq")
        calculated = calculated.sort_values("trade_date").reset_index(drop=True)
        reference = reference.sort_values("trade_date").reset_index(drop=True)
        if len(calculated) != len(reference):
            raise AdjustmentError("计算结果与参考行情行数不一致")
        for column in OHLC_COLUMNS:
            left = pd.to_numeric(calculated[column], errors="coerce")
            right = pd.to_numeric(reference[column], errors="coerce")
            difference = (left - right).abs()
            tolerance = float(case.get("tolerance", 1e-6))
            if difference.isna().any() or float(difference.max()) > tolerance:
                raise AdjustmentError(f"{column} 超出容差 {tolerance}")
    except (AdjustmentError, OSError, KeyError, ValueError) as exc:
        checks.append(
            {
                "id": f"adjustment.{case_id}.qfq",
                "status": "fail",
                "message": f"复权对账失败：{exc}",
            }
        )
    else:
        checks.append(
            {
                "id": f"adjustment.{case_id}.qfq",
                "status": "pass",
                "message": "qfq 计算与参考结果通过",
            }
        )


def _render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Public benchmark v1",
        "",
        f"状态：**{payload['status']}**",
        "",
        "| Check | Status | Message |",
        "|---|---|---|",
    ]
    for check in payload["checks"]:
        lines.append(f"| `{check['id']}` | {check['status']} | {check['message']} |")
    lines.extend(
        [
            "",
            "本结果由本地材料生成；运行器默认不联网、不写入生产缓存。",
            "",
        ]
    )
    return "\n".join(lines)


def run_benchmark(manifest_path: Path, source_root: Path, output_dir: Path) -> int:
    checks: list[dict[str, str]] = []
    try:
        manifest = _read_json(manifest_path)
        _validate_manifest(manifest)
        _check_financial_cases(manifest, source_root, checks)
        for case in manifest["adjustment_cases"]:
            _check_adjustment_case(case, source_root, checks)
    except BenchmarkInputError as exc:
        checks.append({"id": "input", "status": "fail", "message": str(exc)})
    except (OSError, TypeError, KeyError, ValueError, pd.errors.ParserError) as exc:
        checks.append({"id": "input", "status": "fail", "message": f"输入读取失败：{exc}"})

    has_failures = any(check["status"] == "fail" for check in checks)
    input_failure = any(
        check["status"] == "fail"
        and (
            check["id"] == "input"
            or ".hash" in check["id"]
            or "缺少本地材料" in check["message"]
            or "SHA-256 不匹配" in check["message"]
            or "路径越过 source-dir" in check["message"]
        )
        for check in checks
    )
    status = "fail" if has_failures else "pass"
    payload = {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "manifest": manifest_path.name,
        "checks": checks,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "benchmark-results.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (output_dir / "benchmark-report.md").write_text(
        _render_markdown(payload), encoding="utf-8"
    )
    if input_failure:
        return 2
    return 1 if has_failures else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    return run_benchmark(args.manifest, args.source_dir, args.output_dir)


if __name__ == "__main__":
    raise SystemExit(main())
