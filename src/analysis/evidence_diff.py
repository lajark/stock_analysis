"""结构化 EvidencePackage 的字段级变化比较。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

_IGNORED_PATHS = {
    "run_id",
    "created_at",
    "meta.generated_at",
    "snapshot_ref.run_id",
    "snapshot_ref.descriptor_hash",
    "validation.validated_at",
    "validation.run_id",
    "changes",
}


def compare_evidence_packages(
    previous: Mapping[str, Any],
    current: Mapping[str, Any],
    *,
    max_changes: int = 100,
) -> dict[str, Any]:
    """Compare two structured packages without reading old report text."""
    previous_stock = _stock_code(previous)
    current_stock = _stock_code(current)
    if previous_stock != current_stock:
        return {
            "compatible": False,
            "reason": "仅允许比较同一股票的 EvidencePackage",
            "previous_ticker": previous_stock,
            "current_ticker": current_stock,
            "changes": [],
        }

    changes: list[dict[str, Any]] = []
    _diff_mapping(previous, current, "", changes, max_changes)
    return {
        "compatible": True,
        "ticker": current_stock,
        "previous_run_id": previous.get("run_id"),
        "current_run_id": current.get("run_id"),
        "changed_count": len(changes),
        "summary": "无结构化证据变化" if not changes else f"检测到 {len(changes)} 项结构化证据变化",
        "changes": changes,
    }


def _diff_mapping(
    previous: Any,
    current: Any,
    path: str,
    changes: list[dict[str, Any]],
    max_changes: int,
) -> None:
    if len(changes) >= max_changes or _ignored(path):
        return
    if isinstance(previous, Mapping) and isinstance(current, Mapping):
        for key in sorted(set(previous) | set(current), key=str):
            child = f"{path}.{key}" if path else str(key)
            if key not in previous or key not in current:
                # A key missing on one side must still respect the ignore list:
                # otherwise a stale "changes" key present only in the JSON-loaded
                # previous package would be recorded whole, recursively nesting
                # previous diffs into the current one.
                if not _ignored(child):
                    changes.append(
                        {"path": child, "before": previous.get(key), "after": current.get(key)}
                    )
            else:
                _diff_mapping(previous[key], current[key], child, changes, max_changes)
        return
    if not _values_equal(previous, current):
        changes.append({"path": path, "before": previous, "after": current})


def _values_equal(a: Any, b: Any) -> bool:
    """Compare two leaf values, treating equal list/tuple contents as equal.

    JSON round-trips turn in-memory tuples into lists, so a list-vs-tuple
    difference is an artifact of serialization rather than a real evidence
    change; without this the change table is flooded with spurious rows.
    """
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return list(a) == list(b)
    return a == b


def _ignored(path: str) -> bool:
    return path in _IGNORED_PATHS or any(path.startswith(f"{item}.") for item in _IGNORED_PATHS)


def _stock_code(package: Mapping[str, Any]) -> str:
    stock = package.get("stock")
    return str(stock.get("code", "")) if isinstance(stock, Mapping) else ""
