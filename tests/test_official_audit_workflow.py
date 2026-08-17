"""增量官方财务审计队列与快照合并测试。"""

import json

import pandas as pd

from scripts.build_official_difference_disposition import build_difference_disposition
from scripts.merge_official_financials import merge_snapshots
from scripts.profile_derived_reconciliation import profile_derived_reconciliation
from scripts.validate_official_snapshot import validate_snapshot
from src.data.audit_queue import build_audit_queue


def _write_dataset(directory, dataset: str, rows: list[dict[str, object]]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(directory / f"{dataset}.csv", index=False)


def test_audit_queue_marks_resolved_rows_and_limits_selected_batch(tmp_path) -> None:
    left = tmp_path / "left"
    previous = tmp_path / "previous"
    current = tmp_path / "current"
    _write_dataset(
        left,
        "income",
        [
            {"ts_code": "000001.SZ", "end_date": "2025-03-31", "revenue": 100.0},
            {"ts_code": "000002.SZ", "end_date": "2025-03-31", "revenue": 100.0},
        ],
    )
    _write_dataset(
        previous,
        "income",
        [
            {"ts_code": "000001.SZ", "end_date": "2025-03-31", "revenue": 120.0},
            {"ts_code": "000002.SZ", "end_date": "2025-03-31", "revenue": 120.0},
        ],
    )
    _write_dataset(
        current,
        "income",
        [
            {"ts_code": "000001.SZ", "end_date": "2025-03-31", "revenue": 100.0},
            {"ts_code": "000002.SZ", "end_date": "2025-03-31", "revenue": 130.0},
        ],
    )

    open_rows, selected, resolved, summary = build_audit_queue(
        left,
        current,
        as_of="2025-12-31",
        previous_right_dir=previous,
        datasets=("income",),
        max_per_cluster=1,
        max_candidates=1,
    )

    assert summary.resolved_since_previous == 1
    assert len(resolved) == 1
    assert summary.open_candidates == 1
    assert summary.actionable_open_candidates == 1
    assert len(selected) == 1
    assert open_rows.iloc[0]["state"] == "changed"
    assert open_rows.iloc[0]["ts_code"] == "000002.SZ"


def test_audit_queue_excludes_pdf_review_when_raw_provider_revision_matches(tmp_path) -> None:
    left = tmp_path / "left"
    current = tmp_path / "current"
    _write_dataset(
        left,
        "income",
        [
            {
                "ts_code": "000001.SZ",
                "end_date": "2025-03-31",
                "ann_date": "2025-04-01",
                "update_flag": 0,
                "revenue": 100.0,
            },
            {
                "ts_code": "000001.SZ",
                "end_date": "2025-03-31",
                "ann_date": "2025-05-01",
                "update_flag": 1,
                "revenue": 120.0,
            },
        ],
    )
    _write_dataset(
        current,
        "income",
        [{"ts_code": "000001.SZ", "end_date": "2025-03-31", "revenue": 100.0}],
    )

    open_rows, selected, _, summary = build_audit_queue(
        left,
        current,
        as_of="2025-12-31",
        datasets=("income",),
    )

    assert len(open_rows) == 1
    assert open_rows.iloc[0]["role"] == "provider_revision"
    assert selected.empty
    assert summary.excluded_provider_revision == 1


def test_audit_queue_separates_derived_indicator_review_from_pdf_batch(tmp_path) -> None:
    left = tmp_path / "left"
    current = tmp_path / "current"
    _write_dataset(
        left,
        "fina_indicator",
        [{"ts_code": "000001.SZ", "end_date": "2025-03-31", "roe": 10.0}],
    )
    _write_dataset(
        current,
        "fina_indicator",
        [{"ts_code": "000001.SZ", "end_date": "2025-03-31", "roe": 20.0}],
    )

    open_rows, selected, _, summary = build_audit_queue(
        left,
        current,
        as_of="2025-12-31",
        datasets=("fina_indicator",),
    )

    assert len(open_rows) == 1
    assert open_rows.iloc[0]["role"] == "derived_indicator"
    assert selected.empty
    assert summary.excluded_derived_indicator == 1


def test_audit_queue_applies_manual_decision_without_erasing_difference(tmp_path) -> None:
    left = tmp_path / "left"
    current = tmp_path / "current"
    decisions = tmp_path / "decisions.csv"
    _write_dataset(
        left,
        "income",
        [{"ts_code": "601939.SH", "end_date": "2025-03-31", "revenue": 190.0}],
    )
    _write_dataset(
        current,
        "income",
        [{"ts_code": "601939.SH", "end_date": "2025-03-31", "revenue": 186.0}],
    )
    pd.DataFrame(
        [
            {
                "dataset": "income",
                "ts_code": "601939.SH",
                "period_end": "2025-03-31",
                "field": "revenue",
                "decision": "definition_conflict_unresolved",
                "reason": "IFRS 經營收入与供应商 total_revenue 不证明等价",
            }
        ]
    ).to_csv(decisions, index=False)

    open_rows, selected, _, summary = build_audit_queue(
        left,
        current,
        as_of="2025-12-31",
        datasets=("income",),
        decisions_path=decisions,
    )

    assert len(open_rows) == 1
    assert open_rows.iloc[0]["role"] == "manual_decision"
    assert open_rows.iloc[0]["manual_decision"] == "definition_conflict_unresolved"
    assert selected.empty
    assert summary.excluded_manual_decision == 1
    assert summary.actionable_open_candidates == 0


def test_merge_snapshot_replaces_only_patch_keys(tmp_path) -> None:
    base = tmp_path / "base"
    patch = tmp_path / "patch"
    output = tmp_path / "output"
    _write_dataset(
        base,
        "income",
        [
            {"ts_code": "000001.SZ", "end_date": "2025-03-31", "revenue": 100.0},
            {"ts_code": "000002.SZ", "end_date": "2025-03-31", "revenue": 200.0},
        ],
    )
    _write_dataset(
        patch,
        "income",
        [{"ts_code": "000001.SZ", "end_date": "2025-03-31", "revenue": 110.0}],
    )
    base.mkdir(exist_ok=True)
    patch.mkdir(exist_ok=True)
    pd.DataFrame(
        [
            {
                "ts_code": "000001.SZ",
                "period_end": "2025-03-31",
                "status": "parsed",
                "missing_fields": "",
            },
            {
                "ts_code": "000002.SZ",
                "period_end": "2025-03-31",
                "status": "partial",
                "missing_fields": "eps",
            },
        ]
    ).to_csv(base / "official_financials.csv", index=False)
    pd.DataFrame(
        [
            {
                "ts_code": "000001.SZ",
                "period_end": "2025-03-31",
                "status": "parsed",
                "missing_fields": "",
            }
        ]
    ).to_csv(patch / "official_financials.csv", index=False)
    (base / "extraction_manifest.json").write_text(
        json.dumps({"parser_version": "v1", "report_count": 2}), encoding="utf-8"
    )
    (patch / "extraction_manifest.json").write_text(
        json.dumps({"parser_version": "v2"}), encoding="utf-8"
    )

    result = merge_snapshots(base, [patch], output)
    merged = pd.read_csv(output / "income.csv")
    wide = pd.read_csv(output / "official_financials.csv")

    assert result["parser_version"] == "v2"
    assert len(merged) == 2
    assert merged.loc[merged["ts_code"] == "000001.SZ", "revenue"].iloc[0] == 110.0
    assert wide.loc[wide["ts_code"] == "000002.SZ", "status"].iloc[0] == "partial"


def test_profile_derived_reconciliation_keeps_eps_for_manual_review(tmp_path) -> None:
    audit = tmp_path / "audit.csv"
    ratios = tmp_path / "ratios.csv"
    fina = tmp_path / "fina_indicator.csv"
    output = tmp_path / "output"
    pd.DataFrame(
        [
            {
                "dataset": "fina_indicator",
                "ts_code": "000001.SZ",
                "period_end": "2025-03-31",
                "field": "roe",
                "left_value": 10.0,
                "right_value": 20.0,
            },
            {
                "dataset": "fina_indicator",
                "ts_code": "000001.SZ",
                "period_end": "2025-03-31",
                "field": "eps",
                "left_value": 1.0,
                "right_value": 2.0,
            },
        ]
    ).to_csv(audit, index=False)
    pd.DataFrame(
        [
            {
                "ts_code": "000001.SZ",
                "period_end": "2025-03-31",
                "annualization_factor": 1.0,
                "derived_roa": 1.0,
                "derived_roe": 20.0,
                "formula_version": "test",
            }
        ]
    ).to_csv(ratios, index=False)
    pd.DataFrame(
        [
            {
                "ts_code": "000001.SZ",
                "end_date": "2025-03-31",
                "ann_date": "2025-04-01",
                "eps": 1.0,
                "roe": 10.0,
                "roe_yearly": 20.0,
                "roa": 1.0,
                "roa_yearly": 1.0,
            }
        ]
    ).to_csv(fina, index=False)

    result = profile_derived_reconciliation(audit, ratios, fina, output)

    assert result["resolution_counts"] == {
        "resolved_formula_matches_annualized_provider": 1,
        "manual_eps_share_count_review": 1,
    }
    detail = pd.read_csv(output / "derived-resolution.csv")
    assert detail.loc[detail["field"] == "eps", "resolution"].iloc[0] == (
        "manual_eps_share_count_review"
    )


def test_profile_derived_reconciliation_profiles_roa_against_annualized_provider(
    tmp_path,
) -> None:
    audit = tmp_path / "audit-roa.csv"
    ratios = tmp_path / "ratios-roa.csv"
    fina = tmp_path / "fina-roa.csv"
    output = tmp_path / "output-roa"
    pd.DataFrame(
        [
            {
                "dataset": "fina_indicator",
                "ts_code": "000001.SZ",
                "period_end": "2025-03-31",
                "field": "roa",
                "left_value": 1.0,
                "right_value": None,
            }
        ]
    ).to_csv(audit, index=False)
    pd.DataFrame(
        [
            {
                "ts_code": "000001.SZ",
                "period_end": "2025-03-31",
                "annualization_factor": 4.0,
                "derived_roa": 2.0,
                "derived_roe": 3.0,
                "formula_version": "test",
            }
        ]
    ).to_csv(ratios, index=False)
    pd.DataFrame(
        [
            {
                "ts_code": "000001.SZ",
                "end_date": "2025-03-31",
                "ann_date": "2025-04-01",
                "roa": 0.5,
                "roa_yearly": 2.0,
                "roe_yearly": 3.0,
            }
        ]
    ).to_csv(fina, index=False)

    result = profile_derived_reconciliation(audit, ratios, fina, output)

    assert result["resolution_counts"] == {
        "resolved_formula_matches_annualized_provider": 1,
    }


def test_difference_disposition_register_classifies_all_residual_roles(tmp_path) -> None:
    audit = tmp_path / "audit-disposition.csv"
    derived = tmp_path / "derived-disposition.csv"
    output = tmp_path / "disposition"
    pd.DataFrame(
        [
            {
                "dataset": "income",
                "ts_code": "000001.SZ",
                "period_end": "2025-03-31",
                "field": "revenue",
                "role": "manual_decision",
                "manual_decision": "definition_conflict_unresolved",
            },
            {
                "dataset": "fina_indicator",
                "ts_code": "000001.SZ",
                "period_end": "2025-03-31",
                "field": "roa",
                "role": "formula_coverage",
            },
        ]
    ).to_csv(audit, index=False)
    pd.DataFrame(
        [
            {
                "dataset": "fina_indicator",
                "ts_code": "000001.SZ",
                "period_end": "2025-03-31",
                "field": "roa",
                "resolution": "resolved_formula_matches_annualized_provider",
                "formula_version": "test",
            }
        ]
    ).to_csv(derived, index=False)

    result = build_difference_disposition(audit, derived, output)

    assert result["row_count"] == 2
    assert result["unclassified_count"] == 0
    disposition = pd.read_csv(output / "difference-disposition.csv")
    assert set(disposition["actionability"]) == {"policy_required", "none"}


def test_validate_official_snapshot_checks_lineage_and_normalized_values(tmp_path) -> None:
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    key = {"ts_code": "000001.SZ", "period_end": "2025-03-31"}
    dataset_key = {"ts_code": "000001.SZ", "end_date": "2025-03-31"}
    wide = {
        **key,
        "source_file": "a.pdf",
        "source_sha256": "hash",
        "parser_version": "v-test",
        "status": "parsed",
        "missing_fields": "",
        "revenue": "100",
        "net_profit_attributable": "10",
        "eps": "1",
        "roe": "2",
        "total_assets": "1000",
        "shareholders_equity": "500",
        "operating_profit": "20",
        "total_liabilities": "500",
        "operating_cf": "30",
        "investing_cf": "-10",
        "financing_cf": "5",
        "net_profit": "11",
        "roa": "1",
    }
    pd.DataFrame([wide]).to_csv(snapshot / "official_financials.csv", index=False)
    pd.DataFrame([{
            **dataset_key,
        "ann_date": "",
        "f_ann_date": "",
        "report_type": "1",
        "update_flag": "0",
        "revenue": 100,
        "operate_profit": 20,
        "n_income": 11,
        "n_income_attr_p": 10,
        "basic_eps": 1,
        "net_profit": 11,
    }]).to_csv(snapshot / "income.csv", index=False)
    pd.DataFrame([{
            **dataset_key,
        "ann_date": "",
        "f_ann_date": "",
        "report_type": "1",
        "update_flag": "0",
        "total_assets": 1000,
        "total_liab": 500,
        "total_hldr_eqy_exc_min_int": 500,
    }]).to_csv(snapshot / "balance_sheet.csv", index=False)
    pd.DataFrame([{
            **dataset_key,
        "ann_date": "",
        "f_ann_date": "",
        "report_type": "1",
        "update_flag": "0",
        "n_cashflow_act": 30,
        "n_cashflow_inv_act": -10,
        "n_cash_flows_fnc_act": 5,
    }]).to_csv(snapshot / "cashflow.csv", index=False)
    pd.DataFrame([{
        **dataset_key,
        "ann_date": "",
        "f_ann_date": "",
        "report_type": "1",
        "update_flag": "0",
        "eps": 1,
        "roe": 2,
        "roa": 1,
    }]).to_csv(snapshot / "fina_indicator.csv", index=False)
    pd.DataFrame([{
        **key,
        "name": "测试",
        "market": "",
        "report_type": "q1",
        "announce_date": "",
        "revision": "original",
        "status": "provided",
        "file_type": "pdf",
        "preferred_source": "cninfo",
        "source_url": "",
        "local_path": "archive!a.pdf",
        "sha256": "hash",
        "notes": "",
    }]).to_csv(tmp_path / "index.csv", index=False)
    (snapshot / "extraction_manifest.json").write_text(
        '{"parser_version": "v-test"}\n', encoding="utf-8"
    )

    result = validate_snapshot(snapshot, tmp_path / "index.csv", expected_parser_version="v-test")

    assert result["status"] == "pass"
    assert result["errors"] == []
