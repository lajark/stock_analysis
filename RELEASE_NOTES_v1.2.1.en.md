# stock_analysis v1.2.1 — Release Notes

## Highlights

- Fixed the Windows package so the AkShare fallback and its required runtime files are included.
- Tushare remains the primary data source. AkShare is used only when Tushare fails or returns no usable data.
- No new product features or analysis modes were added in this patch release.

## Verification

- Test suite: 76 tests passed.
- All four GUI analysis modes completed successfully in the packaged application with LLM generation disabled.
- The Windows installer includes Start Menu and default desktop shortcuts; normal GUI use does not require a command line.

## Distribution files

- `StockAnalysis-Setup-1.2.1.exe`
- `checksums.sha256`
- `release-manifest.json`
- `RELEASE_NOTES_v1.2.1.en.md`
- `RELEASE_NOTES_v1.2.1.zh-CN.md`
- `THIRD_PARTY_NOTICES.md`

## Notes

- A Tushare Token is required for primary data access; LLM settings are optional.
- Existing settings and user data under `%LOCALAPPDATA%\StockAnalysis\` are retained during an upgrade.
- The application does not execute trades or connect to brokerage accounts.
