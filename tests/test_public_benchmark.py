"""Offline contract tests for the public benchmark runner."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd

from scripts.run_public_benchmark import run_benchmark


def _write(path: Path, content: str | bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
        data = content
    else:
        path.write_text(content, encoding="utf-8")
        data = content.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _write_csv(path: Path, frame: pd.DataFrame) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _prepare_fixture(root: Path) -> dict:
    raw_report = root / "raw_reports" / "600519.SH_2024-12-31_annual_original.pdf"
    report_sha = _write(raw_report, b"public fixture report")

    index = pd.DataFrame(
        [
            {
                "ts_code": "600519.SH",
                "period_end": "2024-12-31",
                "announce_date": "2025-04-02",
                "revision": "original",
                "status": "provided",
                "sha256": report_sha,
                "announcement_source_url": "https://example.invalid/official.pdf",
                "announcement_candidate_count": "1",
                "announcement_id": "fixture-1",
                "local_path": "raw_reports/600519.SH_2024-12-31_annual_original.pdf",
            }
        ]
    )
    _write_csv(root / "prepared" / "official-disclosure-checklist-pit-20260815.csv", index)

    keys = {"ts_code": "600519.SH", "period_end": "2024-12-31"}
    wide = pd.DataFrame(
        [
            {
                **keys,
                "source_file": "raw_reports/600519.SH_2024-12-31_annual_original.pdf",
                "source_sha256": report_sha,
                "parser_version": "cninfo-pdf-financials-v28",
                "status": "provided",
                "missing_fields": "",
                "revenue": "100",
                "net_profit_attributable": "20",
                "eps": "1",
                "roe": "10",
                "total_assets": "1000",
                "shareholders_equity": "500",
                "operating_profit": "25",
                "total_liabilities": "500",
                "operating_cf": "30",
                "investing_cf": "-10",
                "financing_cf": "5",
                "net_profit": "20",
                "roa": "2",
            }
        ]
    )
    snapshot = root / "prepared" / "official-financials-v28"
    _write_csv(snapshot / "official_financials.csv", wide)
    mappings = {
        "income": {
            "revenue": "100",
            "operating_profit": "25",
            "net_profit": "20",
            "eps": "1",
            "net_profit_attributable": "20",
        },
        "balance_sheet": {
            "total_assets": "1000",
            "total_liabilities": "500",
            "shareholders_equity": "500",
        },
        "cashflow": {"operating_cf": "30", "investing_cf": "-10", "financing_cf": "5"},
        "fina_indicator": {"eps": "1", "roe": "10", "roa": "2"},
    }
    source_columns = {
        "income": {
            "revenue": "revenue",
            "operate_profit": "operating_profit",
            "n_income": "net_profit",
            "n_income_attr_p": "net_profit_attributable",
            "basic_eps": "eps",
        },
        "balance_sheet": {
            "total_assets": "total_assets",
            "total_liab": "total_liabilities",
            "total_hldr_eqy_exc_min_int": "shareholders_equity",
        },
        "cashflow": {
            "n_cashflow_act": "operating_cf",
            "n_cashflow_inv_act": "investing_cf",
            "n_cash_flows_fnc_act": "financing_cf",
        },
        "fina_indicator": {"eps": "eps", "roe": "roe", "roa": "roa"},
    }
    for dataset, fields in mappings.items():
        row = {"ts_code": keys["ts_code"], "end_date": keys["period_end"]}
        for source, target in source_columns[dataset].items():
            row[source] = fields[target]
        _write_csv(snapshot / f"{dataset}.csv", pd.DataFrame([row]))
    _write(
        snapshot / "extraction_manifest.json",
        json.dumps({"parser_version": "cninfo-pdf-financials-v28"}),
    )

    raw = pd.DataFrame(
        {
            "ts_code": ["600519.SH", "600519.SH"],
            "trade_date": ["20240101", "20240102"],
            "open": [10.01, 20.0],
            "high": [11.01, 21.0],
            "low": [9.01, 19.0],
            "close": [10.51, 20.5],
            "volume": [100, 200],
        }
    )
    factors = pd.DataFrame(
        {"trade_date": ["20240101", "20240102"], "adj_factor": [1.0, 2.0]}
    )
    reference = raw.copy()
    for column in ("open", "high", "low", "close"):
        reference.loc[0, column] = round(reference.loc[0, column] / 2, 2)
    raw_sha = _write_csv(root / "adjustment_samples/600519.SH/daily_raw.csv", raw)
    factor_sha = _write_csv(root / "adjustment_samples/600519.SH/adj_factor.csv", factors)
    reference_sha = _write_csv(root / "adjustment_samples/600519.SH/daily_qfq.csv", reference)
    return {
        "schema_version": "stock-analysis-benchmark/v1",
        "as_of": "2026-08-15",
        "parser_version": "cninfo-pdf-financials-v28",
        "prepared": {
            "snapshot_dir": "prepared/official-financials-v28",
            "index": "prepared/official-disclosure-checklist-pit-20260815.csv",
        },
        "financial_cases": [
            {
                "ts_code": "600519.SH",
                "periods": ["2024-12-31"],
                "expected": {"revision": "original", "status": "provided"},
                "documents": [
                    {
                        "period_end": "2024-12-31",
                        "announce_date": "2025-04-02",
                        "path": "raw_reports/600519.SH_2024-12-31_annual_original.pdf",
                        "announcement_id": "fixture-1",
                        "source_url": "https://example.invalid/official.pdf",
                        "sha256": report_sha,
                    }
                ],
            }
        ],
        "adjustment_cases": [
            {
                "case_id": "fixture-qfq",
                "ts_code": "600519.SH",
                "raw_path": "adjustment_samples/600519.SH/daily_raw.csv",
                "factor_path": "adjustment_samples/600519.SH/adj_factor.csv",
                "reference_path": "adjustment_samples/600519.SH/daily_qfq.csv",
                "raw_sha256": raw_sha,
                "factor_sha256": factor_sha,
                "reference_sha256": reference_sha,
                "tolerance": 0.01,
                "method_version": "tushare-factor-v1",
            }
        ],
    }


def test_public_benchmark_passes_deterministic_fixture(tmp_path: Path) -> None:
    manifest = _prepare_fixture(tmp_path / "materials")
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    result = run_benchmark(manifest_path, tmp_path / "materials", tmp_path / "result")

    assert result == 0
    payload = json.loads(
        (tmp_path / "result" / "benchmark-results.json").read_text(encoding="utf-8")
    )
    assert payload["status"] == "pass"
    assert all(item["status"] == "pass" for item in payload["checks"])


def test_public_benchmark_returns_input_error_for_hash_mismatch(tmp_path: Path) -> None:
    manifest = _prepare_fixture(tmp_path / "materials")
    manifest["financial_cases"][0]["documents"][0]["sha256"] = "0" * 64
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    result = run_benchmark(manifest_path, tmp_path / "materials", tmp_path / "result")

    assert result == 2
    payload = json.loads(
        (tmp_path / "result" / "benchmark-results.json").read_text(encoding="utf-8")
    )
    assert payload["status"] == "fail"
    assert any(item["id"].startswith("financial.") for item in payload["checks"])


def test_public_benchmark_returns_input_error_for_invalid_schema(tmp_path: Path) -> None:
    manifest = _prepare_fixture(tmp_path / "materials")
    manifest["schema_version"] = "stock-analysis-benchmark/v0"
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    result = run_benchmark(manifest_path, tmp_path / "materials", tmp_path / "result")

    assert result == 2
    payload = json.loads(
        (tmp_path / "result" / "benchmark-results.json").read_text(encoding="utf-8")
    )
    assert payload["checks"][0]["id"] == "input"


def test_public_benchmark_returns_rule_error_for_tolerance_mismatch(tmp_path: Path) -> None:
    manifest = _prepare_fixture(tmp_path / "materials")
    manifest["adjustment_cases"][0]["tolerance"] = 1e-12
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    result = run_benchmark(manifest_path, tmp_path / "materials", tmp_path / "result")

    assert result == 1
    payload = json.loads(
        (tmp_path / "result" / "benchmark-results.json").read_text(encoding="utf-8")
    )
    assert payload["status"] == "fail"
    assert any(item["id"].endswith(".qfq") for item in payload["checks"])


def test_public_benchmark_blocks_future_announcement(tmp_path: Path) -> None:
    manifest = _prepare_fixture(tmp_path / "materials")
    index_path = (
        tmp_path
        / "materials"
        / "prepared"
        / "official-disclosure-checklist-pit-20260815.csv"
    )
    index = pd.read_csv(index_path)
    index.loc[0, "announce_date"] = "2027-01-01"
    index.to_csv(index_path, index=False)
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    result = run_benchmark(manifest_path, tmp_path / "materials", tmp_path / "result")

    assert result == 1
    payload = json.loads(
        (tmp_path / "result" / "benchmark-results.json").read_text(encoding="utf-8")
    )
    assert any("晚于 as_of" in item["message"] for item in payload["checks"])
