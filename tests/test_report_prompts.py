"""Regression tests for report prompt coverage."""

from __future__ import annotations

from pathlib import Path

PROMPT_DIR = Path(__file__).parents[1] / "src" / "reports" / "prompts"


def test_all_report_modes_require_sentiment_proxy_section() -> None:
    for path in sorted(PROMPT_DIR.glob("*.md")):
        prompt = path.read_text(encoding="utf-8")
        assert "市场行为与情绪代理" in prompt, path.name
        assert "数据截止日" in prompt, path.name
        assert "不得" in prompt, path.name
