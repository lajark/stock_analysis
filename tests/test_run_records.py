"""Tests for local RunRecord persistence."""

from src.analysis.contracts import RunRecord
from src.app.run_records import RunRecordStore


def test_run_record_store_appends_and_reads_secret_free_records(tmp_path) -> None:
    store = RunRecordStore(tmp_path / "run_records.jsonl")
    record = RunRecord.start(
        {"ticker": "600519.SH", "LLM_API_KEY": "secret-value"},
        run_id="run-store",
    )
    record.complete_stage("generate_report", details={"model": "test-model"})
    record.finish()

    store.save(record)
    restored = store.list()

    assert restored[0]["run_id"] == "run-store"
    assert restored[0]["outcome"]["status"] == "success"
    assert restored[0]["request"]["LLM_API_KEY"] == "<redacted>"
    assert "secret-value" not in str(restored)
