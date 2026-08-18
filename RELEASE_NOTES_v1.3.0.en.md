# stock_analysis v1.3.0 — Release Notes

## Highlights

### New Features
- New web-style graphical interface (Liquid Glass) as the default startup window, with four tabs: Analysis / Backtest & Optimization / Settings / Help. The legacy Tkinter window remains available (`python -m src.app.gui`).
- New "Backtest / Parameter Optimization": MA-crossover strategy backtest (A-share transaction cost model, cross-symbol and rolling-window support) and parameter-grid optimization (return/Sharpe and other objectives), with an "adopt parameters" action that links optimized MA periods back to the analysis page.
- New "Help" tab: built-in seven-part usage guide (adjustable font size), "Check for Updates", and one-click download with silent installation of updates (official release sources only).
- Report preview supports both Markdown rendering (default) and plain-text views.
- API settings now include optional fields: MaiRui Licence and Biyingapi AppCode (used by the money-flow cross-validation research scripts).
- Batch analysis supports concurrency, per-item progress, and cancellation; LLM reports stream into the UI.
- CNINFO official disclosure events are now included as an evidence dimension (unknown titles stay neutral; direction is never inferred by the LLM).

### Fixes and Improvements
- Fixed deep-analysis reports being truncated at the output cap with empty body text (deep mode now has its own output limit; verified no longer truncated).
- Fixed backtest/parameter optimization not normalizing stock codes, which made the primary data source unusable (e.g. `002001` now backtests directly).
- Fixed packaged-build data caching being unusable (pyarrow is bundled again): repeat analysis on the same day hits the local cache and reduces cost.
- Improved the structured-change diff: fixed recursive `changes` key nesting and list/tuple comparison noise.
- Raised the LLM output cap so long reports are not truncated.

## Verification
- Test suite: 276 passed (one public-API network test failed only because the local proxy was unreachable; unrelated to code).
- Web GUI interaction accepted in a real environment: analysis, backtest/optimization, help, check for updates, Markdown preview.
- Deep analysis verified (002001/600183) with complete output; bare-code backtest works; packaged cache is active.

## Distribution Files
- `StockAnalysis-Setup-1.3.0.exe`
- `checksums.sha256`
- `release-manifest.json`
- `RELEASE_NOTES_v1.3.0.en.md`
- `RELEASE_NOTES_v1.3.0.zh-CN.md`
- `THIRD_PARTY_NOTICES.md`

## Usage Notes
- A Tushare Token is required for data access; LLM configuration is optional (a deep-analysis model is recommended for deep mode).
- Upgrading preserves the existing settings and user data under `%LOCALAPPDATA%\StockAnalysis\`.
- This tool does not execute trades or connect to broker accounts.
