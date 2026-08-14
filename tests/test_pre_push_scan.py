"""Tests for the distribution boundary scanner."""

from pathlib import Path

from scripts.pre_push_scan import scan_paths


def test_scan_rejects_forbidden_path_and_secret_like_content(tmp_path) -> None:
    safe = tmp_path / "README.md"
    safe.write_text("public documentation", encoding="utf-8")
    secret = tmp_path / "config.py"
    secret.write_text("LLM_API_KEY = '" + ("x" * 24) + "'", encoding="utf-8")

    issues = scan_paths(tmp_path, [Path("output/report.md"), Path("config.py")])

    assert issues == ["forbidden path: output/report.md", "secret-like content: config.py"]


def test_scan_allows_example_configuration_and_public_files(tmp_path) -> None:
    example = tmp_path / ".env.example"
    example.write_text("LLM_API_KEY=your_llm_api_key_here", encoding="utf-8")
    readme = tmp_path / "README.md"
    readme.write_text("No credentials are included.", encoding="utf-8")

    assert scan_paths(tmp_path, [Path(".env.example"), Path("README.md")]) == []
