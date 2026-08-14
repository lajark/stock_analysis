# stock_analysis v1.2.1

`stock_analysis` is a personal A-share stock analysis tool. Local computation comes first; an LLM is used only to turn a compact analysis package into a readable report. Version 1.2.1 fixes the Windows package so the AkShare fallback is available when Tushare cannot provide data. Tushare remains the primary data source.

## Windows users

1. Download `StockAnalysis-Setup-1.2.1.exe` and run the installer.
2. Launch **Stock Analysis** from the Start Menu or the desktop shortcut created by default, then open **API Settings**.
3. Enter your Tushare Token. Add an LLM API key, endpoint, and model only if you want an AI report.
4. Return to **Stock Analysis**, enter a ticker, choose a mode, and click **Start Analysis**. No command line is required for normal GUI use.

The packaged application does not require Python. Settings, cache, logs, and reports are stored under `%LOCALAPPDATA%\StockAnalysis\`; uninstalling the application keeps user data by default.

## Developer quick start

```bash
pip install -e .
cp .env.example .env
# Edit .env and set TUSHARE_TOKEN and LLM_API_KEY

# Desktop UI
python -m src.app.gui

# CLI
python -m src.app.cli analyze --ticker 600519 --mode trade
```

## Analysis modes

| Mode | Purpose | Notes |
|------|---------|-------|
| `quick` | Quick scan | Technical, fundamental, valuation, and risk summary |
| `deep` | Deep analysis | Adds targeted knowledge-base context and deeper LLM interpretation |
| `value` | Valuation assessment | Focuses on valuation, fundamentals, and margin of safety |
| `trade` | Trading decision | Support/resistance, target levels, and confidence |

Disable **Generate AI report** in the desktop UI or pass `--no-llm` to produce the local JSON analysis package with zero LLM token usage.

## Common CLI commands

```bash
python -m src.app.cli analyze --ticker 600519 --mode trade
python -m src.app.cli analyze --ticker 600519 --mode quick --no-llm
python -m src.app.cli analyze --ticker 600519 --mode trade --chart
python -m src.app.cli compare --tickers 600519,000858,002837
python -m src.app.cli history
python -m src.app.cli cost
```

## Project structure

```text
stock_analysis/
├── config/                    # YAML configuration
├── src/
│   ├── app/gui.py             # Tkinter desktop entry point
│   ├── app/service.py         # Shared GUI/CLI analysis service
│   ├── app/cli.py             # CLI entry point
│   ├── data/                  # Tushare/AkShare, cache, and validation
│   ├── analysis/              # Technical, fundamental, valuation, risk analysis
│   ├── reports/               # LLM, knowledge retrieval, and Markdown reports
│   └── runtime_paths.py       # Source/packaged runtime paths
├── knowledge_base/            # Structured Markdown knowledge base
├── packaging/                 # PyInstaller/Inno Setup configuration
├── scripts/                   # Windows build scripts
└── tests/                     # Unit tests
```

Source and usage notes for the strategy knowledge are documented in [knowledge_base/README.md](knowledge_base/README.md). The original personal PDF is kept as a local source document and is not bundled as a runtime resource.

## Windows release build

```powershell
pip install -e ".[build]"
powershell -ExecutionPolicy Bypass -File scripts\build_windows.ps1
python scripts/generate_release_metadata.py --version 1.2.1 --artifact installer\StockAnalysis-Setup-1.2.1.exe --pytest-summary "76 passed" --clean-windows11-smoke true
python scripts/pre_push_scan.py --staged
```

The application directory is written to `dist\StockAnalysis\`; the installer is written to `installer\StockAnalysis-Setup-1.2.1.exe`. Inno Setup 6 is required to build the installer.
The metadata command must be run from a clean, tagged release tree; it writes `checksums.sha256` and `release-manifest.json`.

## Testing

```bash
pytest tests/ -v
```

The current suite covers settings persistence, ticker validation, knowledge retrieval, packaging configuration, release metadata, distribution scanning, and the evidence-first pipeline: 76 tests.

## Distribution and license

- See [DISTRIBUTION_POLICY.md](DISTRIBUTION_POLICY.md) for repository and release boundaries.
- See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for dependency notices included with releases.
- MIT License; see [LICENSE](LICENSE).
- For research and auxiliary analysis only. This tool does not execute real trades and does not constitute investment advice.
