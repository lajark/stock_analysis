"""Unit tests for update-check version resolution and download failover."""

from __future__ import annotations

from src.app.update_check import (
    _prioritize_download_urls,
    _release_page_url,
    local_version,
    version_gt,
)

GITEE_EXE = (
    "https://gitee.com/li_nanqi/stock_analysis/releases/download/"
    "v1.3.0/StockAnalysis-Setup-1.3.0.exe"
)
GITHUB_EXE = (
    "https://github.com/lajark/stock_analysis/releases/download/"
    "v1.3.0/StockAnalysis-Setup-1.3.0.exe"
)


def test_local_version_reads_pyproject() -> None:
    version = local_version()
    assert version != "unknown"
    assert all(part.isdigit() for part in version.split("."))


def test_version_gt_treats_unknown_as_zero() -> None:
    assert version_gt("1.3.0", "unknown")
    assert not version_gt("unknown", "1.3.0")


def test_prioritize_download_urls_prefers_gitee(monkeypatch) -> None:
    monkeypatch.delenv("STOCK_ANALYSIS_UPDATE_URLS", raising=False)
    urls = [GITHUB_EXE, GITEE_EXE]
    assert _prioritize_download_urls(urls) == [GITEE_EXE, GITHUB_EXE]


def test_prioritize_keeps_override_order(monkeypatch) -> None:
    monkeypatch.setenv(
        "STOCK_ANALYSIS_UPDATE_URLS", "https://a.example,https://b.example"
    )
    urls = ["https://a.example/x.exe", "https://b.example/x.exe"]
    assert _prioritize_download_urls(urls) == urls


def test_release_page_url_handles_github_and_gitee() -> None:
    assert (
        _release_page_url(
            {"html_url": "https://github.com/x/y/releases/tag/v1"}, "api"
        )
        == "https://github.com/x/y/releases/tag/v1"
    )
    assert (
        _release_page_url(
            {"full_name": "li_nanqi/stock_analysis", "tag_name": "v1.3.0"}, "api"
        )
        == "https://gitee.com/li_nanqi/stock_analysis/releases/tag/v1.3.0"
    )
    assert _release_page_url({}, "https://api.example") is None
