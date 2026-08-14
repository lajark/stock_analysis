"""Versioned contracts shared by the analysis layers.

The first contract layer is intentionally small. It adds metadata and
serialization boundaries without changing the existing analysis algorithms or
the report template shape.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

SCHEMA_VERSION = "1.0"
QualityStatus = Literal["ok", "partial", "stale", "invalid"]
ValidationStatus = Literal["pass", "degraded", "block"]
RunOutcome = Literal["running", "success", "failed", "cancelled"]
_SENSITIVE_TEXT = re.compile(
    r"(?i)(api[_-]?key|token|password|secret)\s*[:=]\s*[^\s,;]+"
)


def new_run_id() -> str:
    """Create an opaque identifier for one analysis run."""
    return str(uuid4())


def utc_now() -> str:
    """Return a timezone-aware UTC timestamp for contract metadata."""
    return datetime.now(timezone.utc).isoformat()


def _stable_hash(value: Mapping[str, Any]) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _sanitize_request(request: Mapping[str, Any]) -> dict[str, Any]:
    """Redact secret-like request fields before they enter a RunRecord."""
    sensitive_markers = ("key", "token", "password", "secret")
    safe: dict[str, Any] = {}
    for key, value in request.items():
        if any(marker in str(key).lower() for marker in sensitive_markers):
            safe[str(key)] = "<redacted>"
        else:
            safe[str(key)] = _sanitize_value(value)
    return safe


def _sanitize_value(value: Any) -> Any:
    """Recursively redact secret-like keys in run metadata."""
    if isinstance(value, Mapping):
        return _sanitize_request({str(key): _sanitize_value(item) for key, item in value.items()})
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_sanitize_value(item) for item in value]
    return value


def _sanitize_text(value: str) -> str:
    return _SENSITIVE_TEXT.sub(r"\1=<redacted>", value)[:500]


@dataclass(frozen=True)
class DatasetDescriptor:
    """Metadata for one normalized dataset, never the dataset contents."""

    name: str
    provider: str
    as_of: str
    row_count: int
    quality: QualityStatus = "ok"
    cache_status: str = "unknown"
    adjustment: str | None = None
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "provider": self.provider,
            "as_of": self.as_of,
            "row_count": self.row_count,
            "quality": self.quality,
            "cache_status": self.cache_status,
            "adjustment": self.adjustment,
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class DataSnapshot:
    """Describe the data available to a deterministic analysis run."""

    run_id: str
    ticker: str
    requested_date: str
    effective_trade_date: str
    stock: dict[str, Any]
    datasets: dict[str, DatasetDescriptor]
    quality: QualityStatus = "ok"
    missing_datasets: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    schema_version: str = SCHEMA_VERSION
    created_at: str = field(default_factory=utc_now)

    @property
    def descriptor_hash(self) -> str:
        """Hash metadata only; raw market data is not serialized here."""
        return _stable_hash(
            {
                "schema_version": self.schema_version,
                "ticker": self.ticker,
                "requested_date": self.requested_date,
                "effective_trade_date": self.effective_trade_date,
                "stock": self.stock,
                "datasets": {
                    name: descriptor.to_dict() for name, descriptor in self.datasets.items()
                },
                "quality": self.quality,
                "missing_datasets": list(self.missing_datasets),
                "warnings": list(self.warnings),
            }
        )

    def reference(self) -> dict[str, Any]:
        """Return the safe reference embedded in an EvidencePackage."""
        return {
            "run_id": self.run_id,
            "ticker": self.ticker,
            "effective_trade_date": self.effective_trade_date,
            "descriptor_hash": self.descriptor_hash,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "request": {
                "ticker": self.ticker,
                "requested_date": self.requested_date,
            },
            "effective_trade_date": self.effective_trade_date,
            "stock": dict(self.stock),
            "datasets": {
                name: descriptor.to_dict() for name, descriptor in self.datasets.items()
            },
            "quality": {
                "overall": self.quality,
                "missing_datasets": list(self.missing_datasets),
                "warnings": list(self.warnings),
            },
            "descriptor_hash": self.descriptor_hash,
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class EvidencePackage:
    """Versioned envelope around the existing flat analysis package."""

    legacy: dict[str, Any]
    run_id: str
    snapshot_ref: dict[str, Any] | None = None
    quality: QualityStatus = "ok"
    data_gaps: tuple[str, ...] = ()
    data_warnings: tuple[str, ...] = ()
    schema_version: str = SCHEMA_VERSION
    created_at: str = field(default_factory=utc_now)

    @classmethod
    def from_legacy(
        cls,
        package: Mapping[str, Any],
        *,
        run_id: str | None = None,
        snapshot_ref: Mapping[str, Any] | None = None,
        quality: QualityStatus = "ok",
        data_gaps: tuple[str, ...] = (),
        data_warnings: tuple[str, ...] = (),
    ) -> EvidencePackage:
        """Wrap an existing package without changing its business fields."""
        return cls(
            legacy=dict(package),
            run_id=run_id or new_run_id(),
            snapshot_ref=dict(snapshot_ref) if snapshot_ref else None,
            quality=quality,
            data_gaps=data_gaps,
            data_warnings=data_warnings,
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> EvidencePackage:
        metadata_fields = {
            "schema_version",
            "run_id",
            "snapshot_ref",
            "quality",
            "data_gaps",
            "data_warnings",
            "created_at",
        }
        legacy = {key: item for key, item in value.items() if key not in metadata_fields}
        return cls(
            legacy=legacy,
            run_id=str(value.get("run_id") or new_run_id()),
            snapshot_ref=value.get("snapshot_ref"),
            quality=value.get("quality", "ok"),
            data_gaps=tuple(value.get("data_gaps") or ()),
            data_warnings=tuple(value.get("data_warnings") or ()),
            schema_version=str(value.get("schema_version", SCHEMA_VERSION)),
            created_at=str(value.get("created_at", utc_now())),
        )

    def to_dict(self) -> dict[str, Any]:
        """Flatten metadata beside legacy fields for report compatibility."""
        package = dict(self.legacy)
        package.update(
            {
                "schema_version": self.schema_version,
                "run_id": self.run_id,
                "snapshot_ref": dict(self.snapshot_ref) if self.snapshot_ref else None,
                "quality": self.quality,
                "data_gaps": list(self.data_gaps),
                "data_warnings": list(self.data_warnings),
                "created_at": self.created_at,
            }
        )
        return package


@dataclass(frozen=True)
class ValidationCheck:
    """One deterministic validation result."""

    check_id: str
    status: Literal["pass", "warn", "fail"]
    message: str
    affected_dimensions: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.check_id,
            "status": self.status,
            "message": self.message,
            "affected_dimensions": list(self.affected_dimensions),
        }


@dataclass(frozen=True)
class ValidationResult:
    """Deterministic gate result used before LLM generation."""

    run_id: str
    status: ValidationStatus
    allow_llm: bool
    confidence_cap: int
    checks: tuple[ValidationCheck, ...] = ()
    warnings: tuple[str, ...] = ()
    blocking_reasons: tuple[str, ...] = ()
    schema_version: str = SCHEMA_VERSION
    validated_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "status": self.status,
            "allow_llm": self.allow_llm,
            "confidence_cap": self.confidence_cap,
            "checks": [check.to_dict() for check in self.checks],
            "warnings": list(self.warnings),
            "blocking_reasons": list(self.blocking_reasons),
            "validated_at": self.validated_at,
        }


@dataclass
class RunRecord:
    """Secret-free lifecycle record persisted by the application layer."""

    run_id: str
    request: dict[str, Any]
    request_fingerprint: str
    stages: dict[str, dict[str, Any]] = field(default_factory=dict)
    outcome: RunOutcome = "running"
    error_type: str | None = None
    safe_message: str | None = None
    schema_version: str = SCHEMA_VERSION
    started_at: str = field(default_factory=utc_now)
    finished_at: str | None = None

    @classmethod
    def start(cls, request: Mapping[str, Any], *, run_id: str | None = None) -> RunRecord:
        safe_request = _sanitize_request(request)
        return cls(
            run_id=run_id or new_run_id(),
            request=safe_request,
            request_fingerprint=_stable_hash(safe_request),
        )

    def complete_stage(
        self,
        name: str,
        *,
        elapsed_ms: int | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        stage: dict[str, Any] = {"status": "completed", "elapsed_ms": elapsed_ms}
        if details:
            stage.update(_sanitize_value(details))
        self.stages[name] = stage

    def fail(self, error_type: str, safe_message: str) -> None:
        self.outcome = "failed"
        self.error_type = error_type
        self.safe_message = _sanitize_text(safe_message)
        self.finished_at = utc_now()

    def finish(self, outcome: Literal["success", "cancelled"] = "success") -> None:
        self.outcome = outcome
        self.finished_at = utc_now()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "request": dict(self.request),
            "request_fingerprint": self.request_fingerprint,
            "stages": {name: dict(value) for name, value in self.stages.items()},
            "outcome": {
                "status": self.outcome,
                "error_type": self.error_type,
                "safe_message": self.safe_message,
            },
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }
