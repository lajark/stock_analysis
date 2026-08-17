"""Build a final disposition register for the residual official-PDF audit rows.

The reconciliation snapshot deliberately keeps every numerical mismatch.  This
helper adds the evidence-based disposition next to each row so that an open
ledger is not confused with an actionable PDF-extraction task.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

KEYS = ["dataset", "ts_code", "period_end", "field"]


def _require_columns(frame: pd.DataFrame, required: set[str], label: str) -> None:
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"{label} 缺少字段：{', '.join(sorted(missing))}")


def _disposition(row: pd.Series) -> tuple[str, str, str]:
    role = str(row.get("role", ""))
    resolution = str(row.get("resolution", "")).strip()
    if role == "manual_decision":
        decision = str(row.get("manual_decision", "")).strip() or "manual_decision_recorded"
        return decision, "policy_required", "field-level manual decision CSV"
    if role == "provider_revision":
        return "provider_revision_explained", "none", "audit queue provider-revision rule"
    if resolution == "resolved_formula_matches_annualized_provider":
        return "formula_crosscheck_resolved", "none", "official-derived-resolution"
    if resolution == "unresolved_missing_formula":
        return "formula_insufficient_opening_balance", "none", "official-derived-resolution"
    if resolution in {
        "definition_review_nonannualized_candidate",
        "manual_definition_review",
        "manual_eps_share_count_review",
    }:
        return resolution, "policy_required", "official-derived-resolution"
    if role == "formula_coverage":
        return "formula_coverage_explained", "none", "audit queue formula-coverage rule"
    if role == "derived_indicator":
        return "derived_indicator_explained", "none", "audit queue derived-indicator rule"
    if role == "source_unreadable":
        return "source_unreadable", "evidence_required", "audit queue source-unreadable rule"
    return "unclassified", "review_required", "no disposition rule matched"


def build_difference_disposition(
    audit_path: Path,
    derived_path: Path,
    output_dir: Path,
) -> dict[str, object]:
    audit = pd.read_csv(audit_path, dtype=str).fillna("")
    derived = pd.read_csv(derived_path, dtype=str).fillna("")
    _require_columns(audit, set(KEYS) | {"role"}, "审计台账")
    _require_columns(derived, set(KEYS) | {"resolution"}, "派生指标归因")
    if derived.duplicated(KEYS).any():
        raise ValueError("派生指标归因存在重复主键")

    merged = audit.merge(
        derived[KEYS + ["resolution", "formula_version"]],
        on=KEYS,
        how="left",
        validate="one_to_one",
    )
    values = merged.apply(_disposition, axis=1, result_type="expand")
    values.columns = ["disposition", "actionability", "evidence_ref"]
    output = pd.concat([merged, values], axis=1)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "difference-disposition.csv"
    output.to_csv(output_path, index=False, encoding="utf-8-sig")
    manifest = {
        "audit": str(audit_path.resolve()),
        "derived": str(derived_path.resolve()),
        "output": str(output_path.resolve()),
        "row_count": int(len(output)),
        "actionability_counts": {
            str(key): int(value)
            for key, value in output["actionability"].value_counts().to_dict().items()
        },
        "disposition_counts": {
            str(key): int(value)
            for key, value in output["disposition"].value_counts().to_dict().items()
        },
        "unclassified_count": int((output["disposition"] == "unclassified").sum()),
    }
    (output_dir / "disposition-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="生成官方 PDF 残余差异最终处置台账")
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--derived", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            build_difference_disposition(args.audit, args.derived, args.output_dir),
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
