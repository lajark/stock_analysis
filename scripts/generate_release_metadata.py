"""Generate checksum and traceability metadata for a Windows release artifact."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _git_value(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        check=False,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def create_release_metadata(
    *,
    repo: Path,
    version: str,
    artifact: Path,
    output_dir: Path,
    pytest_summary: str,
    clean_windows11_smoke: str,
    allow_dirty: bool = False,
) -> dict[str, Any]:
    """Write release metadata and return the manifest object.

    A dirty tree is rejected by default. ``allow_dirty`` is intended only for
    local candidate artifacts; the manifest records that state explicitly.
    """
    artifact = artifact.resolve()
    output_dir = output_dir.resolve()
    if not artifact.exists() or not artifact.is_file():
        raise FileNotFoundError(f"Release artifact not found: {artifact}")

    dirty = bool(_git_value(repo, "status", "--porcelain"))
    if dirty and not allow_dirty:
        raise RuntimeError(
            "Working tree has uncommitted changes; commit the release source or use "
            "--allow-dirty only for a local candidate."
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    checksum = _sha256(artifact)
    source_commit = _git_value(repo, "rev-parse", "HEAD")
    generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    expected_tag = f"v{version}"
    tag_at_head = _git_value(repo, "describe", "--exact-match", "--tags", "HEAD")
    manifest: dict[str, Any] = {
        "product": "stock_analysis",
        "version": version,
        "tag": f"v{version}",
        "release_ready": (
            not dirty
            and tag_at_head == expected_tag
            and clean_windows11_smoke == "true"
        ),
        "artifact": {
            "file": artifact.name,
            "size_bytes": artifact.stat().st_size,
            "sha256": checksum,
        },
        "source": {
            "commit": source_commit,
            "tag_at_head": tag_at_head,
            "working_tree_dirty": dirty,
        },
        "build": {
            "generated_at": generated_at,
            "pyinstaller": True,
            "inno_setup": True,
        },
        "verification": {
            "pytest": pytest_summary,
            "clean_windows_11_smoke_test": clean_windows11_smoke,
        },
        "release_notes": {
            "english": f"RELEASE_NOTES_v{version}.en.md",
            "chinese": f"RELEASE_NOTES_v{version}.zh-CN.md",
        },
    }

    checksum_path = output_dir / "checksums.sha256"
    checksum_path.write_text(f"{checksum}  {artifact.name}\n", encoding="utf-8")
    manifest_path = output_dir / "release-manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=Path("installer"))
    parser.add_argument("--pytest-summary", default="not recorded")
    parser.add_argument(
        "--clean-windows11-smoke",
        choices=("true", "false", "not-run"),
        default="not-run",
    )
    parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="Allow a local candidate from an uncommitted tree; never use for public release.",
    )
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    try:
        manifest = create_release_metadata(
            repo=repo,
            version=args.version,
            artifact=args.artifact,
            output_dir=args.output_dir,
            pytest_summary=args.pytest_summary,
            clean_windows11_smoke=args.clean_windows11_smoke,
            allow_dirty=args.allow_dirty,
        )
    except (FileNotFoundError, RuntimeError) as exc:
        parser.error(str(exc))
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
