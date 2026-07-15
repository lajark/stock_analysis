# stock_analysis — Personal Stock Analysis Tool
[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-30%20passed-brightgreen.svg)](tests/)
> Local computation first; LLM is only used for report generation. Manually triggered, stops when done.

## Project Overview
`stock_analysis` is a personal stock analysis tool that supports technical analysis, fundamental assessment, valuation analysis, and risk judgment for the A-share market.

**Core Design Principles**:
- All numerical computations are done locally with zero token consumption
- Large language models are only used to convert structured analysis results into natural language reports
- Single LLM call, costing approximately CNY 0.02 per run
- Supports `--no-llm` mode with zero token consumption

## Quick Start
```bash
# 1. Clone the repository
git clone <repo-url>
cd stock_analysis

# 2. Install dependencies
pip install -e .

# 3. Configure API keys
cp .env.example .env
# Edit .env and fill in TUSHARE_TOKEN and LLM_API_KEY

# 4. Run analysis
python -m src.app.cli analyze --ticker 600519 --mode trade
```

## Analysis Modes
| Mode | Command | Purpose | Model | Tokens |
|------|---------|---------|-------|--------|
| `quick` | `--mode quick` | Quick scan | fast | ~3K |
| `deep` | `--mode deep` | Deep analysis + knowledge base | pro | ~6K |
| `value` | `--mode value` | Valuation assessment | fast | ~3K |
| `trade` | `--mode trade` | Trading decision | fast | ~4K |

## Key Features
- **10 Technical Indicators**: MA/MACD/RSI/KDJ/Bollinger Bands/CCI/Williams %R/OBV/Volume Ratio
- **Five-Dimensional Fundamental Score**: Revenue trend/Earnings quality/ROE/Debt solvency/Growth potential
- **Valuation Percentiles**: PE/PB/PS current values + 1/3/5-year historical percentiles
- **Risk Analysis**: Three-dimensional assessment of volatility/max drawdown/liquidity
- **Price Levels**: Support/resistance levels + 3-6 month target price + confidence score
- **3 Scoring Strategies**: Default balanced/Conservative stable/Aggressive growth
- **Knowledge Base**: 5 analytical knowledge bases, retrieved on demand in deep mode
- **Visualization**: Candlestick charts/technical indicator charts/multi-stock comparison charts (Plotly)
- **Multi-Stock Comparison**: Side-by-side comparison of PE/RSI/buy confidence

## Common Commands
```bash
# Trading decision (includes target price and confidence)
python -m src.app.cli analyze --ticker 600519 --mode trade

# Multi-stock comparison
python -m src.app.cli compare --tickers 600519,000858,002837

# Generate candlestick chart
python -m src.app.cli analyze --ticker 600519 --mode trade --chart

# No-LLM mode (JSON output only, zero tokens)
python -m src.app.cli analyze --ticker 600519 --mode quick --no-llm

# View history and statistics
python -m src.app.cli history
python -m src.app.cli cost
```

See also [Common Commands.txt](常用命令.txt)

## Project Structure
```
stock_analysis/
├── config/                      # Configuration files
│   ├── settings.yaml            # Global configuration
│   └── field_mapping.yaml       # Data source field mapping
├── src/
│   ├── config.py                # Configuration loader
│   ├── errors.py                # Unified exception system
│   ├── data/                    # Data layer
│   │   ├── providers/           #   Data sources (Tushare/AkShare)
│   │   ├── cache.py             #   Cache management
│   │   ├── calendar.py          #   Trading calendar
│   │   ├── validators.py        #   Data validation
│   │   └── monitoring.py        #   Call monitoring
│   ├── analysis/                # Analysis layer (pure local computation)
│   │   ├── indicators.py        #   10 technical indicators
│   │   ├── fundamentals.py      #   Fundamental analysis
│   │   ├── valuation.py         #   Valuation analysis
│   │   ├── risk.py              #   Risk analysis
│   │   ├── price_levels.py      #   Price levels / target price
│   │   ├── strategies.py        #   3 strategies
│   │   ├── comparison.py        #   Multi-stock comparison
│   │   └── package.py           #   Analysis package builder
│   ├── reports/                 # Report layer
│   │   ├── llm_client.py        #   LLM client
│   │   ├── knowledge_retriever.py # Knowledge base retriever
│   │   ├── renderer.py          #   Report renderer
│   │   ├── charts.py            #   Visualization charts
│   │   └── prompts/             #   Prompt templates (4 files)
│   └── app/                     # Application entry point
│       ├── cli.py               #   CLI commands
│       └── history.py           #   History records
├── knowledge_base/              # Knowledge base (5 files)
├── tests/                       # Tests (30 tests)
├── data/cache/                  # Data cache
├── output/                      # Analysis output
│   ├── reports/                 # Markdown reports
│   ├── json/                    # Analysis package JSON
│   └── charts/                  # Candlestick chart HTML
└── logs/                        # Runtime logs
```

## Dependencies
| Category | Dependencies | Description |
|----------|-------------|-------------|
| Data Sources | tushare, akshare | A-share market and financial data |
| Data Processing | pandas, numpy, duckdb, pyarrow | Data analysis and caching |
| LLM | openai | OpenAI-compatible API |
| CLI | typer, rich | Command-line interface |
| Configuration | pydantic, pydantic-settings, pyyaml, python-dotenv | Configuration management |
| Reporting | jinja2 | Template rendering |
| Visualization | plotly | Interactive charts |
| Logging | loguru | Log management |
| Testing | pytest, pytest-cov | Unit testing |

## Configuration
### Data Sources
- **Tushare Pro** (recommended): Registration required to obtain Token. Sign up at: https://tushare.pro
- **AkShare** (fallback): No registration required. Automatically falls back when Tushare is unavailable

### LLM
Supports any OpenAI-compatible LLM provider, configured via `.env`:
```bash
# Alibaba Cloud Bailian (default)
LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_MODEL=deepseek-v4-flash
LLM_MODEL_DEEP=deepseek-v4-pro
```

## Testing
```bash
pytest tests/ -v    # 30 tests, 0.2s
```

## License
MIT License. For research and auxiliary analysis purposes only. Does not constitute investment advice.

## Disclaimer
This tool provides research and auxiliary analysis functions only. All analysis results are for reference only. Investment involves risks; exercise caution when entering the market.
