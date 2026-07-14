"""分析历史记录 — 管理历次分析记录和报告索引。"""

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from src.config import get_config


class AnalysisHistory:
    """分析历史记录管理器。

    在 output/ 目录维护 history.json，记录每次分析的元数据。
    支持按股票代码、日期范围、分析模式筛选。
    """

    def __init__(self):
        config = get_config()
        self._history_path = config.output_dir / "history.json"
        self._records: list[dict] = self._load()

    def _load(self) -> list[dict]:
        if self._history_path.exists():
            try:
                return json.loads(self._history_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return []
        return []

    def _save(self) -> None:
        self._history_path.parent.mkdir(parents=True, exist_ok=True)
        self._history_path.write_text(
            json.dumps(self._records, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def add(
        self,
        ticker: str,
        name: str,
        mode: str,
        report_path: str,
        tokens: dict,
        cost: float,
        date: str,
    ) -> None:
        """添加一条分析记录。"""
        self._records.append({
            "id": len(self._records) + 1,
            "ticker": ticker,
            "name": name,
            "mode": mode,
            "date": date,
            "analyzed_at": datetime.now().isoformat(),
            "report_path": report_path,
            "tokens": tokens,
            "cost": cost,
        })
        # 只保留最近 200 条
        if len(self._records) > 200:
            self._records = self._records[-200:]
        self._save()

    def list(
        self,
        ticker: Optional[str] = None,
        limit: int = 20,
    ) -> list[dict]:
        """列出分析记录，按时间倒序。"""
        records = self._records
        if ticker:
            records = [r for r in records if r["ticker"] == ticker]
        return list(reversed(records))[:limit]

    def stats(self) -> dict:
        """统计信息。"""
        if not self._records:
            return {"total": 0, "by_mode": {}, "by_ticker": {}, "total_tokens": 0, "total_cost": 0.0}

        modes = {}
        tickers = {}
        total_tokens = 0
        total_cost = 0.0

        for r in self._records:
            modes[r["mode"]] = modes.get(r["mode"], 0) + 1
            tickers[r["ticker"]] = tickers.get(r["ticker"], 0) + 1
            total_tokens += r.get("tokens", {}).get("total_tokens", 0)
            total_cost += r.get("cost", 0.0)

        return {
            "total": len(self._records),
            "by_mode": dict(sorted(modes.items(), key=lambda x: -x[1])),
            "by_ticker": dict(sorted(tickers.items(), key=lambda x: -x[1])[:10]),
            "total_tokens": total_tokens,
            "total_cost": round(total_cost, 4),
        }