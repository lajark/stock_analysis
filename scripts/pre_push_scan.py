"""Scan public Git candidates for forbidden paths and obvious secrets."""

from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path

FORBIDDEN_PARTS = {
    ".workspace",
    "build",
    "dist",
    "installer",
    "logs",
    "output",
    "release",
}
SECRET_FILENAMES = {".env", ".env.local", ".env.production", "id_rsa", "id_ed25519"}
SECRET_PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(
        r"(?i)(?:api[_-]?key|token|password|secret)\s*[:=]\s*['\"]?"
        r"[A-Za-z0-9_\-]{24,}"
    ),
    re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{20,}(?![A-Za-z0-9])"),
)


def candidate_paths(repo: Path, *, staged: bool = False) -> list[Path]:
    """Return staged files or public working-tree candidates."""
    if staged:
        command = ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"]
    else:
        command = ["git", "ls-files", "--cached", "--others", "--exclude-standard"]
    result = subprocess.run(command, cwd=repo, capture_output=True, text=True, check=True)
    return [Path(line) for line in result.stdout.splitlines() if line.strip()]


def scan_paths(repo: Path, paths: list[Path]) -> list[str]:
    """Return path-only issues without exposing matched content."""
    issues: list[str] = []
    for relative in paths:
        normalized = relative.as_posix().strip("/")
        parts = set(normalized.lower().split("/"))
        filename = relative.name.lower()
        if parts & FORBIDDEN_PARTS:
            issues.append(f"forbidden path: {normalized}")
            continue
        if filename in SECRET_FILENAMES or filename.endswith((".pem", ".key", ".p12", ".pfx")):
            issues.append(f"secret-like filename: {normalized}")
            continue

        path = repo / relative
        if not path.is_file():
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for pattern in SECRET_PATTERNS:
            if pattern.search(content):
                issues.append(f"secret-like content: {normalized}")
                break
    return issues


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--staged",
        action="store_true",
        help="Scan only staged files; default scans tracked and non-ignored working-tree files.",
    )
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    issues = scan_paths(repo, candidate_paths(repo, staged=args.staged))
    if issues:
        print("Pre-push scan failed:")
        for issue in issues:
            print(f"- {issue}")
        return 1
    print("Pre-push scan passed: no forbidden paths or obvious secrets found.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
