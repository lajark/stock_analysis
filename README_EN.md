# stock_analysis — English mirror

**A local-first, reproducible A-share analysis toolkit where Python computes the facts and LLMs only write the report.**

This file is kept as the stable English entry point for platform mirrors. The canonical English README is [`README.md`](README.md); the Chinese entry point is [`README.zh-CN.md`](README.zh-CN.md).

## Core principles

- **Local-first:** data fetching, caching and numerical analysis run locally.
- **Reproducible:** inputs, as-of dates and configuration are recorded with the structured analysis package.
- **PIT-aware:** report periods and public announcement dates are checked separately; future announcements are blocked.
- **LLM-not-the-calculator:** models summarize compact calculated evidence and never compute key financial or market numbers.

## Quick start

```bash
pip install -e .
cp .env.example .env
# Set TUSHARE_TOKEN. LLM_API_KEY, LLM_BASE_URL and LLM_MODEL are optional.

python -m src.app.webgui.app
python -m src.app.cli analyze --ticker 600519 --mode quick --no-llm
```

The project supports `quick`, `deep`, `value` and `trade` analysis modes, plus research-only backtests and parameter optimization. Optimization results are never promoted automatically.

## Public verification assets

- [Methodology guide](docs/methodology.md)
- [Sanitized example report](docs/examples/sample-report.md)
- [Benchmark v1](benchmarks/README.md)
- [Distribution policy](DISTRIBUTION_POLICY.md)

```bash
python scripts/run_public_benchmark.py \
  --manifest benchmarks/v1/manifest.json \
  --source-dir path/to/local-materials \
  --output-dir .workspace/tmp/public-benchmark
```

The benchmark is offline by default and never downloads raw reports or changes production cache data.

## Windows

The packaged application does not require Python. Configure the Tushare Token in **API Settings**; LLM settings are optional. User data remains under `%LOCALAPPDATA%\\StockAnalysis\\` and is retained by default when uninstalling.

## Limitations and disclaimer

Provider data may be delayed, revised or incomplete. PIT guarantees depend on announcement metadata; missing metadata is reported as degraded quality. Backtests are research tools, not return guarantees. This software is for research and education only, does not execute trades, and does not constitute investment advice.

See [`README.md`](README.md) for the complete English usage guide and testing commands.
