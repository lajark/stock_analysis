"""知识库按需检索 — 基于分析维度的关键词匹配。

设计原则：
- 不加载全部知识库（53KB），仅按需检索相关片段
- 每次注入的额外内容控制在 ~3KB 以内
- 检索结果可缓存，同一天不重复读取
"""

import re

from src.runtime_paths import resource_root

KB_DIR = resource_root() / "knowledge_base"

# 分析维度 → (知识库文件, 章节) 映射
DIMENSION_MAP = {
    # 技术面
    "macd": ("technical_analysis_guide.md", "1.1 MACD"),
    "rsi": ("technical_analysis_guide.md", "1.2 RSI"),
    "kdj": ("technical_analysis_guide.md", "1.3 KDJ"),
    "bollinger": ("technical_analysis_guide.md", "1.4 布林带"),
    "volume": ("technical_analysis_guide.md", "1.5 成交量"),
    "trend": ("technical_analysis_guide.md", "2.1 趋势分析"),
    "support_resistance": ("technical_analysis_guide.md", "2.2 支撑阻力"),
    "sentiment": ("technical_analysis_guide.md", "2.3 市场情绪"),
    "technical_risk": ("technical_analysis_guide.md", "4. 风险评估"),
    # 策略
    "peg_strategy": ("single_stock_strategy.md", "个股基本面×趋势双轮动策略"),
    "buy_signal": ("ai_analysis_prompts.md", "2.1 买入分析"),
    "sell_signal": ("ai_analysis_prompts.md", "2.2 卖出分析"),
    "hold_signal": ("ai_analysis_prompts.md", "2.3 持有分析"),
    # 风险
    "risk_control": ("technical_analysis_guide.md", "4.2 风险控制"),
    "risk_analysis": ("ai_analysis_prompts.md", "3.2 风险分析"),
    # 估值
    "market_patterns": ("market_patterns.md", "5. 操作策略"),
    "seasonal": ("market_patterns.md", "3. 季节性特征"),
    # 案例
    "investment_cases": ("investment_cases.md", "成功案例"),
    "failure_cases": ("investment_cases.md", "失败案例"),
}


def _extract_section(content: str, section_title: str) -> str:
    """从 Markdown 内容中提取指定章节。

    匹配任意层级 Markdown 标题，提取到下一个同级或更高级标题之前。
    """
    escaped_title = re.escape(section_title)
    pattern = rf"^(?P<marks>#{{1,6}})[ \t]+[^\n]*{escaped_title}[^\n]*$"
    match = re.search(pattern, content, re.IGNORECASE | re.MULTILINE)
    if not match:
        return ""

    start = match.start()
    rest = content[match.end():]
    level = len(match.group("marks"))
    next_section = re.search(rf"^#{{1,{level}}}[ \t]+", rest, re.MULTILINE)
    if next_section:
        end = match.end() + next_section.start()
    else:
        end = len(content)

    return content[start:end].strip()


def _load_file(filename: str) -> str:
    """加载知识库文件（带缓存）。"""
    path = KB_DIR / filename
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def retrieve(analysis_mode: str, dimensions: list[str] | None = None) -> str:
    """按需检索知识库片段。

    Args:
        analysis_mode: 分析模式 (quick | deep | value)
        dimensions: 指定检索维度，None 则根据模式自动选择

    Returns:
        拼接后的知识库片段，可直接注入 system prompt。
    """
    if dimensions is None:
        dimensions = _default_dimensions(analysis_mode)

    # 按文件分组，避免重复读取
    file_contents: dict[str, str] = {}
    results: list[str] = []

    for dim in dimensions:
        if dim not in DIMENSION_MAP:
            continue
        filename, section_title = DIMENSION_MAP[dim]

        if filename not in file_contents:
            file_contents[filename] = _load_file(filename)

        content = file_contents[filename]
        section = _extract_section(content, section_title)
        if section:
            # 限制每段长度，避免过长
            if len(section) > 2000:
                section = section[:2000] + "\n\n...(已截断)"
            results.append(section)

    # 去重
    seen = set()
    unique = []
    for r in results:
        if r not in seen:
            seen.add(r)
            unique.append(r)

    # 拼接，限制总长
    combined = "\n\n---\n\n".join(unique)
    if len(combined) > 5000:
        combined = combined[:5000] + "\n\n...(知识库内容已按 Token 预算截断)"

    return combined


def _default_dimensions(mode: str) -> list[str]:
    """根据分析模式返回默认检索维度。"""
    if mode == "deep":
        return [
            "macd", "rsi", "kdj", "bollinger", "volume",
            "trend", "support_resistance", "sentiment",
            "technical_risk", "risk_control",
            "peg_strategy",
            "market_patterns",
        ]
    elif mode == "value":
        return [
            "peg_strategy",
            "investment_cases",
            "risk_control",
        ]
    elif mode == "trade":
        return [
            "trend", "support_resistance",
            "buy_signal", "sell_signal",
            "risk_control", "peg_strategy",
        ]
    else:  # quick
        return []  # 快速模式不注入知识库，保持 Token 最低


def get_knowledge_context(mode: str) -> str:
    """获取知识库上下文（供 CLI 注入 system prompt）。

    Returns:
        知识库文本，如果为空字符串则表示不需要注入。
    """
    if mode == "quick":
        return ""

    kb_text = retrieve(mode)
    if not kb_text:
        return ""

    return f"""\n\n## 参考知识库（以下内容来自分析知识库，用于辅助分析判断）

{kb_text}

---
请结合以上知识库参考内容，提升分析的深度和专业性。"""
