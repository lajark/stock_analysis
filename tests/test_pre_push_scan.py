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


def test_scan_rejects_raw_public_benchmark_assets(tmp_path) -> None:
    raw_pdf = tmp_path / "benchmarks" / "v1" / "source.pdf"
    raw_pdf.parent.mkdir(parents=True)
    raw_pdf.write_bytes(b"%PDF-1.7")

    issues = scan_paths(tmp_path, [raw_pdf.relative_to(tmp_path)])

    assert issues == ["forbidden public asset: benchmarks/v1/source.pdf"]


def test_scan_allows_small_public_manifest_and_svg(tmp_path) -> None:
    manifest = tmp_path / "benchmarks" / "v1" / "manifest.json"
    preview = tmp_path / "docs" / "assets" / "preview.svg"
    preview.parent.mkdir(parents=True)
    manifest.parent.mkdir(parents=True)
    manifest.write_text('{"schema_version":"stock-analysis-benchmark/v1"}', encoding="utf-8")
    preview.write_text("<svg xmlns='http://www.w3.org/2000/svg'/>", encoding="utf-8")

    issues = scan_paths(
        tmp_path,
        [manifest.relative_to(tmp_path), preview.relative_to(tmp_path)],
    )

    assert issues == []


def test_scan_allows_example_configuration_and_public_files(tmp_path) -> None:
    example = tmp_path / ".env.example"
    example.write_text("LLM_API_KEY=your_llm_api_key_here", encoding="utf-8")
    readme = tmp_path / "README.md"
    readme.write_text("No credentials are included.", encoding="utf-8")

    assert scan_paths(tmp_path, [Path(".env.example"), Path("README.md")]) == []


def test_scan_rejects_oversized_public_csv(tmp_path) -> None:
    csv_path = tmp_path / "docs" / "examples" / "large.csv"
    csv_path.parent.mkdir(parents=True)
    csv_path.write_bytes(b"x" * (5 * 1024 * 1024 + 1))

    assert scan_paths(tmp_path, [csv_path.relative_to(tmp_path)]) == [
        "oversized public data: docs/examples/large.csv"
    ]
