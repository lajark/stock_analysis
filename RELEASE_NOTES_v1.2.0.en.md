# stock_analysis v1.2.0 — Release Notes

## Highlights

- Added an evidence-first analysis package with data quality, provenance, validation, scenarios, invalidation conditions, and structured change tracking.
- Added a Data Gateway with cache, Tushare primary access, and explicit AkShare fallback boundaries.
- Added deterministic Context Router metadata, fragment IDs, content hashes, and context-size limits before LLM generation.
- Added a default desktop shortcut and Start Menu shortcut to the Windows installer; normal GUI use no longer requires a command line.
- Preserved the four analysis modes, the `--no-llm` path, local-first computation, and single-call LLM cost control.

## Verification

- Test suite: 75 tests passed.
- A Windows installer was rebuilt from the v1.2.0 configuration.
- Static installer checks confirm both Start Menu and default desktop shortcuts.
- A clean Windows 11 25H2 (Build 26200) desktop-context smoke test completed successfully: install and uninstall returned exit code 0, the desktop and Start Menu shortcuts resolved to the installed executable, and launching from the desktop shortcut opened the GUI successfully.

## Distribution files

- `StockAnalysis-Setup-1.2.0.exe`
- `checksums.sha256`
- `release-manifest.json`
- `THIRD_PARTY_NOTICES.md`

## Notes

- A Tushare Token is required for normal data access; LLM settings are optional.
- The application does not execute trades or connect to brokerage accounts.
- Existing CLI workflows remain available for developers and automation.
