# 方法说明

本文说明 `stock_analysis` 如何处理时间口径、复权、数据源降级和 LLM 报告。它描述当前实现，不承诺供应商数据永远完整。

## 1. 分析时点与 PIT

项目把两个日期分开：

- `end_date`：财务报表覆盖的报告期末，例如 `2024-12-31`。
- `ann_date` / `f_ann_date`：该版本被公开披露的日期。两者都存在时优先使用 `f_ann_date`，否则使用 `ann_date`。
- `as_of`：本次分析允许使用的信息截止日。

在给定 `as_of` 时，报告期末晚于截止日的记录会被排除，公告日晚于截止日的记录会被排除。对同一 `ts_code + end_date` 的多个版本，规范化顺序为：最新公告日、`update_flag=1`、合并报表类型优先，最后使用稳定输入顺序打破并列。

公告日期缺失不是“已确认安全”：为了兼容旧缓存，记录仍可被读取，但 validation 会给出 warning 并把运行标为 degraded，报告不得声称完整 PIT 保证。公告日晚于 `as_of` 仍属于未来信息，会阻断关键结论和 LLM 调用。

## 2. 行情与复权

日线必须明确标记 `none`、`qfq` 或 `hfq`，三种口径不能混用。

- `none`：返回供应商原始 OHLC。
- `hfq`：使用供应商因子直接乘到 OHLC。
- `qfq`：使用请求区间最后一个因子作为基准，逐日乘以 `factor / last_factor`。

成交量和成交额保持供应商口径，不用价格因子伪造。复权因子缺失、非正、日期无法对应或计算结果非有限时，流程抛出 adjustment error，禁止静默回退原始价格。当前实现版本为 `tushare-factor-v1`，并写入数据描述符。

## 3. 数据源、缓存与质量

Tushare Pro 是结构化主源，AkShare 是受控降级源。降级只改变取数来源，不让 LLM 补造数字；每个数据集保留 provider、as-of、行数、质量、warning 和复权字段。相同日期、相同股票的请求优先命中本地缓存。

缓存不等于新鲜度证明。交易日校验、供应商状态和数据质量检查通过后，运行才会标记为可用；网络失败、空数据、字段不完整或缺少 PIT 元数据都会在分析包和运行记录中留下可审计信息。

## 4. LLM 边界

LLM 接收的字段限定为精简分析包中的 `meta`（分析日期、数据源、数据截至日）、`stock`、已计算的 `technical`、`valuation`、`fundamental`、`risk`、`sentiment`、`price_levels`、变更摘要和 `validation` 结果，以及必要的知识库上下文。不接收完整历史行情、原始财务明细、复权因子表、供应商响应、用户缓存目录或本机路径。Python 负责：

- 财务过滤、修订选择和比率计算；
- 技术指标、价格水平、估值、风险和情绪代理；
- 复权、排名、置信度和验证门；
- 数据日期、来源、质量和方法版本。

LLM 只负责摘要、解释、风险提示和 Markdown 组织。若 validation 阻断，服务不会调用 LLM；若质量降级，报告必须保留降级说明。

## 5. 可审计输出

结构化分析包保留 schema 版本、运行 ID、数据快照引用、数据缺口、warning 和验证结果。Markdown 报告的“数据溯源”至少应让读者看到分析时间、数据日期、来源、模型（如有）和免责声明。

官方披露核验与复权 benchmark 只公开清单、来源 URL、公告 ID、SHA-256 和预期规则，不公开原始 PDF、完整行情或本地运行产物。用户应在本机准备原始材料，并把 benchmark 输出留在 `.workspace/tmp/` 等生成目录。

## English summary

The project separates report period from public announcement date, blocks future announcements, records deterministic revision selection, refuses incomplete price adjustments, and keeps LLMs outside the numerical pipeline. Missing announcement metadata is reported as degraded quality rather than silently treated as point-in-time safe.
