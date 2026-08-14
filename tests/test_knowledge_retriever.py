"""Tests for heading-aware knowledge retrieval."""

from src.reports.knowledge_retriever import _extract_section, retrieve


def test_extract_section_supports_top_level_heading() -> None:
    content = """# Strategy with PEG

Introduction.

## Details

Rules.

# Another document

Not included.
"""

    result = _extract_section(content, "Strategy with PEG")

    assert "Introduction." in result
    assert "## Details" in result
    assert "Another document" not in result


def test_value_mode_retrieves_author_strategy() -> None:
    result = retrieve("value")

    assert "个股基本面×趋势双轮动策略" in result
    assert "PEG" in result
