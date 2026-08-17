"""本地分析运行记录的轻量持久化。"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from src.analysis.contracts import RunRecord
from src.config import get_config

# Batch analysis appends records from multiple worker threads to the same
# JSONL file; the lock keeps lines from interleaving inside one process.
_WRITE_LOCK = threading.Lock()


class RunRecordStore:
    """Append-only JSONL store for secret-free run metadata."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (get_config().output_dir / "run_records.jsonl")

    def save(self, record: RunRecord) -> None:
        """Append one completed or failed record."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with _WRITE_LOCK:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")

    def list(self, limit: int = 20) -> list[dict[str, Any]]:
        """Read recent records, ignoring incomplete lines from an interrupted write."""
        if limit <= 0 or not self.path.exists():
            return []
        records: list[dict[str, Any]] = []
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        for line in reversed(lines):
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                records.append(value)
            if len(records) >= limit:
                break
        return records
