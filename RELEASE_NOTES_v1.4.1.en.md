# stock_analysis v1.4.1 — Release Notes

## Highlights

### Market sentiment is now part of every report
- All four report modes (`quick`, `deep`, `value`, and `trade`) now include a dedicated “Market Behavior and Sentiment Proxy” section instead of keeping sentiment only in the structured analysis package.
- Reports expose the sentiment status, quality, as-of date, method version, and provenance. When evidence is insufficient, the report explicitly says so rather than allowing the LLM to infer a market direction.
- Context routing and prompts now carry the structured sentiment fields to the LLM for explanation only, preserving local-first computation and auditability.

### Stability and release-readiness improvements
- The sentiment output contract is versioned as `market-sentiment-v2`, with stable numeric keys and provenance metadata for future backtests and audits.
- The financial-cache test now derives freshness from the current time, preventing date-dependent false failures.
- CI's maintained test set now includes report-prompt coverage, with release checks for routing, rendering, and prompt consistency.

## Verification
- Offline test suite: 330 passed; 3 network integration tests remain explicitly opt-in.
- Ruff, the maintained mypy set, and the pre-push secret scan passed.
- PyInstaller onedir build passed, producing `dist/StockAnalysis/StockAnalysis.exe`.
- On this Windows 11 host, the `1.4.1` upgrade install, first launch, homepage/API version check, no-LLM quick analysis, silent uninstall, and user-data retention checks all passed; an isolated fresh `STOCK_ANALYSIS_HOME` startup and version-interface check also passed.

## Distribution Files
- `StockAnalysis-Setup-1.4.1.exe` (requires Inno Setup 6)
- `checksums.sha256`
- `release-manifest.json`
- `RELEASE_NOTES_v1.4.1.en.md`
- `RELEASE_NOTES_v1.4.1.zh-CN.md`
- `THIRD_PARTY_NOTICES.md`

## Usage Notes
- A Tushare Token is required for data access; LLM configuration is optional.
- Upgrading preserves existing settings and user data under `%LOCALAPPDATA%\StockAnalysis\`.
- This tool does not execute trades or connect to broker accounts.
