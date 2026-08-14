# stock_analysis v1.1.0 — Release Notes

## Highlights

- Added a minimal Windows desktop UI for API-key configuration and daily analysis.
- Added a shared analysis service so the GUI and CLI use the same validation, caching, local calculations, and report flow.
- Added PyInstaller and Inno Setup configuration for a one-click Windows 11 installer.
- Kept the four existing analysis modes and the `--no-llm` path; no trading or broker integration was added.
- Documented the provenance and distribution boundary of the structured knowledge base.

## Verification

- Test suite: 43 tests passed.
- The Windows package is built from the tagged source commit and should be smoke-tested on a clean Windows 11 machine before publishing the installer as a Release attachment.

## Upgrade note

This is a minor feature release after `v1.0.0`. Existing CLI workflows remain available.
