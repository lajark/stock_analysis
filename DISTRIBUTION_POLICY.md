# stock_analysis 项目分发与可见性政策

> 版本：v1.0  
> 生效日期：2026-08-14  
> 适用范围：源码仓库、GitHub、Gitee、Windows 安装包、Release 附件及相关构建流程  
> 核心原则：**最小必要分发、公开仓库只放可公开内容、密钥永不进入版本历史。**

---

## 0. 项目参数

```yaml
repository_visibility: "public"
release_visibility: "public"
primary_remote: "https://github.com/lajark/stock_analysis.git"
mirror_remote: "https://gitee.com/li_nanqi/stock_analysis.git"
workspace_dir: "../.workspace/"
release_dir: "installer/"
policy_scan_command: "python scripts/pre_push_scan.py --staged"
ci_policy_job: "ci-lint-test-build-smoke"
```

说明：

- GitHub 是主公开仓库，Gitee 是镜像。源码、配置、测试、运行资源和知识库必须一致；本项目允许一个已记录的首页文档例外：GitHub 的 `README.md` 使用英文，Gitee 的 `README.md` 使用中文。除该文件外，两端文件集合必须一致。
- `stock_analysis/` 是后续开发与构建的唯一源码目录。
- `../release/stock_analysis/` 是旧发布副本，属于 `LOCAL-ONLY`。完成仓库整合后不得继续在其中手工维护第二套源码。
- `policy_scan_command` 已配置为本地基础扫描；`ci_policy_job` 已配置为 `ci-lint-test-build-smoke`（GitHub Actions，含与本地 `pre_push_scan.py` 等价的扫描步骤）。CI 无远程发布能力，正式 Release 上传仍需另行授权。
- 本文件是项目分发边界的单一事实来源；`.gitignore`、构建脚本和未来 CI 只负责执行本政策。

---

## 1. 核心原则

1. 公开仓库只包含构建、测试、使用和公开协作必需的文件。
2. GitHub 与 Gitee 不采用不同的产品文件白名单，不允许只在其中一个平台补传源码、配置、测试或运行资源。平台首页语言可按 §4 的 README 例外处理。
3. 源码、内部计划、运行数据、构建中间产物和正式 Release 必须分离。
4. API Key、Token、密码、私钥和真实 `.env` 永不进入 Git、Release、日志或 Issue。
5. Tushare、LLM 和股票分析运行结果默认属于用户本地数据。
6. 可稳定重建的 `build/`、`dist/`、`installer/` 产物不进入 Git 历史。
7. 正式 Windows 安装包只通过 Release 附件分发，不作为普通源码文件提交。
8. 来源、版权或授权不明确的知识资料，确认前按 `INTERNAL` 或 `LOCAL-ONLY` 处理。
9. 文件同时命中多个分类时采用更严格级别：`SECRET > LOCAL-ONLY > INTERNAL > PUBLIC`。
10. 不能确认的文件不得因执行 `git add -A` 而默认公开。

---

## 2. 文件分类与可见性矩阵

| 分类 | GitHub/Gitee 公开仓库 | 公开 Release | 本地 |
|---|---:|---:|---:|
| `PUBLIC` | 允许 | 允许 | 允许 |
| `INTERNAL` | 禁止 | 禁止 | 允许 |
| `LOCAL-ONLY` | 禁止 | 禁止 | 允许 |
| `SECRET` | 禁止 | 禁止 | 仅安全存储 |

`INTERNAL` 只有在未来单独使用访问受控的私有协作仓库时才可条件共享；不得因为 Gitee 或 GitHub
支持私有仓库而降低 `SECRET` 分类。

---

## 3. 项目特定分类

### 3.1 PUBLIC｜允许同步到 GitHub 与 Gitee

以下文件在内容通过敏感信息与版权检查后可公开：

- `src/`：正式产品源码。
- `tests/`：不使用真实密钥、账户、持仓或非公开行情的测试代码和合成夹具。
- `config/settings.yaml`、`config/field_mapping.yaml`：不含真实凭据的公共配置。
- `src/reports/prompts/`：项目自有且可公开的 Prompt 模板。
- `knowledge_base/` 下的 Markdown 知识库：项目作者已确认内容为项目自有或作者自有文档的
  结构化派生内容，来源和许可记录见 `knowledge_base/README.md`。
- `README.md`、`README_EN.md`、`常用命令.txt`：公开用户文档。
- `LICENSE`、`THIRD_PARTY_NOTICES.md`、未来的 `CHANGELOG.md`。
- `.env.example`：只能包含字段名、公开接口地址、模型示例和明显的假值。
- `.gitignore`、`pyproject.toml`、构建/Lint/类型检查配置。
- `packaging/`、`scripts/build_windows.ps1`：不嵌入证书、签名密钥和本机绝对路径的发布配置。
- `DISTRIBUTION_POLICY.md`：本政策本身可公开，便于贡献者遵守。
- `THIRD_PARTY_NOTICES.md`、`scripts/generate_release_metadata.py`、`scripts/pre_push_scan.py`：发布附件所需的依赖通知、校验元数据和推送前基础扫描工具。

公开源码中的示例股票代码、公开历史行情测试值不得关联用户真实持仓、交易账户或个人画像。

### 3.2 INTERNAL｜仅内部规划或待确认资料

- `PRD.md`、`TODO.md`、路线图、验收记录和未公开设计草案。
- `AGENTS.md`、`CLAUDE.md`、Agent 交接记录和内部协作规则。
- `定制命令.txt`：包含个人关注股票组合，不属于产品通用文档。
- 内部发布审计记录、事故记录和未公开安全评估。
- 尚未确认来源或许可的新知识资料；确认前不得复制进 `knowledge_base/`。

### 3.3 LOCAL-ONLY｜仅本机或受控工作区

- `../.workspace/`、临时审计克隆、scratch、HANDOFF 和会话状态。
- `../release/stock_analysis/` 旧发布副本及其 `.git/`。
- `build/`、`dist/`、`installer/`、`*.egg-info/`、测试缓存和覆盖率输出。
- `data/cache/`、`output/`、`logs/`、DuckDB/SQLite/Parquet 文件。
- 用户分析历史、报告、图表、股票组合和成本统计。
- `.venv/`、IDE 设置、本机脚本和 `*.local.*` 配置。
- 原始 PDF、原始数据集、一次性导出包和未确认版权的参考资料。
- 工作区根目录的《AI+个股投资策略.pdf》：项目作者自有的来源底稿。其许可不存在障碍，但
  原始版式、特定日期和特定标的内容不是运行依赖，按最小必要分发原则保留在本地。

### 3.4 SECRET｜严禁分发

- `.env`、`.env.local`、`.env.production` 等真实环境配置。
- `TUSHARE_TOKEN`、`LLM_API_KEY` 及任何供应商 Token、密码或 Session。
- 私钥、签名证书私钥、代码签名密码、恢复码和 CI Secret 值。
- 真实账户、券商信息、个人身份信息或受监管数据。

示例配置不得使用曾经有效的密钥作为“假值”。如 Secret 已进入历史，必须先轮换/吊销，再清理
Git 历史与远端制品；仅删除最新版本不构成修复。

---

## 4. GitHub 与 Gitee 同步规则

1. 一个共享产品提交先在本地完成测试与分发检查，再推送到 GitHub 和 Gitee。
2. 两个平台的源码、配置、测试、知识库、许可证和发布说明文件必须一致。允许在共享提交之后增加一个仅修改 `README.md` 的平台文档提交：GitHub 使用英文首页，Gitee 保留中文首页；两端差异必须由 `git diff --name-only` 证明仅为 `README.md`。
3. 正式版本 Tag 可以分别指向各平台的最终首页提交，但两端 Tag 对应的产品代码必须来自同一个共享提交；Tag 差异只能由上述 README 文档提交造成。
4. 不使用网页“Upload files”长期维护平台特有提交；远端紧急修改必须回收到本地 Git 历史并同步镜像。
5. 推送后使用只读远端引用和树清单检查确认两个平台满足上述同步规则。
6. 禁止把整个工作区、`release/` 副本或安装目录直接作为源码推送范围。
7. `git add -A` 前必须检查未跟踪文件；该命令不能替代文件分类。
8. 当前开发仓库尚未配置远端和正式提交，在完成历史/远端整合前不得宣称其内容已同步。

推荐远端命名：

```text
origin  -> GitHub（主仓库）
gitee   -> Gitee（镜像）
```

---

## 5. `.gitignore` 执行规则

`.gitignore` 至少覆盖：

- 所有真实 `.env` 变体，并明确放行 `.env.example`；
- 私钥和证书私钥；
- 内部计划、个人定制命令和 Agent 本机文件；
- 缓存、日志、分析输出、本地数据库和构建产物；
- 测试缓存、覆盖率、虚拟环境、IDE 与系统临时文件。

`.gitignore` 不影响已跟踪文件。若禁发文件已经进入历史，应先评估是否涉及 Secret/版权，再选择
停止跟踪、历史清理和远端制品删除方案。未经用户确认不得直接重写历史或强制推送。

---

## 6. 推送前检查

当前提供本地基础策略扫描器，但尚未配置专业 Secret Scanner 或 CI 守门；推送前仍至少人工完成：

1. `git status --short`：逐个分类暂存和未跟踪文件。
2. `git diff --cached --name-status`：确认仅包含 `PUBLIC` 文件。
3. 检查 `.env`、密钥/证书、本地数据库、日志、报告和构建目录未被跟踪。
4. 对暂存内容运行 Secret Scanner；扫描只报告文件位置，不在日志中回显疑似密钥值。
5. 确认知识库和第三方资产有可再分发依据。
6. 运行项目测试、Ruff 和与改动相关的类型检查。
7. 确认 GitHub/Gitee 目标仓库仍为预期的公开可见性。

已新增 `.github/workflows/ci.yml`（`ci-lint-test-build-smoke`：Ruff/Mypy 维护集门、全量离线测试、PyInstaller 构建烟测、`pre_push_scan.py` 等价扫描），并与本地 `scripts/ci.ps1` 镜像保持等价规则；Tag 构建与 Release 上传分离，远程发布须另行授权。

---

## 7. Windows Release 规则

允许作为公开 Release 附件的内容：

- `StockAnalysis-Setup-<version>.exe`；
- `checksums.sha256`；
- `release-manifest.json`；
- 版本说明、`LICENSE`、`THIRD_PARTY_NOTICES.md`。

发布前必须确认：

1. 安装包由对应源码提交构建，可追溯到 Commit 和版本 Tag。
2. 包内不含 `.env`、真实 API Key、用户报告、缓存或日志。
3. PyInstaller 数据清单只包含运行必需且允许再分发的资源。
4. 安装、首次启动、无 LLM 模式、卸载均在干净 Windows 11 用户环境验证。
5. SHA-256 和 Manifest 由构建流程生成，不手工维护旧哈希。
6. 第三方 Python 依赖和知识资料的许可证/通知完整。

本地 `installer/` 目录仍为 `LOCAL-ONLY` 并由 Git 忽略；把其中经过审核的单个安装包上传为 Release
附件不改变该目录的 Git 分类。

---

## 8. 例外与事故处理

任何扩大分发范围、降低分类或绕过扫描的决定必须记录对象、原因、风险、批准依据、生效时间和
回滚方式。建议记录在 `../.workspace/decisions/`，不得放入公开仓库。

Secret 泄露时：立即轮换/吊销凭据、暂停分发、确认暴露范围、清理历史和 Release/CI 制品、重新
扫描并要求相关副本重新同步。

版权或受限资料误公开时：停止进一步分发，确认许可与影响范围，必要时删除远端文件、Release 和
历史副本；未经确认不得用“仅供学习”声明替代授权。

---

## 9. 当前远端审计基线

审计时间：2026-08-14。审计采用公开匿名克隆、远端引用查询、树清单比较、禁发路径检查和有限的
敏感模式文件名扫描；未配置专业 Secret Scanner，因此不能将结果表述为完整安全扫描。

| 项目 | GitHub | Gitee |
|---|---|---|
| `main` | `e1ebe984ea47161cb8101ce36fe22b5088df7611` | `17329396b5f0ce19a29ce85a593d619db5402e43` |
| 当前文件数 | 52 | 51 |
| 相对 v1.0 差异 | 新增 `README_EN.md` | v1.0 基线 |
| 明显禁发路径 | 未发现 | 未发现 |
| `.env.example` 密钥字段 | 占位值 | 占位值 |

审计结论：

- 两个远端均可匿名克隆，按公开仓库政策执行。
- 源码、测试、公共配置、README、LICENSE 和普通命令说明的公开范围总体合理。
- 未发现 `.env`、日志、缓存、输出、构建目录或安装包被提交。
- GitHub 与 Gitee `main` 不一致，不符合镜像规则。
- 两份 PDF 派生知识库与作者自有原文核对一致；作者身份和公开许可已确认，继续以 Markdown
  形式公开是合适的，原始 PDF 无需进入仓库或安装包。
- Windows GUI、打包脚本、知识库来源记录和 v1.1.0 双语发布说明尚未同步到上述远端；本次同步将按 §4 的 README 例外执行。
- v1.1.0 基线当时没有策略扫描脚本和 CI 第二层守门。
- v1.1.0 基线当时尚缺自动生成的 Manifest、checksum 和第三方许可通知；v1.2.0 已补齐本地生成工具和第三方通知文件。

因此，v1.0.0 远端基线的范围评定为：**主体源码和知识库范围基本合适，但当时尚不满足下一次公开同步/Release 的完整分发要求。** 本次 v1.1.0 推送须先完成本地检查，并按 §4 的 README 语言例外同步。

---

### 9.1 v1.1.0 同步复核

复核时间：2026-08-14。已对 GitHub 和 Gitee 的公开 `main`、版本 Tag、递归文件树和首页语言进行只读核验。

| 项目 | GitHub | Gitee |
|---|---|---|
| `main` | `9764c5807def985a5566b60f3ab40fef417418c7` | `268f8683df6b7d9b8ad22018855e97004deaf2f9` |
| `v1.1.0` 所指产品提交 | `9764c5807def985a5566b60f3ab40fef417418c7` | `268f8683df6b7d9b8ad22018855e97004deaf2f9` |
| 递归文件数 | 65 | 65 |
| `README.md` | English | 中文 |

复核结论：两端产品文件树一致，差异仅为首页 `README.md` 的语言内容；`.env`、密钥、内部规划、缓存、输出、构建目录和安装包均未进入公开 Git 树。`README_EN.md`、中英文 v1.1.0 发布说明、LICENSE、知识库来源说明和分发规则均已在两端保留。该结果符合本政策 §4 的平台首页语言例外。

### 9.2 v1.2.0 发布前收口

- 版本目标：`v1.2.0`。
- 本地已统一版本号、快捷方式说明、中英文发布说明和 Inno Setup 构建版本。
- `THIRD_PARTY_NOTICES.md` 已加入源码和安装包；`scripts/generate_release_metadata.py` 可生成 checksum 与 release manifest，并默认拒绝脏工作区。
- 共享产品提交为 `bbfeb1c848e559871b082ef2521b593685811c3e`，`v1.2.0` Tag 指向产品提交 `dfdbf58944f4af1a01e6b88648381c413601e959`；GitHub 与 Gitee 已完成同步，无需强制推送。
- 历史清理后，所有可达提交的作者/提交者邮箱均为 `noreply` 形式；原含真实邮箱的 `e1ebe98` 已不在任何分支、Tag 或远程引用中。
- GitHub `main` 保持英文 `README.md`；Gitee 仅增加 `README.md` 中文文档提交 `8b5ffc8`，其他产品文件与共享提交一致，符合 §4 的 README 语言例外。
- 已在 Windows 11 25H2（Build 26200）的正常桌面用户上下文完成安装、快捷方式启动和卸载冒烟；Release Manifest 已更新为 `release_ready=true`。沙箱内的 `localappdata` 展开失败经对照确认仅由受限进程的 Shell Folder API 行为造成。

## 10. 最低验收清单

- [x] 仓库与 Release 可见性已定义。
- [x] 项目文件可映射到四级分类。
- [x] Secret、本地运行数据和构建产物有忽略规则。
- [x] PDF 派生知识资料的作者身份、来源和公开许可已确认。
- [x] GitHub 与 Gitee 的产品文件已同步；允许且仅允许 `README.md` 因平台语言不同而产生文档提交差异。
- [ ] 本地开发仓库与旧发布副本已整合为单一 Git 工作流。
- [x] 推送前 Secret/路径扫描命令已实现。
- [x] CI 第二层分发守门已实现（`.github/workflows/ci.yml` `ci-lint-test-build-smoke`，与本地 `scripts/ci.ps1` 等价；无 secrets/上传能力，Tag 发布仍需另行授权）。
- [x] Release Manifest/checksum 生成脚本和第三方许可通知已提供。
- [x] Windows 正式 Release 已在正常 Windows 11 环境完成安装、启动、快捷方式和卸载验证。
