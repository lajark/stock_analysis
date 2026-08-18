"""报告渲染器 — 将 LLM 输出与结构化数据组合为 Markdown 报告。"""

from datetime import datetime
from pathlib import Path
from typing import Any

from jinja2 import Template

from src.config import get_config

# 报告模板
REPORT_TEMPLATE = """# {{ stock_name }} ({{ stock_code }}) 股票分析报告

> 分析日期：{{ analysis_date }} | 行业：{{ industry }} | 数据来源：{{ data_provider }}

---

## 核心指标概览

| 指标 | 数值 | 评价 |
|------|------|------|
| 最新收盘价 | {{ technical.close }} 元 | — |
| 趋势判断 | {{ technical.trend }} | MA排列 |
| MACD | {{ technical.macd_status }} | DIF={{ technical.macd }} |
| RSI | {{ technical.rsi }} | {{ technical.rsi_status }} |
| KDJ | {{ technical.kdj_k }}/{{ technical.kdj_d }}/{{ technical.kdj_j }} | — |
| KDJ状态 | {{ technical.kdj_status }} | — |
| 估值分位 | {{ valuation.percentiles.price_percentile_1y }}% | {{ valuation.percentiles.level }} |
| 基本面评分 | {{ fundamental.score }} | {{ fundamental.score_label }} |
| 风险等级 | {{ risk.risk_level.label }} | 评分 {{ risk.risk_level.score }} |

{% if price_levels %}
## 关键价位

| 类型 | 价位 | 距离当前价 | 强度 |
|------|------|-----------|------|
{%- for s in price_levels.supports[:3] %}
| 支撑: {{ s.type }} | {{ s.price }} | {{ s.distance_pct }}% | {{ s.strength }} |
{%- endfor %}
{%- for r in price_levels.resistances[:3] %}
| 阻力: {{ r.type }} | {{ r.price }} | {{ r.distance_pct }}% | {{ r.strength }} |
{%- endfor %}

### 交易信号

| 信号 | 置信度 |
|------|--------|
| 买入 | {{ price_levels.confidence.buy_confidence }}% ({{ price_levels.confidence.buy_label }}) |
| 卖出 | {{ price_levels.confidence.sell_confidence }}% ({{ price_levels.confidence.sell_label }}) |

### 3-6个月目标价

**目标买入价**：
{%- for bt in price_levels.targets.buy_targets[:2] %}
- {{ bt.price }} ({{ bt.reason }}, 置信度: {{ bt.confidence }})
{%- endfor %}

**目标卖出价**：
{%- for st in price_levels.targets.sell_targets[:2] %}
- {{ st.price }} ({{ st.reason }}, 置信度: {{ st.confidence }})
{%- endfor %}
{% endif %}

---

{{ llm_output }}

---

{% if changes and changes.changed_count %}
## 与上次结构化分析的变化

| 字段 | 上次值 | 本次值 |
|------|--------|--------|
{%- for change in changes.changes[:10] %}
| {{ change.path }} | {{ change.before }} | {{ change.after }} |
{%- endfor %}
{% endif %}

## 数据溯源

| 项目 | 详情 |
|------|------|
| 分析时间 | {{ meta.generated_at }} |
| 数据日期 | {{ meta.data_date }} |
| 数据源 | {{ meta.data_provider }} |
| 模型 | {{ llm_model }} |
{% if ma_override_note %}
| 参数说明 | {{ ma_override_note }} |
{% endif %}
{% if tokens.input_tokens is not none and tokens.output_tokens is not none %}
| Token 消耗 | input={{ tokens.input_tokens }}, output={{ tokens.output_tokens }} |
{% else %}
| Token 消耗 | 统计不可用（供应商流式未返回 usage） |
{% endif %}

---

> ⚠️ 免责声明：本报告仅供研究和辅助分析使用，不构成投资建议。投资有风险，入市需谨慎。
"""


def _shorten_change_values(changes: Any, limit: int = 150) -> Any:
    """Return a copy of ``changes`` whose before/after values are truncated.

    A whole evidence list can differ in just a few fields; dumping the full
    Python repr into a Markdown table cell makes the report unreadable, so
    oversized values are cut to ``limit`` characters with a trailing ellipsis.
    """
    if not isinstance(changes, dict):
        return changes
    rendered = []
    for change in changes.get("changes", []):
        if not isinstance(change, dict):
            rendered.append(change)
            continue
        rendered.append(
            {
                **change,
                "before": _truncate(change.get("before"), limit),
                "after": _truncate(change.get("after"), limit),
            }
        )
    return {**changes, "changes": rendered}


def _truncate(value: Any, limit: int) -> str:
    text = str(value)
    if len(text) <= limit:
        return text
    return text[:limit] + "…"


def render_report(
    package: dict[str, Any],
    llm_output: str,
    llm_model: str,
    tokens: dict[str, int],
    output_path: str | None = None,
    run_id: str | None = None,
    config_override: Any = None,
) -> str:
    """渲染完整 Markdown 报告。

    Args:
        package: 结构化分析包
        llm_output: LLM 生成的文本
        llm_model: 使用的模型名称
        tokens: Token 用量
        output_path: 输出文件路径，为 None 则自动生成
        run_id: 分析 run_id；自动生成文件名时作为唯一后缀，避免并发碰撞
        config_override: 外部配置对象（含 analysis.ma_periods 等信息），
                         用于报告注明"使用了策略建议参数"。

    Returns:
        报告文件路径。
    """
    template = Template(REPORT_TEMPLATE)
    stock = package["stock"]
    meta = package["meta"]

    # Truncate oversized change values so the change table stays readable
    # (e.g. whole evidence lists that differ only in a few fields).
    changes = _shorten_change_values(package.get("changes"))

    # 判断是否使用了策略建议参数覆盖
    ma_override_note = ""
    if config_override is not None:
        raw = getattr(config_override, "analysis_ma_periods", "")
        enabled = getattr(config_override, "use_analysis_ma_override", "0")
        if raw and enabled.strip().lower() in {"1", "true", "yes", "on"}:
            ma_override_note = (
                f"技术指标 MA 周期使用策略建议参数：{config_override.analysis.ma_periods}"
            )

    report = template.render(
        stock_name=stock["name"],
        stock_code=stock["code"],
        industry=stock.get("industry", "未知"),
        analysis_date=meta["analysis_date"],
        data_provider=meta["data_provider"],
        technical=package["technical"],
        fundamental=package["fundamental"],
        valuation=package["valuation"],
        risk=package["risk"],
        price_levels=package.get("price_levels", {}),
        changes=changes,
        meta=meta,
        llm_output=llm_output,
        llm_model=llm_model,
        tokens=tokens,
        ma_override_note=ma_override_note,
    )

    # 写入文件
    if output_path is None:
        config = get_config()
        config.reports_dir.mkdir(parents=True, exist_ok=True)
        # run_id suffix guarantees uniqueness even when two batch workers
        # render the same code in the same clock tick (datetime resolution on
        # Windows is far coarser than the old second-level timestamp).
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        code = stock["code"]
        unique = f"_{run_id[:8]}" if run_id else ""
        output_path = str(config.reports_dir / f"{code}_{ts}{unique}.md")

    # Atomic write: readers/other workers never see a truncated report.
    path = Path(output_path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(report, encoding="utf-8")
    tmp.replace(path)

    return output_path
