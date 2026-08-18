"""Update check via GitHub/Gitee releases, shared across GUI shells."""

from __future__ import annotations

import json
import os
import re
import urllib.request
from pathlib import Path

# Update check endpoints (GitHub primary, Gitee fallback).
# ``STOCK_ANALYSIS_UPDATE_URLS`` (comma-separated) overrides the release source
# — primarily for testing the update flow against a local/self-hosted mirror.
_DEFAULT_UPDATE_URLS = [
    "https://api.github.com/repos/lajark/stock_analysis/releases/latest",
    "https://gitee.com/api/v5/repos/li_nanqi/stock_analysis/releases/latest",
]


def update_urls() -> list[str]:
    """Return the release URLs to check (env override first, default otherwise)."""
    override = os.environ.get("STOCK_ANALYSIS_UPDATE_URLS", "").strip()
    if override:
        return [part.strip() for part in override.split(",") if part.strip()]
    return list(_DEFAULT_UPDATE_URLS)


def local_version() -> str:
    """Read the project version from pyproject.toml."""
    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    try:
        text = pyproject.read_text(encoding="utf-8")
        match = re.search(r'(?m)^version\s*=\s*"([^"]+)"', text)
        return match.group(1) if match else "unknown"
    except OSError:
        return "unknown"


def _version_tuple(version: str) -> tuple[int, ...]:
    parts: list[int] = []
    for part in str(version).split("."):
        try:
            parts.append(int(part))
        except ValueError:
            parts.append(0)
    return tuple(parts)


def version_gt(remote: str, local: str) -> bool:
    """True when ``remote`` is a newer version than ``local``."""
    return _version_tuple(remote) > _version_tuple(local)


def check_for_updates() -> tuple[str, list[str], str | None]:
    """Query the release APIs; return (local, results, download_url).

    ``download_url`` prefers the Windows installer (``.exe``) release asset so
    the GUI can offer a one-click download-and-install flow; it falls back to
    the release HTML page when no installer asset is published.
    """
    local = local_version()
    results: list[str] = []
    download_url: str | None = None
    for url in update_urls():
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "stock-analysis/1.0", "Accept": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                tag = data.get("tag_name", "").lstrip("v")
                remote_url = data.get("html_url", url)
                if version_gt(tag, local):
                    results.append(f"发现新版本：{tag}（当前：{local}）")
                    if download_url is None:
                        download_url = _installer_asset_url(data) or remote_url
                else:
                    results.append(f"已是最新版本（{local}）")
        except (urllib.error.URLError, json.JSONDecodeError, OSError) as exc:
            results.append(f"检查 {url} 失败：{type(exc).__name__}")
    return local, results, download_url


def _installer_asset_url(release: dict) -> str | None:
    """Return the browser download URL of the first ``.exe`` release asset."""
    for asset in release.get("assets", []) or []:
        name = str(asset.get("name", "")).lower()
        if name.endswith(".exe"):
            url = asset.get("browser_download_url") or asset.get("url")
            if url:
                return str(url)
    return None
