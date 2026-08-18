"""Update check via GitHub/Gitee releases, shared across GUI shells."""

from __future__ import annotations

import json
import os
import re
import urllib.request
from pathlib import Path

from src.runtime_paths import resource_root

# Update check endpoints (GitHub primary, Gitee fallback).
# ``STOCK_ANALYSIS_UPDATE_URLS`` (comma-separated) overrides the release source
# — primarily for testing the update flow against a local/self-hosted mirror.
_DEFAULT_UPDATE_URLS = [
    "https://api.github.com/repos/lajark/stock_analysis/releases/latest",
    "https://gitee.com/api/v5/repos/li_nanqi/stock_analysis/releases/latest",
]

_HEADERS = {"User-Agent": "stock-analysis/1.0", "Accept": "application/json"}


def update_urls() -> list[str]:
    """Return the release URLs to check (env override first, default otherwise)."""
    override = os.environ.get("STOCK_ANALYSIS_UPDATE_URLS", "").strip()
    if override:
        return [part.strip() for part in override.split(",") if part.strip()]
    return list(_DEFAULT_UPDATE_URLS)


def local_version() -> str:
    """Read the project version from pyproject.toml.

    Source mode finds it next to the package; the PyInstaller bundle ships a
    copy at the resource root (``_internal/pyproject.toml``) so the version
    stays discoverable after packaging.
    """
    candidates = (
        Path(__file__).resolve().parents[2] / "pyproject.toml",
        resource_root() / "pyproject.toml",
    )
    for pyproject in candidates:
        try:
            text = pyproject.read_text(encoding="utf-8")
            match = re.search(r'(?m)^version\s*=\s*"([^"]+)"', text)
            if match:
                return match.group(1)
        except OSError:
            continue
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


def _prioritize_download_urls(urls: list[str]) -> list[str]:
    """Order installer URLs for download: Gitee mirror first, GitHub second.

    Release APIs are checked GitHub-first, but the one-click install flow
    prefers the Gitee asset because GitHub's download CDN is frequently reset
    on mainland networks. An explicit ``STOCK_ANALYSIS_UPDATE_URLS`` override
    keeps the configured source order instead.
    """
    if os.environ.get("STOCK_ANALYSIS_UPDATE_URLS"):
        return list(urls)
    return sorted(urls, key=lambda url: 0 if "gitee.com" in url else 1)


def _release_page_url(release: dict, api_url: str) -> str | None:
    """Return a human-browsable release page (Gitee responses lack ``html_url``)."""
    html = release.get("html_url")
    if html:
        return str(html)
    full_name = release.get("full_name")
    tag = release.get("tag_name")
    if full_name and tag:
        return f"https://gitee.com/{full_name}/releases/tag/{tag}"
    return None


def check_for_updates() -> tuple[str, list[str], list[str], str | None]:
    """Query the release APIs; return (local, results, download_urls, release_page).

    ``download_urls`` holds Windows installer (``.exe``) asset URLs ordered by
    download preference (Gitee first, GitHub second) so the one-click install
    flow can fail over between sources. ``release_page`` is the newest release
    page when no installer asset is published, for manual download.
    """
    local = local_version()
    results: list[str] = []
    download_urls: list[str] = []
    release_page: str | None = None
    for url in update_urls():
        try:
            req = urllib.request.Request(url, headers=_HEADERS)
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                tag = str(data.get("tag_name", "")).lstrip("v")
                if version_gt(tag, local):
                    results.append(f"发现新版本：{tag}（当前：{local}）")
                    asset = _installer_asset_url(data)
                    if asset and asset not in download_urls:
                        download_urls.append(asset)
                    elif release_page is None:
                        release_page = _release_page_url(data, url)
                else:
                    results.append(f"已是最新版本（{local}）")
        except (urllib.error.URLError, json.JSONDecodeError, OSError) as exc:
            results.append(f"检查 {url} 失败：{type(exc).__name__}")
    return local, results, _prioritize_download_urls(download_urls), release_page


def _installer_asset_url(release: dict) -> str | None:
    """Return the browser download URL of the first ``.exe`` release asset."""
    for asset in release.get("assets", []) or []:
        name = str(asset.get("name", "")).lower()
        if name.endswith(".exe"):
            url = asset.get("browser_download_url") or asset.get("url")
            if url:
                return str(url)
    return None
