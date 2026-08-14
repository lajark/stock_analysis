# stock_analysis v1.1.0

个人股票分析工具：本地计算为主，LLM 仅用于报告生成。v1.1.0 新增极简 Windows 桌面界面和一键安装包，同时保留 CLI。

## Windows 普通用户

1. 下载 `StockAnalysis-Setup-1.1.0.exe` 并双击安装。
2. 启动“股票分析工具”，在“API 设置”填写 Tushare Token。
3. 需要 AI 中文报告时，再填写 LLM API Key、接口地址和模型。
4. 回到“股票分析”，输入股票代码、选择模式并点击“开始分析”。

安装版不要求 Python。设置、缓存、日志和报告默认保存在 `%LOCALAPPDATA%\StockAnalysis\`；卸载不会自动删除用户数据。

## 开发者快速开始

```bash
pip install -e .
cp .env.example .env
# 编辑 .env 填入 TUSHARE_TOKEN 和 LLM_API_KEY

# 启动桌面界面
python -m src.app.gui

# 或使用 CLI
python -m src.app.cli analyze --ticker 600519 --mode trade
```

## 分析模式

| 模式 | 用途 | 说明 |
|------|------|------|
| `quick` | 快速扫描 | 基础技术面、基本面、估值和风险摘要 |
| `deep` | 深度分析 | 增加知识库检索和更深的 LLM 解读 |
| `value` | 价值评估 | 聚焦估值、安全边际和基本面 |
| `trade` | 交易决策 | 支撑/阻力、目标价和置信度 |

关闭界面中的“使用 AI 生成中文报告”或使用 `--no-llm`，即可零 Token 输出本地 JSON 分析包。

## 常用 CLI

```bash
python -m src.app.cli analyze --ticker 600519 --mode trade
python -m src.app.cli analyze --ticker 600519 --mode quick --no-llm
python -m src.app.cli analyze --ticker 600519 --mode trade --chart
python -m src.app.cli compare --tickers 600519,000858,002837
python -m src.app.cli history
python -m src.app.cli cost
```

## 项目结构

```text
stock_analysis/
├── config/                    # YAML 配置
├── src/
│   ├── app/gui.py             # Tkinter 桌面入口
│   ├── app/service.py         # GUI/CLI 共用分析服务
│   ├── app/cli.py             # CLI 入口
│   ├── data/                  # Tushare/AkShare、缓存和校验
│   ├── analysis/              # 技术面、基本面、估值、风险和价格水平
│   ├── reports/               # LLM、知识检索和 Markdown 报告
│   └── runtime_paths.py       # 源码/打包运行路径
├── knowledge_base/            # 结构化 Markdown 知识库
├── packaging/                 # PyInstaller/Inno Setup 配置
├── scripts/                   # Windows 构建脚本
└── tests/                     # 单元测试
```

知识库中的策略 Markdown 来源和使用边界见 [knowledge_base/README.md](knowledge_base/README.md)。原始个人 PDF 不作为运行时资源分发。

## Windows 发布构建

```powershell
pip install -e ".[build]"
powershell -ExecutionPolicy Bypass -File scripts\build_windows.ps1
```

程序目录输出到 `dist\StockAnalysis\`，安装程序输出到 `installer\StockAnalysis-Setup-1.1.0.exe`。构建依赖 Inno Setup 6。

## 测试

```bash
pytest tests/ -v
```

当前测试覆盖配置持久化、股票代码校验、知识库检索和核心指标，共 43 项。

## 分发与许可证

- 分发边界见 [DISTRIBUTION_POLICY.md](DISTRIBUTION_POLICY.md)。
- MIT License，详见 [LICENSE](LICENSE)。
- 本工具仅供研究和辅助分析，不构成投资建议，不执行真实交易。
