# stock_analysis v1.4.0 — Release Notes

## Highlights

### New Features
- Beijing Stock Exchange (BJ) ticker support: recognizes the 4 / 8 / 920 code ranges and an explicit `.BJ` suffix. Both the primary Tushare source and the AkShare fallback can fetch BJ bars; desktop, web, and CLI all accept BJ codes directly (e.g. `430047`, `832566`, `920001`).
- Unified stock-code validation at every entry point: the desktop analysis/backtest/optimization flows, the web analysis API, and the CLI all normalize codes before dispatch (`002001 -> 002001.SZ`, `430047 -> 430047.BJ`). Invalid, empty, or mismatched inputs (e.g. `600519.BJ`, `430047.SH`) are rejected up front with a readable message instead of failing later in data fetching.
- Extended token budget for long reports: the `value` and `deep` modes share a dedicated output cap so long reports are no longer truncated.
- More reliable one-click updates: the installer download tries Gitee first and falls back to GitHub automatically; when a release ships no installer asset, the UI offers the release page for manual download; packaged-build version detection is fixed by bundling `pyproject.toml`.

### Fixes and Improvements
- Fixed silent data-fetch failures caused by a mismatched exchange suffix: the app now clearly reports the correct market suffix.
- Fixed packaged (PyInstaller) builds being unable to read their own version, which broke update checks.
- Tightened the update-install allowlist: every download source must be on the official list, and any foreign source is rejected as a whole.
- Internal cleanup: consolidated valuation rounding logic, cleaned up price-level drawdown estimation, modernized annotations (`Optional[x]` -> `x | None`), and removed unused imports and stray blank lines.

## Verification
- Offline test suite: 326 passed (one public-API network contract test failed only because the local proxy was unreachable; unrelated to code, and excluded by CI by default).
- Ruff clean across `src/` and `tests/`; mypy maintained set: 0 issues.
- BJ ticker validation (`430047.BJ` / `832566` / `920001`, etc.) and entry-point normalization are covered by unit tests.

## Distribution Files
- `StockAnalysis-Setup-1.4.0.exe`
- `checksums.sha256`
- `release-manifest.json`
- `RELEASE_NOTES_v1.4.0.en.md`
- `RELEASE_NOTES_v1.4.0.zh-CN.md`
- `THIRD_PARTY_NOTICES.md`

## Usage Notes
- A Tushare Token is required for data access; LLM configuration is optional (a deep-analysis model is recommended for deep/value modes).
- Upgrading preserves the existing settings and user data under `%LOCALAPPDATA%\StockAnalysis\`.
- This tool does not execute trades or connect to broker accounts.
