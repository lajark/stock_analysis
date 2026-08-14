# stock_analysis v1.2.1 — 更新说明

## 主要更新

- 修复 Windows 安装包遗漏 AkShare 降级数据源及其必要运行文件的问题。
- Tushare 继续作为主数据源；只有 Tushare 请求失败或未返回可用数据时才会降级使用 AkShare。
- 本补丁版本不增加新的产品功能或分析模式。

## 验证情况

- 测试套件：76 项通过。
- 关闭 LLM 生成功能后，安装版图形界面的四种分析模式均已成功完成分析。
- Windows 安装器包含开始菜单和默认桌面快捷方式，日常 GUI 使用无需命令行。

## 分发文件

- `StockAnalysis-Setup-1.2.1.exe`
- `checksums.sha256`
- `release-manifest.json`
- `RELEASE_NOTES_v1.2.1.en.md`
- `RELEASE_NOTES_v1.2.1.zh-CN.md`
- `THIRD_PARTY_NOTICES.md`

## 使用说明

- 主数据访问需要配置 Tushare Token；LLM 配置为可选项。
- 升级安装时会保留 `%LOCALAPPDATA%\StockAnalysis\` 下的现有设置和用户数据。
- 本工具不执行交易，也不连接券商账户。
