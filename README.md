# stock_analysis — 个人股票分析工具

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-30%20passed-brightgreen.svg)](tests/)

> 本地计算为主，LLM 仅用于报告生成。手动触发，用完即停。

## 项目简介

`stock_analysis` 是一个个人股票分析工具，支持 A 股市场的技术分析、基本面评估、估值分析和风险判断。

**核心设计原则**：
- 所有数值计算在本地完成，零 Token 消耗
- 大模型仅用于将结构化分析结果转化为自然语言报告
- 单次 LLM 调用，成本约 CNY 0.02/次
- 支持 `--no-llm` 模式，完全不消耗 Token

## 快速开始

```bash
# 1. 克隆仓库
git clone <repo-url>
cd stock_analysis

# 2. 安装依赖
pip install -e .

# 3. 配置密钥
cp .env.example .env
# 编辑 .env 填入 TUSHARE_TOKEN 和 LLM_API_KEY

# 4. 运行分析
python -m src.app.cli analyze --ticker 600519 --mode trade
```

## 分析模式

| 模式 | 命令 | 用途 | 模型 | Token |
|------|------|------|------|-------|
| `quick` | `--mode quick` | 快速扫描 | fast | ~3K |
| `deep` | `--mode deep` | 深度分析+知识库 | pro | ~6K |
| `value` | `--mode value` | 价值评估 | fast | ~3K |
| `trade` | `--mode trade` | 交易决策 | fast | ~4K |

## 主要功能

- **10 个技术指标**：MA/MACD/RSI/KDJ/布林带/CCI/威廉/OBV/成交量比率
- **基本面五维度评分**：收入趋势/盈利质量/ROE/偿债/成长性
- **估值分位**：PE/PB/PS 当前值 + 1/3/5 年历史分位
- **风险分析**：波动率/最大回撤/流动性三维评估
- **价格水平**：支撑位/阻力位 + 3-6 个月目标价 + 置信度
- **3 种评分策略**：默认均衡/保守稳健/激进增长
- **知识库**：5 个分析知识库，深度模式按需检索
- **可视化**：K 线图/技术指标图/多股对比图（Plotly）
- **多股票对比**：横向比较 PE/RSI/买入置信度

## 常用命令

```bash
# 交易决策（含目标价位和置信度）
python -m src.app.cli analyze --ticker 600519 --mode trade

# 多股票对比
python -m src.app.cli compare --tickers 600519,000858,002837

# 生成 K 线图
python -m src.app.cli analyze --ticker 600519 --mode trade --chart

# 无 LLM 模式（仅输出 JSON，零 Token）
python -m src.app.cli analyze --ticker 600519 --mode quick --no-llm

# 查看历史和统计
python -m src.app.cli history
python -m src.app.cli cost
```

详见 [常用命令.txt](常用命令.txt)

## 项目结构

```
stock_analysis/
├── config/                      # 配置文件
│   ├── settings.yaml            # 全局配置
│   └── field_mapping.yaml       # 数据源字段映射
├── src/
│   ├── config.py                # 配置加载
│   ├── errors.py                # 统一异常体系
│   ├── data/                    # 数据层
│   │   ├── providers/           #   数据源 (Tushare/AkShare)
│   │   ├── cache.py             #   缓存管理
│   │   ├── calendar.py          #   交易日历
│   │   ├── validators.py        #   数据校验
│   │   └── monitoring.py        #   调用监控
│   ├── analysis/                # 分析层（纯本地计算）
│   │   ├── indicators.py        #   10 个技术指标
│   │   ├── fundamentals.py      #   基本面分析
│   │   ├── valuation.py         #   估值分析
│   │   ├── risk.py              #   风险分析
│   │   ├── price_levels.py      #   价格水平/目标价
│   │   ├── strategies.py        #   3 种策略
│   │   ├── comparison.py        #   多股票对比
│   │   └── package.py           #   分析包构建
│   ├── reports/                 # 报告层
│   │   ├── llm_client.py        #   LLM 客户端
│   │   ├── knowledge_retriever.py # 知识库检索
│   │   ├── renderer.py          #   报告渲染
│   │   ├── charts.py            #   可视化图表
│   │   └── prompts/             #   Prompt 模板 (4 个)
│   └── app/                     # 应用入口
│       ├── cli.py               #   CLI 命令
│       └── history.py           #   历史记录
├── knowledge_base/              # 知识库（5 文件）
├── tests/                       # 测试 (30 tests)
├── data/cache/                  # 数据缓存
├── output/                      # 分析输出
│   ├── reports/                 # Markdown 报告
│   ├── json/                    # 分析包 JSON
│   └── charts/                  # K 线图 HTML
└── logs/                        # 运行日志
```

## 依赖

| 类别 | 依赖 | 说明 |
|------|------|------|
| 数据源 | tushare, akshare | A 股行情和财务数据 |
| 数据处理 | pandas, numpy, duckdb, pyarrow | 数据分析和缓存 |
| LLM | openai | OpenAI 兼容接口 |
| CLI | typer, rich | 命令行界面 |
| 配置 | pydantic, pydantic-settings, pyyaml, python-dotenv | 配置管理 |
| 报告 | jinja2 | 模板渲染 |
| 可视化 | plotly | 交互式图表 |
| 日志 | loguru | 日志管理 |
| 测试 | pytest, pytest-cov | 单元测试 |

## 配置

### 数据源

- **Tushare Pro**（推荐）：需注册获取 Token，注册地址：https://tushare.pro
- **AkShare**（备用）：零注册，Tushare 不可用时自动降级

### LLM

支持任意 OpenAI 兼容接口的 LLM 供应商，通过 `.env` 配置：

```bash
# 阿里云百炼（默认）
LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_MODEL=deepseek-v4-flash
LLM_MODEL_DEEP=deepseek-v4-pro
```

## 测试

```bash
pytest tests/ -v    # 30 tests, 0.2s
```

## 许可证

MIT License. 仅供研究和辅助分析使用，不构成投资建议。

## 免责声明

本工具仅提供研究和辅助分析功能，所有分析结果仅供参考。投资有风险，入市需谨慎。
