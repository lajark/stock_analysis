# Benchmark v1

这个 benchmark 用于验证“官方材料 → PIT 选择 → 结构化快照”和“原始行情 + 复权因子 → qfq 结果”两条可审计路径。仓库只提交清单、来源地址、公告 ID、哈希和规则；原始 PDF、完整行情和生成结果必须由用户放在本地。

## 准备本地材料

1. 从 `manifest.json` 的 `documents` 下载官方 PDF，按清单中的 `path` 放到 `<source-dir>/raw_reports/`，并核对 SHA-256。
2. 使用项目现有官方审计工具生成 PIT 清单和标准化快照：

   ```powershell
   python scripts/index_official_reports.py --checklist <checklist.csv> --archive <reports.zip> --output <source-dir>/prepared/index.csv --manifest-output <source-dir>/prepared/index-manifest.json
   python scripts/extract_official_financials.py --index <source-dir>/prepared/index.csv --archive <reports.zip> --output-dir <source-dir>/prepared/official-financials
   python scripts/enrich_official_pit_dates.py --index <source-dir>/prepared/index.csv --output <source-dir>/prepared/pit-index.csv --raw-output <source-dir>/prepared/pit-raw.csv --manifest <source-dir>/prepared/pit-manifest.json
   ```

   将最终使用的公告日期清单保存为 `prepared/official-disclosure-checklist-pit-20260815.csv`。它需要包含 `ts_code`、`period_end`、`announce_date`、`revision`、`status`、`sha256`、`announcement_source_url` 和 `announcement_candidate_count` 字段。

3. 将每个复权样本的 `daily_raw.csv`、`adj_factor.csv` 和供应商参考 `daily_qfq.csv` 放到清单指定路径。

## 运行

```bash
python scripts/run_public_benchmark.py \
  --manifest benchmarks/v1/manifest.json \
  --source-dir path/to/local-materials \
  --output-dir .workspace/tmp/public-benchmark
```

运行器默认不联网、不修改生产缓存。输出为确定性的 `benchmark-results.json` 和 `benchmark-report.md`。

- `0`：所有材料、哈希、PIT 规则和复权对账通过；
- `1`：材料存在但规则或数值不一致；
- `2`：清单无效、材料缺失或哈希不符。

Benchmark 样本覆盖贵州茅台、招商银行、宝钢股份、宁德时代、海航控股的 2024 年报与 2025 半年报，并用中国平安 2024 一季报的多公告候选验证确定性选择。复权样本覆盖现金分红、现金分红加转增和无事件连续性对照。

## 解释结果

Benchmark 不是投资回测，也不是数据源替换门。它只回答：在给定原始材料、分析时点和方法版本下，项目能否得到可复核的报告期、公告版本和复权结果。缺少公告元数据时应显示 degraded；未来公告或复权因子缺失不得被当作通过。

复权参考文件通常按行情展示精度保留两位小数，因此清单中的 `tolerance` 必须显式记录该舍入误差；它不是放宽缺失因子或口径混用的许可。

英文方法摘要见 [`docs/methodology.en.md`](../docs/methodology.en.md)。
