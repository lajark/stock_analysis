# stock_analysis

**A local-first, reproducible A-share analysis toolkit where Python computes the facts and LLMs only write the report.**

`stock_analysis` is a personal research tool for people who want to inspect the data path behind an A-share analysis. It is not an AI stock-picking service, an automated trading system, or a promise of returns.

![Web GUI preview](docs/assets/webgui-preview.svg)

Windows users: download the existing [GitHub v1.4.1 Release](https://github.com/lajark/stock_analysis/releases/tag/v1.4.1) or [Gitee v1.4.1 Release](https://gitee.com/li_nanqi/stock_analysis/releases/tag/v1.4.1). No new release is created by this maintenance cycle.

## Why this project exists

Most AI stock tools let a model touch the numbers. This project keeps the numerical pipeline local and explicit:

- **Local-first** — data fetching, caching, indicators, ratios, valuation and risk calculations run locally.
- **Reproducible** — the same input data, as-of date and configuration produce the same structured analysis package.
- **PIT-aware** — financial rows are filtered by report period and public announcement date when that metadata is available.
- **LLM-not-the-calculator** — an LLM may summarize and explain a compact analysis package, but it does not calculate financial metrics, technical indicators, adjustments, rankings or scores.

For the Chinese overview, see [README.zh-CN.md](README.zh-CN.md). The longer English copy is kept at [README_EN.md](README_EN.md) for platform mirrors.

## What it can do

- Analyze an A-share ticker in `quick`, `deep`, `value` or `trade` mode.
- Use Tushare as the primary source and AkShare as a controlled fallback.
- Cache source data locally and preserve provider, quality, as-of and adjustment metadata.
- Produce a local JSON analysis package with `--no-llm`, using zero LLM tokens.
- Render a Markdown report from the structured package when an OpenAI-compatible LLM is configured.
- Run research-only backtests and parameter optimization without auto-promoting parameters.
- Validate official disclosure snapshots, PIT selections and price-adjustment calculations offline.

## Data quality and method transparency

The project distinguishes a report period (`end_date`) from the date a record became public (`ann_date`/`f_ann_date`). Future announcements are blocked for an as-of analysis; multiple public revisions are selected deterministically and remain visible in audit metadata. If announcement metadata is absent, the run is explicitly degraded rather than claiming a complete PIT guarantee.

Raw, forward-adjusted (`qfq`) and backward-adjusted (`hfq`) prices are never mixed. Requested adjustment factors are required; missing factors fail the adjustment instead of silently falling back to raw prices. The adjustment implementation version is recorded with the dataset descriptor.

See the bilingual [methodology guide](docs/methodology.md) and the [benchmark guide](benchmarks/README.md) for the exact fields, formulas, limitations and offline verification workflow.

## Quick start

```bash
pip install -e .
cp .env.example .env
# Set TUSHARE_TOKEN. LLM_API_KEY, LLM_BASE_URL and LLM_MODEL are optional.

# Desktop WebView UI
python -m src.app.webgui.app

# CLI, with optional report generation
python -m src.app.cli analyze --ticker 600519 --mode trade
python -m src.app.cli analyze --ticker 600519 --mode quick --no-llm
```

The normal data path is local after the provider response is cached. The application never executes trades or connects to a brokerage account.

## Windows installer

The packaged application does not require Python. Download the installer from the project Release page, open **API Settings**, enter a Tushare Token, and add an LLM endpoint only when a narrative report is wanted. User settings, cache, logs and reports live under `%LOCALAPPDATA%\\StockAnalysis\\`; uninstall keeps user data by default.

Developer build:

```powershell
pip install -e ".[build]"
powershell -ExecutionPolicy Bypass -File scripts\build_windows.ps1
```

## Common CLI commands

```bash
python -m src.app.cli analyze --ticker 600519 --mode trade --chart
python -m src.app.cli compare --tickers 600519,000858,002837
python -m src.app.cli history
python -m src.app.cli cost
```

## Backtest and parameter optimization

Backtest and optimization evaluate a long-only double-MA-cross research strategy (signal at the close of day T, executed at the open of day T+1) against an explicit A-share cost model. They are independent of the analysis pipeline; optimization results are never applied automatically.

```bash
python -m src.app.cli backtest --ticker 600519 --start 2020-01-01 --end 2025-12-31 --fast 20 --slow 60
python -m src.app.cli optimize --ticker 600519 --start 2020-01-01 --end 2025-12-31 --objective robust
python -m src.app.cli optimize-rolling --ticker 600519 --start 2020-01-01 --end 2025-12-31 --train-size 500 --validation-size 200 --test-size 200
python -m src.app.cli optimize-multi --tickers 600519,000858,002837 --start 2020-01-01 --end 2025-12-31
```

Suggested MA values are opt-in and global when enabled in the Web UI: they affect the technical-indicator MA periods for every later ticker, not only the backtest symbol. RSI, MACD, KDJ and the backtest strategy parameters are unchanged. The full behavior is documented in the in-app help and the Chinese overview.

## Example report and benchmark

- [Sanitized example report](docs/examples/sample-report.md) — a fixed, public-data-shaped demonstration with no credentials or user history.
- [Benchmark v1](benchmarks/README.md) — verifies official-report metadata, PIT selection and adjustment calculations from materials that the user supplies locally.

```bash
python scripts/run_public_benchmark.py \
  --manifest benchmarks/v1/manifest.json \
  --source-dir path/to/local-materials \
  --output-dir .workspace/tmp/public-benchmark
```

The benchmark is offline by default. It does not download documents, change the configured provider or write production cache data.

## Project structure

```text
stock_analysis/
├── src/data/          # providers, cache, calendars, PIT and adjustment handling
├── src/analysis/      # indicators, fundamentals, valuation, risk and evidence contracts
├── src/reports/       # compact-package routing, LLM client and Markdown rendering
├── src/app/           # CLI, WebView GUI and shared service
├── knowledge_base/    # structured, redistributable Markdown guidance
├── docs/              # public methodology and sanitized examples
├── benchmarks/        # public manifests only; no raw PDFs or full行情 files
├── scripts/           # build, audit, release and benchmark helpers
└── tests/             # unit and contract coverage
```

## Testing

```bash
python -m pytest tests/ -v -m "not integration"
ruff check src/ scripts/
mypy src/
python scripts/pre_push_scan.py
```

The offline suite covers settings, providers, PIT filtering, adjustment formulas, evidence contracts, report rendering, packaging, release metadata and distribution scanning. Network integration tests are opt-in.

## Limitations and disclaimer

- Provider responses can be delayed, revised, incomplete or unavailable.
- PIT guarantees depend on announcement metadata; missing metadata is reported as degraded quality.
- Backtests are descriptive research tools, not evidence of future returns.
- This software is for research and education only, does not execute trades, and does not constitute investment advice. Verify important facts against the original disclosure before making any decision.

See [DISTRIBUTION_POLICY.md](DISTRIBUTION_POLICY.md), [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) and [LICENSE](LICENSE) for distribution and licensing details.
