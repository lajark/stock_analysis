"""Enrich the official-report checklist with CNINFO publication metadata.

The supplied PDF archive is named by reporting period, not publication date.
This helper queries CNINFO announcement metadata only, matches the four report
types by year/title, and keeps ambiguous or missing matches explicit.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import re
import sys
from pathlib import Path
from typing import Any

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.data.cninfo import CninfoAnnouncementClient  # noqa: E402

REPORT_TITLE_PATTERNS = {
    "annual": ("{year}年年度报告", "{year}年度报告"),
    "q1": ("{year}年第一季度报告", "{year}年一季度报告"),
    "semiannual": (
        "{year}年半年度报告",
        "{year}半年度报告",
        "{year}年中期报告",
        "{year}中期报告",
        "{year}年半年报",
        "{year}半年报",
    ),
    "q3": ("{year}年第三季度报告", "{year}年三季度报告"),
}
EXCLUDED_TITLE_MARKERS = (
    "摘要",
    "英文",
    "English",
    "ESG",
    "环境、社会及治理",
    "H股",
    "网上说明会",
    "关于披露",
    "董事会决议",
    "监事会",
    "审核意见",
    "业绩说明会",
    "监管工作函",
    "回复公告",
    "风险提示",
    "财务报表",
    "财务报告",
    "资金占用",
    "业绩预告",
    "专项报告",
)


def _fetch_one(
    task: tuple[str, str, int, int, int],
) -> tuple[str, pd.DataFrame | None, str | None]:
    code, start_date, page_size, max_pages, timeout = task
    try:
        client = CninfoAnnouncementClient(
            page_size=page_size,
            max_pages=max_pages,
            timeout=timeout,
        )
        return code, client.fetch_announcements(code, start_date, "2025-12-31"), None
    except Exception as exc:  # noqa: BLE001 - one security must not abort the batch
        return code, None, f"{type(exc).__name__}: {exc}"


def _match_report(row: pd.Series, announcements: pd.DataFrame) -> dict[str, Any]:
    period = pd.Timestamp(row["period_end"])
    patterns = tuple(
        pattern.format(year=period.year) for pattern in REPORT_TITLE_PATTERNS[row["report_type"]]
    )
    title_series = announcements["title"].astype(str)
    title_mask = title_series.apply(lambda title: any(pattern in title for pattern in patterns))
    if row["report_type"] == "semiannual":
        generic_half_year = title_series.str.contains("半年报", regex=False, na=False)
        published_year = pd.to_datetime(
            announcements["published_at"], errors="coerce"
        ).dt.year
        title_mask |= generic_half_year & published_year.eq(period.year)
    candidates = announcements.loc[title_mask].copy()
    candidates = candidates.loc[
        ~candidates["title"].astype(str).apply(
            lambda title: any(marker in title for marker in EXCLUDED_TITLE_MARKERS)
        )
    ]
    total_candidate_count = len(candidates)

    # CNINFO may return a parent company's report together with a subsidiary's
    # report (for example, 中国平安 and 平安银行). Prefer a title that starts
    # with the issuer name immediately before the report period; otherwise keep
    # the original chronological selection rule.
    issuer_name = re.sub(r"\s+", "", str(row.get("name", "")))
    preferred = candidates
    issuer_preferred = False
    if issuer_name and len(candidates) > 1:
        def issuer_score(title: str) -> int:
            compact_title = re.sub(r"\s+", "", title)
            if issuer_name not in compact_title:
                return 0
            positions = [compact_title.find(pattern.replace(" ", "")) for pattern in patterns]
            positions = [position for position in positions if position >= 0]
            if not positions:
                return 0
            prefix = compact_title[: min(positions)]
            if prefix == issuer_name:
                return 3
            if prefix.startswith(issuer_name) and not any(
                separator in prefix for separator in (":", "：")
            ):
                return 2
            return 1

        scores = candidates["title"].astype(str).map(issuer_score)
        max_score = int(scores.max()) if len(scores) else 0
        if max_score > 0:
            preferred = candidates.loc[scores == max_score]
            issuer_preferred = len(preferred) < len(candidates)
    candidates = preferred.sort_values(["published_at", "item_id"])
    result: dict[str, Any] = {
        "announce_date": "",
        "announcement_title": "",
        "announcement_id": "",
        "announcement_source_url": "",
        "announcement_match": "missing",
        "announcement_candidate_count": total_candidate_count,
    }
    if len(candidates) == 1:
        result["announcement_match"] = (
            "issuer_preferred_candidate" if issuer_preferred else "exact"
        )
    elif len(candidates) > 1:
        result["announcement_match"] = "earliest_original_candidate"
    else:
        return result
    selected = candidates.iloc[0]
    result.update(
        {
            "announce_date": pd.Timestamp(selected["published_at"]).strftime("%Y-%m-%d"),
            "announcement_title": str(selected["title"]),
            "announcement_id": str(selected["announcement_id"]),
            "announcement_source_url": str(selected["source_url"]),
        }
    )
    return result


def enrich(
    index_path: Path,
    output_path: Path,
    raw_output_path: Path,
    manifest_path: Path,
    *,
    workers: int = 4,
    page_size: int = 30,
    max_pages: int = 20,
    timeout: int = 30,
) -> dict[str, Any]:
    index = pd.read_csv(index_path, dtype=str).fillna("")
    required = {"ts_code", "period_end", "report_type", "status"}
    missing = required - set(index.columns)
    if missing:
        raise ValueError(f"清单缺少字段：{', '.join(sorted(missing))}")

    codes = sorted(index.loc[index["status"] == "provided", "ts_code"].unique())
    tasks = [(code, "2023-01-01", page_size, max_pages, timeout) for code in codes]
    fetched: dict[str, pd.DataFrame] = {}
    errors: dict[str, str] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = [executor.submit(_fetch_one, task) for task in tasks]
        for future in concurrent.futures.as_completed(futures):
            code, frame, error = future.result()
            if frame is not None:
                fetched[code] = frame
            if error:
                errors[code] = error
            print(f"fetched {len(fetched) + len(errors)}/{len(tasks)} securities", flush=True)

    raw_frames = []
    for code, frame in fetched.items():
        raw = frame.copy()
        raw.insert(0, "ts_code", code)
        raw_frames.append(raw)
    raw = pd.concat(raw_frames, ignore_index=True) if raw_frames else pd.DataFrame()
    raw_output_path.parent.mkdir(parents=True, exist_ok=True)
    raw.to_csv(raw_output_path, index=False, encoding="utf-8-sig")

    enriched = index.copy()
    metadata_columns = [
        "announce_date",
        "announcement_title",
        "announcement_id",
        "announcement_source_url",
        "announcement_match",
        "announcement_candidate_count",
    ]
    for column in metadata_columns:
        enriched[column] = ""
    for row_index, row in enriched.iterrows():
        if row["status"] != "provided":
            enriched.loc[row_index, "announcement_match"] = "structural_missing"
            continue
        announcements = fetched.get(row["ts_code"])
        if announcements is None:
            enriched.loc[row_index, "announcement_match"] = "fetch_error"
            continue
        matched = _match_report(row, announcements)
        for key, value in matched.items():
            enriched.loc[row_index, key] = value
    output_path.parent.mkdir(parents=True, exist_ok=True)
    enriched.to_csv(output_path, index=False, encoding="utf-8-sig")
    match_counts = enriched["announcement_match"].value_counts().to_dict()
    manifest = {
        "source": "cninfo",
        "index": str(index_path.resolve()),
        "output": str(output_path.resolve()),
        "raw_announcements": str(raw_output_path.resolve()),
        "security_count": len(codes),
        "fetched_security_count": len(fetched),
        "fetch_errors": errors,
        "match_counts": match_counts,
        "query": {
            "start_date": "2023-01-01",
            "end_date": "2025-12-31",
            "page_size": page_size,
            "max_pages": max_pages,
        },
        "matching": {
            "report_title_patterns": REPORT_TITLE_PATTERNS,
            "excluded_title_markers": EXCLUDED_TITLE_MARKERS,
            "issuer_preference": (
                "同一证券存在多个候选时，优先选择发行人名称紧邻报告年份/类型的主体报告"
            ),
        },
        "pit_note": (
            "公告日期来自 CNINFO 元数据；匹配规则按报告年份/类型和非摘要标题，"
            "重复候选优先发行人主体报告，否则取最早公告，并保留候选数。"
        ),
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="补充 CNINFO 官方报告公告日期")
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--raw-output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--page-size", type=int, default=30)
    parser.add_argument("--max-pages", type=int, default=20)
    parser.add_argument("--timeout", type=int, default=30)
    args = parser.parse_args()
    result = enrich(
        args.index,
        args.output,
        args.raw_output,
        args.manifest,
        workers=args.workers,
        page_size=args.page_size,
        max_pages=args.max_pages,
        timeout=args.timeout,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
