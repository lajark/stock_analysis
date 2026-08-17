"""Persistence adapter for backtest and optimization audit records."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from src.analysis.backtest import (
    BacktestResult,
    MultiOptimizationResult,
    OptimizationResult,
    RollingOptimizationResult,
)
from src.analysis.contracts import RunRecord, utc_now
from src.app.run_records import RunRecordStore

BACKTEST_AUDIT_SCHEMA_VERSION = "backtest-run-v1"
BACKTEST_DETERMINISM = "deterministic_no_random_state"
_VERSION_PATTERN = re.compile(r'(?m)^version\s*=\s*"([^"]+)"')


@dataclass(frozen=True)
class BacktestRunRecord:
    """Independent, secret-free audit envelope for a backtest result.

    The existing ``RunRecord`` remains the append-only compatibility wrapper;
    this contract contains only reproducibility metadata and a digest, never
    the raw equity curve or complete historical price series.
    """

    run_id: str
    request_fingerprint: str
    result_kind: Literal["backtest", "optimize"]
    application_version: str
    data_hashes: tuple[str, ...] = ()
    strategy_versions: tuple[str, ...] = ()
    adjustments: tuple[str, ...] = ()
    adjustment_versions: tuple[str, ...] = ()
    result_digest: str = ""
    audit_schema_version: str = BACKTEST_AUDIT_SCHEMA_VERSION
    determinism: str = BACKTEST_DETERMINISM
    random_seed: int | None = None
    created_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if not self.run_id or not self.request_fingerprint:
            raise ValueError("回测审计记录缺少 run_id 或 request_fingerprint")
        if self.result_kind not in {"backtest", "optimize"}:
            raise ValueError("回测审计记录 result_kind 无效")
        if self.result_digest and len(self.result_digest) != 64:
            raise ValueError("回测审计记录 result_digest 无效")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> BacktestRunRecord:
        """Restore and validate a persisted audit envelope."""
        if str(value.get("schema_version", "")) != BACKTEST_AUDIT_SCHEMA_VERSION:
            raise ValueError("不支持的回测审计记录版本")
        return cls(
            run_id=str(value.get("run_id", "")),
            request_fingerprint=str(value.get("request_fingerprint", "")),
            result_kind=str(value.get("result_kind", "")),  # type: ignore[arg-type]
            application_version=str(value.get("application_version", "unknown")),
            data_hashes=tuple(str(item) for item in (value.get("data_hashes") or ())),
            strategy_versions=tuple(
                str(item) for item in (value.get("strategy_versions") or ())
            ),
            adjustments=tuple(str(item) for item in (value.get("adjustments") or ())),
            adjustment_versions=tuple(
                str(item) for item in (value.get("adjustment_versions") or ())
            ),
            result_digest=str(value.get("result_digest", "")),
            determinism=str(value.get("determinism", BACKTEST_DETERMINISM)),
            random_seed=value.get("random_seed"),
            created_at=str(value.get("created_at", "")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.audit_schema_version,
            "run_id": self.run_id,
            "request_fingerprint": self.request_fingerprint,
            "result_kind": self.result_kind,
            "application_version": self.application_version,
            "data_hashes": list(self.data_hashes),
            "strategy_versions": list(self.strategy_versions),
            "adjustments": list(self.adjustments),
            "adjustment_versions": list(self.adjustment_versions),
            "result_digest": self.result_digest,
            "determinism": self.determinism,
            "random_seed": self.random_seed,
            "created_at": self.created_at,
        }


def _collect_strings(value: Any, key: str) -> tuple[str, ...]:
    """Collect nested string metadata without retaining the result payload."""
    values: set[str] = set()
    if isinstance(value, Mapping):
        for name, item in value.items():
            if str(name) == key and item not in (None, ""):
                values.add(str(item))
            values.update(_collect_strings(item, key))
    elif isinstance(value, (list, tuple)):
        for item in value:
            values.update(_collect_strings(item, key))
    return tuple(sorted(values))


def _result_digest(value: Mapping[str, Any]) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _application_version() -> str:
    """Read the project version without requiring an installed package."""
    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    try:
        match = _VERSION_PATTERN.search(pyproject.read_text(encoding="utf-8"))
        return match.group(1) if match else "unknown"
    except OSError:
        return "unknown"


def persist_backtest_run(
    request: Mapping[str, Any],
    result: (
        BacktestResult
        | OptimizationResult
        | RollingOptimizationResult
        | MultiOptimizationResult
    ),
    *,
    store: RunRecordStore | None = None,
) -> RunRecord:
    """Persist metadata and metrics without storing raw price history."""
    run = RunRecord.start(
        {
            **dict(request),
            "run_kind": "backtest",
            "audit_schema_version": BACKTEST_AUDIT_SCHEMA_VERSION,
            "application_version": _application_version(),
            "random_seed": None,
            "determinism": BACKTEST_DETERMINISM,
        }
    )
    if isinstance(
        result, (OptimizationResult, RollingOptimizationResult, MultiOptimizationResult)
    ):
        stage_name = "optimize"
        details = result.to_dict()
    else:
        stage_name = "backtest"
        details = result.to_dict(include_curve=False)
    application_version = _application_version()
    contract = BacktestRunRecord(
        run_id=run.run_id,
        request_fingerprint=run.request_fingerprint,
        result_kind=stage_name,
        application_version=application_version,
        data_hashes=_collect_strings(details, "data_hash"),
        strategy_versions=_collect_strings(details, "strategy_version"),
        adjustments=_collect_strings(details, "adjustment"),
        adjustment_versions=_collect_strings(details, "adjustment_application_version"),
        result_digest=_result_digest(details),
    )
    details = {
        "audit_schema_version": BACKTEST_AUDIT_SCHEMA_VERSION,
        "application_version": application_version,
        "random_seed": None,
        "determinism": BACKTEST_DETERMINISM,
        "backtest_record": contract.to_dict(),
        **details,
    }
    run.complete_stage(stage_name, details=details)
    run.finish()
    (store or RunRecordStore()).save(run)
    return run
