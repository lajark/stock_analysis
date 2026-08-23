# stock_analysis v1.4.1 — 更新说明

## 主要更新

### 市场情绪分析纳入报告体系
- 四种报告模式（`quick`、`deep`、`value`、`trade`）均新增“市场行为与情绪代理”章节，不再只在结构化分析包中保留情绪结果。
- 报告统一展示情绪状态、质量、数据截至日、方法版本与来源说明；缺少有效证据时明确标记“数据不足”，不会让 LLM 自由推断市场方向。
- 上下文路由与提示词同步纳入情绪代理字段，LLM 仅负责解释已计算的结构化结果，保持本地计算优先与可审计性。

### 稳定性与发布前改进
- 情绪输出契约统一为 `market-sentiment-v2`，稳定数值键与来源元数据便于后续回测和审计。
- 财务缓存测试改为相对当前时间生成，避免测试在日期推进后误报过期。
- CI 维护测试集纳入报告提示词覆盖；发布前增加报告路由、渲染和提示词一致性检查。

## 验证情况
- 离线测试套件：330 项通过，3 项联网集成测试按规则保持显式 opt-in。
- Ruff、维护集 mypy、发布前敏感信息扫描均通过。
- PyInstaller onedir 构建通过，产物为 `dist/StockAnalysis/StockAnalysis.exe`。
- 已在本机 Windows 11 目标目录完成 `1.4.1` 升级安装、启动、首页/API 版本检查、无 LLM quick 分析、静默卸载及用户数据保留验证；另以隔离的全新 `STOCK_ANALYSIS_HOME` 配置完成启动与版本接口复核。

## 分发文件
- `StockAnalysis-Setup-1.4.1.exe`（需 Inno Setup 6 构建）
- `checksums.sha256`
- `release-manifest.json`
- `RELEASE_NOTES_v1.4.1.en.md`
- `RELEASE_NOTES_v1.4.1.zh-CN.md`
- `THIRD_PARTY_NOTICES.md`

## 使用说明
- 正常获取数据需配置 Tushare Token；LLM 配置为可选项。
- 升级安装会保留 `%LOCALAPPDATA%\StockAnalysis\` 下的现有设置与用户数据。
- 本工具不执行交易，也不连接券商账户。
