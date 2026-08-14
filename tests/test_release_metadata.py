"""Tests for release checksum and traceability metadata generation."""

from pathlib import Path

import pytest

import scripts.generate_release_metadata as release_metadata


def test_create_release_metadata_writes_checksum_and_manifest(tmp_path) -> None:
    artifact = tmp_path / "StockAnalysis-Setup-1.2.0.exe"
    artifact.write_bytes(b"candidate installer")
    output_dir = tmp_path / "installer"

    manifest = release_metadata.create_release_metadata(
        repo=Path(__file__).resolve().parents[1],
        version="1.2.0",
        artifact=artifact,
        output_dir=output_dir,
        pytest_summary="71 passed",
        clean_windows11_smoke="not-run",
        allow_dirty=True,
    )

    assert manifest["artifact"]["file"] == artifact.name
    assert manifest["artifact"]["sha256"]
    assert manifest["release_ready"] is False
    assert (output_dir / "checksums.sha256").read_text(encoding="utf-8").endswith(
        f"  {artifact.name}\n"
    )
    assert (output_dir / "release-manifest.json").exists()


def test_create_release_metadata_rejects_dirty_tree_without_override(monkeypatch, tmp_path) -> None:
    artifact = tmp_path / "installer.exe"
    artifact.write_bytes(b"candidate installer")
    monkeypatch.setattr(
        release_metadata,
        "_git_value",
        lambda repo, *args: " M README.md" if args == ("status", "--porcelain") else "abc123",
    )

    with pytest.raises(RuntimeError, match="uncommitted changes"):
        release_metadata.create_release_metadata(
            repo=Path(__file__).resolve().parents[1],
            version="1.2.0",
            artifact=artifact,
            output_dir=tmp_path / "installer",
            pytest_summary="71 passed",
            clean_windows11_smoke="not-run",
        )
