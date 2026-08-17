"""Extract a conservative financial snapshot from the supplied report PDFs.

This is an audit helper, not a general-purpose PDF table parser. It reads the
standard ``主要会计数据和财务指标`` table and the consolidated financial
statement pages, keeps the first current-period value, and records the exact
archive member plus parser status. Missing or ambiguous fields remain empty;
they are never inferred from prose.

The output directory contains a wide audit table and four normalized CSV files
that can be passed to ``scripts/reconcile_financial_data.py`` as the reference
side. Announcement dates are intentionally left blank because the supplied
archive is named by report period, not by CNINFO publication metadata; this
means the first reconciliation is a value/coverage comparison, not a final
point-in-time publication-date verdict.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import io
import json
import logging
import re
import unicodedata
import zipfile
from pathlib import Path
from typing import Any

import pandas as pd
from pypdf import PdfReader
from pypdf.generic import DecodedStreamObject, NameObject

REPORT_TYPES = {"03-31": "q1", "06-30": "semiannual", "09-30": "q3", "12-31": "annual"}
PARSER_VERSION = "cninfo-pdf-financials-v28"
_CID_MAP_CACHE: dict[str, dict[int, int]] = {}
_CID_CMAP_CACHE: dict[tuple[str, int], DecodedStreamObject] = {}
# A small number of Hong Kong-listed A-share reports use Traditional Chinese
# labels while keeping the same numeric tables as the mainland templates.
# Normalize only accounting/table vocabulary here; this is intentionally not
# a general Traditional-to-Simplified converter so issuer names and narrative
# text remain untouched.
TRADITIONAL_TEXT_REPLACEMENTS = {
    "合併綜合收益表": "合并利润表",
    "合併利潤表": "合并利润表",
    "合併現金流量表": "合并现金流量表",
    "合併資產負債表": "合并资产负债表",
    "合併財務狀況表": "合并资产负债表",
    "營業收入": "营业收入",
    "營業利潤": "营业利润",
    "經營費用": "经营费用",
    "加權平均權益回報率": "加权平均净资产收益率",
    "加權平均权益回報率": "加权平均净资产收益率",
    "歸屬於母公司股東的權益": "归属于母公司股东的权益",
    "歸屬於母公司股東的淨利潤": "归属于母公司股东的净利润",
    "归属於母公司股東": "归属于母公司股东",
    "母公司股東": "母公司股东",
    "總资产": "总资产",
    "總负债": "总负债",
    "負債總額": "负债总额",
    "綜合收益表": "利润表",
    "財務狀況表": "资产负债表",
    "資產負債表": "资产负债表",
    "現金流量表": "现金流量表",
    "財務報表附註": "财务报表附注",
    "經營收入": "营业收入",
    "淨利潤": "净利润",
    "歸屬於本行股東的淨利潤": "归属于本行股东的净利润",
    "基本和稀釋每股收益": "基本和稀释每股收益",
    "年化加權平均淨資產收益率": "年化加权平均净资产收益率",
    "資產總額": "资产总额",
    "歸屬於本行股東權益": "归属于本行股东权益",
    "負債合計": "负债合计",
    "股東權益": "股东权益",
    "總計": "总计",
    "經營活動": "经营活动",
    "投資活動": "投资活动",
    "籌資活動": "筹资活动",
    "產生的現金流量淨額": "产生的现金流量净额",
    "所用的現金流量淨額": "所用的现金流量净额",
    "現金流量淨額": "现金流量净额",
    "所得稅前": "所得税前",
    "資產": "资产",
    "負債": "负债",
    "歸屬": "归属",
    "權益": "权益",
    "百萬元": "百万元",
    "千萬元": "千万元",
    "萬元": "万元",
    "億元": "亿元",
    "人民幣": "人民币",
}
# PDF text extraction is not consistent about accounting negatives: closing
# parentheses may be separated by whitespace, and some issuers use full-width
# Chinese parentheses.  Keep the token conservative while accepting both
# forms so cash-flow statement signs are not silently flipped to positive.
NUMBER_PATTERN = re.compile(
    r"(?<![\d])(?:[-−]?[（(]?\d[\d,]*(?:\.\d+)?\s*[）)]?)(?:%)?"
)
UNIT_PATTERN = re.compile(
    r"(?:"
    r"(?:金额|货币)?\s*单位[^\n]{0,30}?"
    r"|[（(][^）\n]{0,30}?"
    r"|人民币\s*"
    r")(亿元|百万元|百万|万元|千元|元)"
)
UNIT_SCALE = {
    "元": 1.0,
    "千元": 1_000.0,
    "万元": 10_000.0,
    "百万元": 1_000_000.0,
    "百万": 1_000_000.0,
    "亿元": 100_000_000.0,
}

MAJOR_FIELDS: dict[str, tuple[tuple[str, ...], bool]] = {
    "revenue": (("营业收入", "营业总收入"), True),
    "net_profit_attributable": (
        (
            "归属于上市公司股东的净利润",
            "归属于母公司所有者的净利润",
            "归属于母公司股东的净利润",
            "归属于本行股东的净利润",
            "归属于本行普通股股东的净利润",
        ),
        True,
    ),
    "eps": (("基本和稀释每股收益", "基本每股收益"), False),
    "roe": (("加权平均净资产收益率", "净资产收益率"), False),
    "total_assets": (("总资产", "资产总额", "资产合计", "资产总计"), True),
    "shareholders_equity": (
        (
            "归属于上市公司股东的所有者权益",
            "归属于上市公司股东的净资产",
            "归属于母公司所有者权益",
            "归属于母公司所有者的权益",
            "归属于母公司所有者的净资产",
            "归属于母公司股东权益",
            "归属于母公司股东的权益",
            "归属于本公司股东权益合计",
            "归属于本行股东权益",
            "归属于本行股东权益合计",
        ),
        True,
    ),
}

STATEMENT_FIELDS: dict[str, tuple[tuple[str, ...], tuple[str, ...], bool]] = {
    "operating_profit": (
        ("利润表",),
        ("营业利润", "营业(亏损)/利润", "营业亏损"),
        True,
    ),
    "total_liabilities": (("资产负债表",), ("负债合计", "负债总计"), True),
    "operating_cf": (
        ("现金流量表",),
        (
            "经营活动产生的现金流量净额",
            "经营活动 (使用) /产生的现金流量净额",
            "经营活动产生/(使用)的现金流量净额",
            "经营活动产生 / ( 使用 ) 的现金流量净额",
            "经营活动产生 /( 使用 ) 的现金流量净额",
            "经营活动 ( 使用 )/ 产生的现金流量净额",
            "经营活动使用的现金流量净额",
        ),
        True,
    ),
    "investing_cf": (
        ("现金流量表",),
        (
            "投资活动产生的现金流量净额",
            "投资活动使用的现金流量净额",
            "投资活动所用的现金流量净额",
            "投资活动产生/(使用)的现金流量净额",
            "投资活动 ( 使用 )/ 产生的现金流量净额",
        ),
        True,
    ),
    "financing_cf": (
        ("现金流量表",),
        (
            "筹资活动产生的现金流量净额",
            "筹资活动使用的现金流量净额",
            "筹资活动所用的现金流量净额",
            "筹资活动产生/(使用)的现金流量净额",
            "筹资活动产生/(所用)的现金流量净额",
            "筹资活动产生 / （使用）的现金流量净额",
            "筹资活动产生 / (使用) 的现金流量净额",
            "筹资活动(使用)/产生的现金流量净额",
            "筹资活动产生 /( 使用 ) 的现金流量净额",
            "筹资活动 ( 使用 )/ 产生的现金流量净额",
            "筹资活动产生 ╱ （使用） 的现金流量净额",
            "筹资活动 （所用） ／产生的现金流量净额",
            "筹资活动（所用）／产生的现金流量净额",
            "筹资活动 (所用)/产生的现金流量净额",
            "筹资活动（所用）/产生的现金流量净额",
            "筹资活动(所用)/产生的现金流量净额",
        ),
        True,
    ),
    "net_profit": (
        ("利润表",),
        (
            "四、净利润",
            "五、净利润",
            "净利润",
            "净(亏损)/利润",
        ),
        True,
    ),
}

STATEMENT_REJECT_PREFIXES = {
    "net_profit": ("归属于", "少数"),
    # Bank cash-flow statements often show both the pre-tax operating cash
    # flow and the after-tax total.  The provider field ``n_cashflow_act``
    # corresponds to the latter; reject the former when both rows share the
    # same label suffix.
    "operating_cf": ("所得税前",),
}


def _report_type(period_end: str) -> str:
    try:
        return REPORT_TYPES[period_end[4:].lstrip("-")]
    except KeyError as error:
        raise ValueError(f"不支持的报告期：{period_end}") from error


def _number(value: str) -> float | None:
    text = (
        value.replace(",", "")
        .replace("−", "-")
        .replace("（", "(")
        .replace("）", ")")
        .replace("%", "")
        .strip()
    )
    negative = text.startswith("(") and text.endswith(")")
    text = text.strip("()")
    try:
        parsed = float(text)
    except ValueError:
        return None
    return -parsed if negative else parsed


def _normalize_pdf_text(text: str) -> str:
    """Normalize wrapped labels and known Traditional accounting vocabulary."""
    # Adobe CID maps may yield Kangxi radicals (for example ``⼈``) for glyph
    # classes whose compatibility decomposition is the ordinary Hanzi.  NFKC
    # is limited to Unicode compatibility forms and does not invent labels.
    normalized = "".join(
        unicodedata.normalize("NFKC", character)
        if 0x2E80 <= ord(character) <= 0x2FFF
        else character
        for character in text
    )
    normalized = re.sub(
        r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", normalized
    )
    for source, target in sorted(
        TRADITIONAL_TEXT_REPLACEMENTS.items(), key=lambda item: -len(item[0])
    ):
        normalized = normalized.replace(source, target)
    return normalized


def _load_cid_unicode_map(path: Path | None) -> dict[int, int]:
    """Load an Adobe GB1/CNS1 ``cid2code.txt`` map for an optional audit run."""
    if path is None:
        return {}
    cache_key = str(path.resolve())
    if cache_key in _CID_MAP_CACHE:
        return _CID_MAP_CACHE[cache_key]
    mapping: dict[int, int] = {}
    unicode_column: int | None = None
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.startswith("CID\t"):
                headers = line.rstrip("\n").split("\t")
                unicode_column = next(
                    (
                        index
                        for index, header in enumerate(headers)
                        if header.startswith("Uni") and header.endswith("UTF16")
                    ),
                    None,
                )
                continue
            if not line.strip() or line.startswith("#"):
                continue
            columns = line.rstrip("\n").split("\t")
            if unicode_column is None or len(columns) <= unicode_column:
                continue
            try:
                cid = int(columns[0])
                code_text = columns[unicode_column].split(",", 1)[0].strip()
                codepoint = int(code_text, 16)
            except (TypeError, ValueError):
                continue
            if codepoint <= 0x10FFFF:
                mapping[cid] = codepoint
    if not mapping:
        raise ValueError(f"CID 映射为空或格式不受支持：{path}")
    _CID_MAP_CACHE[cache_key] = mapping
    return mapping


def _embedded_cff_cids(font: Any) -> set[int]:
    """Read CID names from an embedded CFF font without requiring a PDF CLI."""
    try:
        from fontTools.cffLib import CFFFontSet
    except ImportError as error:
        raise RuntimeError("使用 --cid-map 需要安装 fontTools") from error

    descriptor = (
        font["/DescendantFonts"][0]
        .get_object()["/FontDescriptor"]
        .get_object()
    )
    data = descriptor["/FontFile3"].get_object().get_data()
    cff = CFFFontSet()
    cff.decompile(io.BytesIO(data), None)
    top = cff[next(iter(cff.keys()))]
    cids: set[int] = set()
    for glyph_name in top.charset:
        match = re.fullmatch(r"cid(\d+)", glyph_name)
        if match:
            cids.add(int(match.group(1)))
    return cids


def _build_cid_to_unicode_cmap(
    cids: set[int], mapping: dict[int, int]
) -> DecodedStreamObject | None:
    pairs = [(cid, mapping[cid]) for cid in sorted(cids) if cid in mapping]
    if not pairs:
        return None
    lines = [
        "/CIDInit /ProcSet findresource begin",
        "12 dict begin",
        "begincmap",
        "/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def",
        "/CMapName /Adobe-GB1-UTF16-H def",
        "/CMapType 2 def",
        "1 begincodespacerange",
        "<0000> <FFFF>",
        "endcodespacerange",
    ]
    for start in range(0, len(pairs), 100):
        batch = pairs[start : start + 100]
        lines.append(f"{len(batch)} beginbfchar")
        for cid, codepoint in batch:
            if codepoint <= 0xFFFF:
                target = f"{codepoint:04X}"
            else:
                scalar = codepoint - 0x10000
                target = f"{0xD800 + (scalar >> 10):04X}{0xDC00 + (scalar & 0x3FF):04X}"
            lines.append(f"<{cid:04X}> <{target}>")
        lines.append("endbfchar")
    lines.extend(
        [
            "endcmap",
            "CMapName currentdict /CMap defineresource pop",
            "end",
            "end",
        ]
    )
    cmap = DecodedStreamObject()
    cmap.set_data(("\n".join(lines) + "\n").encode("ascii"))
    return cmap


def _inject_cid_to_unicode(
    page: Any,
    mapping: dict[int, int],
    cmap_cache: dict[tuple[str, int], DecodedStreamObject],
    map_key: str,
) -> None:
    """Add ToUnicode only to embedded Type0 fonts that lack it."""
    if not mapping:
        return
    resources = page.get("/Resources")
    if resources is None:
        return
    fonts = resources.get_object().get("/Font", {})
    for reference in fonts.values():
        font = reference.get_object()
        if font.get("/Subtype") != "/Type0" or "/ToUnicode" in font:
            continue
        object_id = int(getattr(reference, "idnum", id(font)))
        cache_key = (map_key, object_id)
        cmap = cmap_cache.get(cache_key)
        if cmap is None:
            cids = _embedded_cff_cids(font)
            cmap = _build_cid_to_unicode_cmap(cids, mapping)
            if cmap is None:
                continue
            cmap_cache[cache_key] = cmap
        font[NameObject("/ToUnicode")] = cmap


def _scale(text: str, position: int, label: str = "") -> float:
    label_window = text[position : position + len(label) + 80]
    # Quarterly reports often put the unit directly after the metric label,
    # e.g. ``营业收入（人民币百万元）``.  The generic unit pattern handles
    # the currency qualifier; the older parenthesized-only pattern does not.
    label_markers = [
        marker
        for marker in UNIT_PATTERN.finditer(label_window)
        if not any(
            cue in label_window[max(0, marker.start() - 16) : marker.end() + 16]
            for cue in ("/股", "／股", "每股")
        )
    ]
    if label_markers:
        return UNIT_SCALE[label_markers[0].group(1)]
    label_markers = [
        marker
        for marker in re.finditer(
            r"[（(]\s*(亿元|百万元|百万|万元|千元|元)\s*[）)]", label_window
        )
        if not any(
            cue in label_window[max(0, marker.start() - 16) : marker.end() + 16]
            for cue in ("/股", "／股", "每股")
        )
    ]
    if label_markers:
        return UNIT_SCALE[label_markers[0].group(1)]
    prior_text = text[max(0, position - 5000) : position]
    markers = list(UNIT_PATTERN.finditer(prior_text))
    for marker in reversed(markers):
        marker_context = prior_text[max(0, marker.start() - 12) : marker.end() + 12]
        # ``（元/股）`` belongs to EPS/price rows, not to subsequent
        # balance-sheet amounts on the same summary page.
        if "/股" in marker_context or "每股" in marker_context:
            continue
        return UNIT_SCALE[marker.group(1)]
    return 1.0


def _looks_like_ratio_label(text: str, position: int, label: str) -> bool:
    """Return whether a metric occurrence belongs to a ratio/average label.

    Bank reports commonly place rows such as ``手续费净收入对营业收入比率``
    and ``总权益对资产总额比率`` beside the actual summary table.  A raw
    substring search for ``营业收入`` or ``资产总额`` must not treat those
    descriptive ratios as the requested monetary fields.
    """
    line_start = text.rfind("\n", 0, position) + 1
    line_end = text.find("\n", position + len(label))
    if line_end < 0:
        line_end = len(text)
    prefix = text[max(line_start, position - 24) : position]
    suffix = text[position + len(label) : min(line_end, position + len(label) + 24)]
    if "的计算及披露" in suffix or "和每股收益" in suffix:
        return True
    ratio_markers = ("比率", "收益率", "回报率", "占比")
    if any(marker in prefix or marker in suffix for marker in ratio_markers):
        return True
    # ``平均`` is a ratio cue for monetary rows such as ``资产总额的平均
    # 值``, but it is part of the legitimate label ``加权平均净资产收益率``.
    if "平均" not in label and "平均" in (prefix + suffix):
        return True
    return False


def _has_usable_label(text: str, label: str) -> bool:
    start = 0
    while (position := text.find(label, start)) >= 0:
        if not _looks_like_ratio_label(text, position, label):
            return True
        start = position + len(label)
    return False


def _pick_values(
    text: str,
    position: int,
    label: str,
    *,
    value_index: int = 0,
    skip_references: bool = False,
) -> float | None:
    """Pick a current-period number while skipping statement footnote IDs."""
    snippet = text[position + len(label) : position + len(label) + 500]
    if value_index >= 2:
        # Text extraction can concatenate adjacent summary rows.  Trim at a
        # recognizable next-row label before tokenizing numbers, otherwise a
        # later row's percentage may be mistaken for the requested YTD value.
        row_markers = (
            "营业收入",
            "归属于上市公司股东的",
            "归属于母公司股东的",
            "归属于本行股东的",
            "经营活动产生的现金流量净额",
            "投资活动产生的现金流量净额",
            "筹资活动产生的现金流量净额",
            "基本每股收益",
            "稀释每股收益",
            "加权平均净资产收益率",
            "总资产",
            "资产总额",
            "资产总计",
        )
        row_end = min(
            (index for marker in row_markers if (index := snippet.find(marker)) >= 0),
            default=len(snippet),
        )
        snippet = snippet[:row_end]
        first_line_end = snippet.find("\n")
        if first_line_end >= 0 and "不适用" in snippet[:first_line_end]:
            # In compact Q3 tables, percentage-change cells are textual
            # ``不适用`` and the row ends before the next page/header.  Keep
            # the selector from reaching a later year printed nearby.
            snippet = snippet[:first_line_end]
        # Compact issuer tables may place the whole row on one extracted
        # line.  When both change cells are textual ``不适用``, stop after
        # the second marker so the next metric row cannot supply values.
        markers = [match.start() for match in re.finditer("不适用", snippet)]
        if len(markers) >= 2:
            snippet = snippet[: markers[1] + len("不适用")]
    # Some PDFs insert a line/text-space after each thousands separator
    # (``913, 789``).  Remove only whitespace following a comma so the number
    # tokenizer sees one amount instead of two unrelated values.
    snippet = re.sub(r"\s*,\s*", ",", snippet)
    raw_values = [
        (match.group(), value)
        for match in NUMBER_PATTERN.finditer(snippet)
        if (value := _number(match.group())) is not None
    ]
    # PDF text extraction preserves table footnote markers such as ``(1)``
    # immediately before the actual current-period value.  They are not
    # financial negatives; remove only the short parenthesized marker form so
    # genuine values such as ``(1,208)`` remain untouched.
    if len(raw_values) > 1 and re.fullmatch(
        r"[（(]\s*\d{1,2}\s*[）)]", raw_values[0][0]
    ):
        raw_values = raw_values[1:]
    values = [value for _, value in raw_values]
    if not values:
        return None
    # Q3 summaries sometimes expose only two numeric columns (current period
    # and YTD) because percentage-change cells contain ``不适用``.  The
    # generic Q3 selector uses index 2 for three/four-column layouts; when
    # only two amounts are present, the second amount is the YTD value.
    if value_index >= 2 and len(values) == 2:
        return values[1]
    # Other Q3 summaries have three numeric cells because a textual
    # ``不适用`` change cell disappears from the extracted number stream:
    # current quarter, YTD, and the remaining percentage change.  The YTD
    # amount is the middle numeric value in that layout.
    if value_index >= 2 and len(values) == 3 and "不适用" in snippet:
        return values[1]
    # Rows often contain ``四(65)(h)`` before the actual amount. Small leading
    # integers followed by a financial-sized value are statement references.
    removed_short_reference = False
    short_reference_pattern = r"\d{1,2}[）)]?"
    if skip_references and len(raw_values) > 1:
        first_raw, second_raw = raw_values[0][0].strip(), raw_values[1][0].strip()
        if re.fullmatch(short_reference_pattern, first_raw) and (
            "." in second_raw or "%" in second_raw
        ):
            raw_values = raw_values[1:]
            values = [value for _, value in raw_values]
            removed_short_reference = True
    if (
        skip_references
        and not removed_short_reference
        and len(values) > 1
        and re.fullmatch(short_reference_pattern, raw_values[0][0].strip())
        and abs(values[0]) <= 100
    ):
        first_amount = next(
            (index for index, value in enumerate(values[1:], 1) if abs(value) >= 1_000),
            None,
        )
        if first_amount is not None:
            values = values[first_amount:]
    return values[value_index] if len(values) > value_index else values[0]


def _find_value(
    text: str,
    labels: tuple[str, ...],
    *,
    scaled: bool,
    reject_prefixes: tuple[str, ...] = (),
    value_index: int = 0,
    skip_references: bool = False,
    prefer_label_order: bool = False,
) -> float | None:
    # (normalized value, text position, raw value before unit scaling)
    candidates: list[tuple[float, int, float]] = []
    label_candidates: dict[str, list[tuple[float, int, float]]] = {}
    for label in labels:
        start = 0
        while (position := text.find(label, start)) >= 0:
            # If a short alias is literally embedded in a longer alias at the
            # same text position, keep only the longer row label.  Do not
            # prefer a merely longer but distinct alias (for example, a bank's
            # ordinary-shareholder profit is not the attributable profit row).
            overlapping = any(
                len(other) > len(label)
                and label in other
                and text.startswith(other, position - (len(other) - len(label)))
                for other in labels
            )
            if overlapping:
                start = position + len(label)
                continue
            if _looks_like_ratio_label(text, position, label):
                start = position + len(label)
                continue
            following = text[position + len(label) : position + len(label) + 1]
            if (
                (label.endswith("净") or label.endswith("净资产"))
                and following
                and "\u4e00" <= following <= "\u9fff"
            ):
                start = position + len(label)
                continue
            if label == "总资产":
                context = text[max(0, position - 8) : position + len(label) + 10]
                if "平均总资产" in context or any(
                    marker in context
                    for marker in ("总资产收益率", "总资产回报率", "总资产比率")
                ):
                    start = position + len(label)
                    continue
            # ``净利润`` is also the tail of longer rows such as
            # ``归属于上市公司股东的净利润``.  An 8-character window is too
            # short for the reject-prefix guard and lets summary-page
            # references win before the actual consolidated row.
            prefix_window = max(
                8,
                max((len(item) for item in reject_prefixes), default=0) + 24,
            )
            line_start = text.rfind("\n", 0, position) + 1
            prefix = text[max(line_start, position - prefix_window) : position]
            if not any(item in prefix for item in reject_prefixes):
                value = _pick_values(
                    text,
                    position,
                    label,
                    value_index=value_index,
                    skip_references=skip_references,
                )
                if value is not None:
                    multiplier = _scale(text, position, label) if scaled else 1.0
                    candidate = (value * multiplier, position, value)
                    candidates.append(candidate)
                    label_candidates.setdefault(label, []).append(candidate)
            start = position + len(label)
    if not candidates:
        return None

    def choose(items: list[tuple[float, int, float]]) -> float:
        ordered = sorted(items, key=lambda item: item[1])
        if scaled:
            if any(abs(item[2]) >= 1_000 for item in ordered):
                ordered = [item for item in ordered if abs(item[2]) >= 100]
            substantial = [item for item in ordered if abs(item[0]) >= 10_000]
            if substantial:
                return substantial[0][0]
        return ordered[0][0]

    # Some reports disclose both ``营业总收入`` and the narrower ``营业收入``.
    # Tushare's ``revenue`` maps to the latter; prefer the first label with a
    # usable candidate when the caller supplies an explicit alias priority.
    if prefer_label_order:
        for label in labels:
            if label in label_candidates:
                return choose(label_candidates[label])
    if scaled:
        # Narrative references frequently contain only a year or a footnote
        # number before the real table row.  For the large monetary fields in
        # this audit, prefer a financially sized candidate when one exists;
        # retain the first candidate as a conservative fallback for small
        # issuers/units.
        ordered = sorted(candidates, key=lambda item: item[1])
        # A footnote or ratio such as ``4.0`` can precede the actual
        # million-yuan row ``44,432,848``.  If a table-sized raw candidate is
        # present, ignore these very small raw values before choosing by
        # position.  This is intentionally conservative and only applies
        # when the same label has a clearly larger alternative.
        if any(abs(item[2]) >= 1_000 for item in ordered):
            ordered = [item for item in ordered if abs(item[2]) >= 100]
        substantial = [item for item in ordered if abs(item[0]) >= 10_000]
        if substantial:
            return substantial[0][0]
        return ordered[0][0]
    return min(candidates, key=lambda item: item[1])[0]


def _major_page_text(pages: list[str]) -> str:
    major_labels = {
        label
        for labels, _ in MAJOR_FIELDS.values()
        for label in labels
    }

    # The ``主要会计数据`` heading is not stable across issuers.  Some
    # reports use ``主要财务数据`` while others omit the heading entirely.
    # Score a short page window instead of a single page so a two-page table
    # (for example, data on p. 8 and indicators on p. 9) stays together.
    def window_score(index: int) -> tuple[int, int, int, int, int]:
        block = "\n".join(pages[index : min(len(pages), index + 4)])
        label_hits = sum(_has_usable_label(block, label) for label in major_labels)
        table_markers = sum(
            block.count(marker)
            for marker in ("主要会计数据", "主要财务数据", "主要财务指标")
        )
        unit_hits = sum(1 for _ in UNIT_PATTERN.finditer(block))
        number_hits = len(NUMBER_PATTERN.findall(block))
        # Prefer the earliest window on ties: summary tables are normally
        # near the front, while notes and segment disclosures repeat labels.
        return table_markers, label_hits, unit_hits, number_hits, -index

    if not pages:
        return ""
    # Prefer the page that actually contains a major-data table heading and
    # row labels.  A number of annual reports place a decorative three-year
    # chart immediately before the table; a four-page window around that
    # chart otherwise makes values such as ``8,066.51`` look like the current
    # total assets.  Requiring both a heading and row labels keeps the chart
    # out while retaining issuers whose table starts on a single page.
    table_candidates: list[tuple[int, tuple[int, int, int, int]]] = []
    core_labels = (
        "营业收入",
        "归属于上市公司股东的净利润",
        "归属于母公司股东的净利润",
        "归属于本行股东的净利润",
        "归属于本行普通股股东的净利润",
        "资产总额",
        "资产总计",
        "总资产",
    )

    def _window(anchor: int) -> str:
        """Return the major-table window, including a preceding continuation page.

        Annual-report summary tables often start with revenue at the bottom of
        one page and continue with profit/assets on the next page.  Selecting
        only the continuation page can then expose a later quarterly table and
        make its first value look like the annual value.  A preceding page is
        included only when it has both the summary marker and a core row label,
        which keeps decorative charts and narrative pages out.
        """
        start = anchor
        if anchor > 0:
            previous = pages[anchor - 1]
            previous_has_marker = any(
                marker in previous
                for marker in ("主要会计数据", "主要财务数据", "主要财务指标")
            )
            previous_has_core = any(
                _has_usable_label(previous, label) for label in core_labels
            )
            if previous_has_marker and previous_has_core:
                start = anchor - 1
        return "\n".join(pages[start : min(len(pages), anchor + 4)])

    # Bank annual reports may put the actual full-year table immediately
    # before a ``主要财务指标`` page.  The preceding page has no standard
    # heading, but its ``全年业绩``/``年度业绩`` rows are more authoritative
    # than the following quarterly table.  Recognize that layout explicitly.
    summary_row_labels = (
        "营业收入",
        "归属于上市公司股东的净利润",
        "归属于母公司股东的净利润",
        "归属于本行股东的净利润",
        "归属于本行普通股股东的净利润",
        "资产总额",
        "资产总计",
    )
    summary_candidates: list[tuple[int, tuple[int, int, int]]] = []
    for index, page in enumerate(pages):
        if not any(marker in page for marker in ("全年业绩", "年度业绩")):
            continue
        row_hits = sum(_has_usable_label(page, label) for label in summary_row_labels)
        unit_hits = sum(1 for _ in UNIT_PATTERN.finditer(page))
        number_hits = len(NUMBER_PATTERN.findall(page))
        if row_hits >= 3 and unit_hits > 0:
            summary_candidates.append((index, (row_hits, unit_hits, number_hits)))
    if summary_candidates:
        anchor = max(
            summary_candidates,
            key=lambda item: (item[1][0], -item[0], item[1][1], item[1][2]),
        )[0]
        return _window(anchor)

    # A few insurance/financial Q3 templates put the reliable cumulative
    # table under ``主要会计数据及财务指标`` while repeating a shorter
    # ``主要会计数据`` heading later in the report.  Prefer the front table
    # only when it exposes the full 1-9 month column set and at least three
    # core rows; this avoids globally favoring repeated headings.
    structured_candidates: list[tuple[int, int, int, int]] = []
    for index, page in enumerate(pages[:40]):
        if "主要会计数据及财务指标" not in page:
            continue
        if not (
            re.search(r"\d{4}\s*年\s*7\s*-\s*9\s*月", page)
            and re.search(r"\d{4}\s*年\s*1\s*-\s*9\s*月", page)
        ):
            continue
        core_hits = sum(_has_usable_label(page, label) for label in core_labels)
        unit_hits = sum(1 for _ in UNIT_PATTERN.finditer(page))
        number_hits = len(NUMBER_PATTERN.findall(page))
        if core_hits >= 3 and unit_hits > 0:
            structured_candidates.append((index, core_hits, unit_hits, number_hits))
    if structured_candidates:
        anchor = max(
            structured_candidates,
            key=lambda item: (item[1], -item[0], item[2], item[3]),
        )[0]
        return _window(anchor)

    for index, page in enumerate(pages):
        if "目录" in page or "财务报表附注" in page[:160]:
            continue
        marker_hits = sum(
            page.count(marker)
            for marker in ("主要会计数据", "主要财务数据", "主要财务指标")
        )
        label_hits = sum(_has_usable_label(page, label) for label in major_labels)
        if marker_hits == 0 or label_hits == 0 or not any(
            _has_usable_label(page, label) for label in core_labels
        ):
            continue
        unit_hits = sum(1 for _ in UNIT_PATTERN.finditer(page))
        number_hits = len(NUMBER_PATTERN.findall(page))
        table_candidates.append((index, (marker_hits, label_hits, unit_hits, number_hits)))
    if table_candidates:
        anchor = max(
            table_candidates,
            # When the heading/row coverage ties, prefer the earliest page;
            # later pages commonly repeat the heading in explanatory text.
            key=lambda item: (item[1][0], item[1][1], -item[0], item[1][2], item[1][3]),
        )[0]
        return _window(anchor)

    # Some issuer-specific interim templates omit the standard heading but
    # place all major rows in the management-discussion section.  Use a
    # conservative row-label fallback only when at least three major rows and
    # a monetary unit occur on the same page; this recovers those tables
    # without opening the entire report to narrative matches.
    row_labels = (
        "营业收入",
        "归属于上市公司股东的净利润",
        "归属于母公司股东的净利润",
        "归属于本行股东的净利润",
        "归属于本行普通股股东的净利润",
        "基本每股收益",
        "加权平均净资产收益率",
        "资产总额",
        "资产总计",
    )
    row_candidates: list[tuple[int, tuple[int, int, int]]] = []
    for index, page in enumerate(pages):
        if "目录" in page:
            continue
        row_hits = sum(_has_usable_label(page, label) for label in row_labels)
        unit_hits = sum(1 for _ in UNIT_PATTERN.finditer(page))
        number_hits = len(NUMBER_PATTERN.findall(page))
        if row_hits >= 3 and unit_hits > 0:
            row_candidates.append((index, (row_hits, unit_hits, number_hits)))
    if row_candidates:
        anchor = max(
            row_candidates,
            # Prefer the first page when row coverage ties; the following
            # page is often explanatory text or the continuation of the
            # same section rather than the primary data table.
            key=lambda item: (item[1][0], -item[0], item[1][1], item[1][2]),
        )[0]
        return _window(anchor)

    scored = [(index, window_score(index)) for index in range(len(pages))]
    marker_windows = [item for item in scored if item[1][0] > 0]
    if marker_windows:
        # Narrative sections may repeat the words "主要财务指标".  When a
        # real summary marker exists, prefer the earliest high-label window
        # rather than a later narrative/segment table.
        anchor = max(
            marker_windows,
            key=lambda item: (item[1][1], item[1][2], item[1][4], item[1][3]),
        )[0]
    else:
        # If no heading or usable row table was found, do not select a
        # late-note page merely because it repeats two metric substrings.
        # Returning an empty major section is safer than turning a subsidiary
        # disclosure value into the issuer's revenue or total assets.
        fallback = [
            item
            for item in scored
            if item[1][1] >= 2 and item[1][2] > 0 and item[0] < 80
        ]
        if not fallback:
            return ""
        anchor = max(fallback, key=lambda item: item[1])[0]
    return _window(anchor)


def _summary_cashflow_value(
    pages: list[str],
    labels: tuple[str, ...],
    *,
    scaled: bool,
) -> float | None:
    """Read a cash-flow row from a labelled summary table when the statement is unreadable.

    A small number of issuer PDFs contain an embedded-font/corrupted page in
    the consolidated cash-flow statement, while the same annual value is
    printed in the structured ``主要指标`` table.  This fallback is limited to
    pages carrying an explicit summary-table marker; it never searches prose
    or derives a number from a narrative sentence.
    """
    summary_markers = ("主要指标", "主要财务指标", "主要会计数据")
    for page in pages:
        if not any(marker in page for marker in summary_markers):
            continue
        value = _find_value(page, labels, scaled=scaled)
        if value is not None:
            return value
    return None


def _unheaded_cashflow_value(
    pages: list[str],
    labels: tuple[str, ...],
) -> float | None:
    """Read a cash-flow total from a structured page whose title is unreadable."""
    for page in pages:
        if "附注" not in page:
            continue
        if "现金流入小计" not in page or "现金流出小计" not in page:
            continue
        value = _find_value(
            page,
            labels,
            scaled=False,
            skip_references=True,
        )
        if value is not None:
            return value
    return None


def _statement_value(
    pages: list[str],
    section_terms: tuple[str, ...],
    labels: tuple[str, ...],
    *,
    scaled: bool,
    reject_prefixes: tuple[str, ...] = (),
    value_index: int = 0,
) -> float | None:
    def is_heading(page_text: str) -> bool:
        heading_terms = {
            "资产负债表": (
                "合并资产负债表",
                "合并及公司资产负债表",
                "合并及母公司资产负债表",
                "合并及银行资产负债表",
            ),
            "利润表": (
                "合并利润表",
                "合并及公司利润表",
                "合并及母公司利润表",
                "合并及银行利润表",
                "合并年初到报告期末利润表",
            ),
            "现金流量表": (
                "合并现金流量表",
                "合并及公司现金流量表",
                "合并及母公司现金流量表",
                "合并及银行现金流量表",
                "合并年初到报告期末现金流量表",
            ),
        }
        if not any(
            heading in page_text
            for term in section_terms
            for heading in heading_terms.get(term, (term,))
        ):
            return False
        # A table-of-contents page can contain the same heading text.  Only
        # treat a page as a statement heading when it also contains table
        # structure or a target row; this prevents narrative/TOC values from
        # being returned as financial statement values.
        if "目录" in page_text:
            return False
        # Notes frequently quote the words ``合并利润表``/``合并资产负债表``
        # while discussing an accounting policy or tax reconciliation.  The
        # report header exposes these pages as ``财务报表附注``; do not let a
        # following narrative year or footnote number become a statement
        # value.
        note_position = page_text.find("财务报表附注", 0, 160)
        heading_positions = [
            page_text.find(heading)
            for term in section_terms
            for heading in heading_terms.get(term, (term,))
            if page_text.find(heading) >= 0
        ]
        if note_position >= 0 and (
            not heading_positions or min(heading_positions) > note_position
        ):
            return False
        if any(
            page_text.endswith(heading)
            or heading in page_text[max(0, len(page_text) - 200) :]
            for term in section_terms
            for heading in heading_terms.get(term, (term,))
        ):
            # Some issuer templates place the next statement title at the
            # bottom of the preceding balance-sheet page and put the unit
            # header on the following page.  Treat that tail title as a
            # valid heading; contents pages have already been excluded.
            return True
        return bool(
            UNIT_PATTERN.search(page_text)
            or "金额单位" in page_text
            or "附注" in page_text
            or "编制单位" in page_text
        )

    for index, page in enumerate(pages):
        # A statement heading in an audit report or table of contents can be
        # inherited by the next page through the four-page look-back below.
        # Never read numeric values from a contents page (page numbers and
        # section references are not financial statement values).
        if "目录" in page:
            continue
        heading_index = next(
            (
                heading
                for heading in range(index, max(-1, index - 4), -1)
                if is_heading(pages[heading])
            ),
            None,
        )
        if heading_index is None:
            continue
        context = "\n".join(pages[heading_index : index + 1])
        statement_reject_prefixes = reject_prefixes
        if labels == ("负债合计",):
            statement_reject_prefixes = tuple(
                dict.fromkeys((*reject_prefixes, "流动", "非流动"))
            )
        value = _find_value(
            page,
            labels,
            scaled=False,
            reject_prefixes=statement_reject_prefixes,
            value_index=value_index,
            skip_references=True,
        )
        if value is not None:
            scale_markers = list(UNIT_PATTERN.finditer(context))
            scale = UNIT_SCALE[scale_markers[0].group(1)] if scale_markers else 1.0
            return value * scale if scaled else value
    return None


def _member_name(ts_code: str, period_end: str) -> str:
    return f"{ts_code}_{period_end}_{_report_type(period_end)}_original.pdf"


def _load_index(path: Path) -> dict[tuple[str, str], dict[str, str]]:
    frame = pd.read_csv(path, dtype=str).fillna("")
    required = {"ts_code", "period_end", "status", "local_path", "sha256"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"索引缺少字段：{', '.join(sorted(missing))}")
    return {
        (row["ts_code"], row["period_end"]): row
        for row in frame.to_dict(orient="records")
    }


def _income_q3_statement_index(pages: list[str], fallback: int = 0) -> int:
    """Infer the YTD column position from the income-statement header.

    Bank reports use both ``quarter, YTD, prior quarter, prior YTD`` and
    ``quarter, prior quarter, YTD, prior YTD`` layouts.  Looking at the first
    four period headers is safer than assigning one fixed index to all banks.
    """
    for page in pages:
        if "利润表" not in page or "营业收入" not in page:
            continue
        header = page.split("一、营业收入", 1)[0]
        compact_header = re.sub(r"\s+", "", header)
        # Several bank reports put the consolidated YTD pair first, followed
        # by the current-quarter pair (``九个月至三个月``).  Their headers do
        # not contain an explicit ``1-9 月`` token, so the generic regex below
        # cannot infer the position and would otherwise select the quarter.
        if (
            "本集团本行" in compact_header
            and "止九个月至9月30日止三个月" in compact_header
        ):
            return 0
        periods = re.findall(r"(7\s*[-至]\s*9|1\s*[-至]\s*9)\s*月", header)
        if len(periods) < 4:
            continue
        first_four = periods[:4]
        for index, period in enumerate(first_four):
            if period.lstrip().startswith("1"):
                return index
    return fallback


def _parse_report(
    archive: zipfile.ZipFile,
    member: str,
    *,
    ts_code: str,
    period_end: str,
    source_sha256: str,
    cid_map_path: Path | None = None,
) -> dict[str, Any]:
    reader = PdfReader(io.BytesIO(archive.read(member)))
    cid_map = _load_cid_unicode_map(cid_map_path)
    map_key = str(cid_map_path.resolve()) if cid_map_path is not None else ""
    cmap_cache: dict[tuple[str, int], DecodedStreamObject] = {}
    pages: list[str] = []
    for page in reader.pages:
        _inject_cid_to_unicode(page, cid_map, cmap_cache, map_key)
        pages.append(_normalize_pdf_text(page.extract_text() or ""))
    major = _major_page_text(pages)
    values: dict[str, float | None] = {}
    q3_major_index = 3
    if period_end.endswith("-09-30"):
        q3_header = major[:2000]
        if "年初至报告期末" in major and "调整前调整后" in major:
            q3_major_index = 4
        elif (
            re.search(r"\d{4}\s*年\s*7\s*-\s*9\s*月", q3_header)
            and re.search(r"\d{4}\s*年\s*1\s*-\s*9\s*月", q3_header)
            and ("同比变动" in q3_header or "同比变化" in q3_header)
        ):
            # Insurance/financial issuers often print six numeric cells:
            # current quarter, prior quarter, change, current YTD, prior YTD,
            # change.  The current YTD value is therefore index 3.
            q3_major_index = 3
        elif "年初至报告期末" in major or any(
            marker in major
            for marker in (
                "三个月",
                "九个月",
                "3个月",
                "9个月",
                "7 至 9 月比",
                "7至9月比",
                "1 至 9 月比",
                "1至9月比",
                "1-9 月比",
                "1-9月比",
            )
        ):
            q3_major_index = 2
    for field, (labels, scaled) in MAJOR_FIELDS.items():
        major_value_index = q3_major_index
        # Compact bank summary rows use ``current quarter, current YTD,
        # change`` for the combined EPS row; the change is often printed as a
        # parenthesized negative number, so index 2 would select the change.
        if (
            period_end.endswith("-09-30")
            and field == "eps"
            and "基本和稀释每股收益" in major
        ):
            major_value_index = 1
        cumulative_index = (
            major_value_index
            if period_end.endswith("-09-30")
            and field in {"revenue", "net_profit_attributable", "eps", "roe"}
            else 0
        )
        values[field] = _find_value(
            major,
            labels,
            scaled=scaled,
            reject_prefixes=(
                "扣除非经常性损益后的",
                "扣除非经常性损益的",
                "扣除非经常性损益后",
            )
            if field == "eps"
            else (),
            value_index=cumulative_index,
            skip_references=field in {"eps", "roe"},
            prefer_label_order=field == "revenue",
        )
    front_matter = "\n".join(pages[:20])
    for field in ("revenue", "net_profit_attributable", "eps", "roe"):
        if values[field] is None:
            labels, scaled = MAJOR_FIELDS[field]
            values[field] = _find_value(
                front_matter,
                labels,
                scaled=scaled,
                reject_prefixes=(
                    "扣除非经常性损益后的",
                    "扣除非经常性损益的",
                    "扣除非经常性损益后",
                )
                if field == "eps"
                else (),
                value_index=0,
                skip_references=field in {"eps", "roe"},
                prefer_label_order=field == "revenue",
            )
    if values["shareholders_equity"] is None:
        shareholder_labels = (
            *MAJOR_FIELDS["shareholders_equity"][0],
            "归属于母公司所有者权益（或股东权益）合计",
            "归属于母公司所有者权益(或股东权益)合计",
        )
        values["shareholders_equity"] = _statement_value(
            pages,
            ("资产负债表",),
            shareholder_labels,
            scaled=True,
        )
    q3_statement_index = (
        2
        if period_end.endswith("-09-30")
        and any(
            (
                ("3 个月" in page and "9 个月" in page)
                or ("3个月" in page and "9个月" in page)
                or ("三个月" in page and "九个月" in page)
            )
            for page in pages
        )
        else 0
    )
    if period_end.endswith("-09-30"):
        # Some banks publish four statement columns (current quarter, YTD,
        # prior quarter, prior YTD).  For these layouts the cumulative value
        # is index 1; two-column cash-flow statements still fall back to the
        # first value in _pick_values.
        income_q3_statement_index = _income_q3_statement_index(
            pages, fallback=q3_statement_index
        )
    else:
        income_q3_statement_index = q3_statement_index
    raw_statement_values: dict[str, float | None] = {}
    for field, (sections, labels, scaled) in STATEMENT_FIELDS.items():
        # Income statements can expose quarter/YTD columns in different
        # orders, so use the header-inferred index above.  Cash-flow
        # statements are cumulative YTD tables; when a bank prints both
        # group and parent-company columns, the first column is the
        # consolidated value consumed by the provider schema.
        statement_value_index = (
            income_q3_statement_index
            if field in {"operating_profit", "net_profit"}
            else 0
        )
        values[field] = _statement_value(
            pages,
            sections,
            labels,
            scaled=scaled,
            reject_prefixes=STATEMENT_REJECT_PREFIXES.get(field, ()),
            value_index=statement_value_index,
        )
        raw_statement_values[field] = _statement_value(
            pages,
            sections,
            labels,
            scaled=False,
            reject_prefixes=STATEMENT_REJECT_PREFIXES.get(field, ()),
            value_index=statement_value_index,
        )
    # Some PDFs expose a readable, labelled summary cash-flow table even when
    # the consolidated statement page has an unusable embedded font.  Recover
    # only the missing cash-flow field from that table; no narrative fallback
    # is allowed.
    for field in ("operating_cf", "investing_cf", "financing_cf"):
        if values[field] is not None:
            continue
        _sections, labels, scaled = STATEMENT_FIELDS[field]
        summary_value = _summary_cashflow_value(pages, labels, scaled=scaled)
        if summary_value is None:
            continue
        values[field] = summary_value
        raw_statement_values[field] = _summary_cashflow_value(
            pages, labels, scaled=False
        )
    for field in ("operating_cf", "investing_cf", "financing_cf"):
        if values[field] is not None:
            continue
        _sections, labels, _scaled = STATEMENT_FIELDS[field]
        unheaded_value = _unheaded_cashflow_value(pages, labels)
        if unheaded_value is None:
            continue
        # Keep this in raw units; the normal statement scale inference below
        # uses the already parsed total-assets value to apply the report's
        # power-of-ten unit consistently.
        values[field] = unheaded_value
        raw_statement_values[field] = unheaded_value
    # Annual bank reports may have a corrupted-font statement page while the
    # preceding structured ``全年业绩`` summary remains readable.  Recover
    # only fields with an unambiguous summary row; investing/financing cash
    # flow are deliberately excluded because those rows are usually absent
    # from the summary and would otherwise invite narrative matches.
    major_fallbacks = {
        "operating_profit": (("营业利润",), True, ()),
        "total_liabilities": (("负债合计", "负债总额"), True, ()),
        "shareholders_equity": (
            (
                "归属于本行股东权益",
                "归属于本行股东权益合计",
                "归属于母公司股东权益",
            ),
            True,
            (),
        ),
        "net_profit": (("净利润",), True, ("归属于", "少数")),
        "operating_cf": (("经营活动产生的现金流量净额",), True, ()),
    }
    for field, (labels, scaled, reject_prefixes) in major_fallbacks.items():
        if values[field] is not None:
            continue
        value = _find_value(
            major,
            labels,
            scaled=scaled,
            reject_prefixes=reject_prefixes,
        )
        if value is None:
            continue
        values[field] = value
        raw_statement_values[field] = _find_value(
            major,
            labels,
            scaled=False,
            reject_prefixes=reject_prefixes,
        )
    # Summary tables are often rounded to thousands/millions while the
    # consolidated income statement retains exact yuan.  Prefer the exact
    # attributable-profit statement value when it is close to the summary
    # value; a large deviation remains untouched for manual review.
    exact_attributable = _statement_value(
        pages,
        ("利润表",),
        (
            "归属于上市公司股东的净利润",
            "归属于母公司所有者的净利润",
            "归属于母公司股东的净利润",
            "归属于本行股东的净利润",
        ),
        scaled=True,
    )
    summary_attributable = values.get("net_profit_attributable")
    if (
        exact_attributable is not None
        and summary_attributable is not None
        and abs(exact_attributable - summary_attributable)
        <= max(1.0, abs(exact_attributable) * 0.01)
    ):
        values["net_profit_attributable"] = exact_attributable
    # A few issuer templates omit the financial-statement unit while still
    # presenting amounts in thousands of yuan (BYD is a representative
    # example).  Infer only a standard power-of-ten multiplier from the
    # already parsed major-table total-assets value, then apply it to
    # statement-only fields.  Ambiguous ratios are left untouched.  Apply the
    # inferred multiplier per field: some templates expose an explicit unit
    # for liabilities but omit it for the cash-flow and profit rows on the
    # same page, so multiplying every statement field would double-scale the
    # already-normalized values.
    # Keep the primary statement label separate from the broader fallback:
    # in some reports ``资产合计`` appears earlier in a subsidiary/note table
    # on the same page, which would otherwise win by text position.
    raw_assets = _statement_value(
        pages,
        ("资产负债表",),
        ("资产总计",),
        scaled=False,
    )
    if raw_assets is None:
        raw_assets = _statement_value(
            pages,
            ("资产负债表",),
            ("资产合计",),
            scaled=False,
        )
    if raw_assets is None:
        raw_assets = _find_value(
            major,
            MAJOR_FIELDS["total_assets"][0],
            scaled=False,
        )
    if raw_assets and values.get("total_assets"):
        inferred = values["total_assets"] / raw_assets
        standard_scales = (1.0, 1_000.0, 10_000.0, 1_000_000.0, 100_000_000.0)
        scale_hint = next(
            (
                scale
                for scale in standard_scales
                if abs(inferred - scale) <= max(1.0, abs(scale) * 1e-9)
            ),
            None,
        )
        if scale_hint and scale_hint != 1.0:
            for field, raw_value in raw_statement_values.items():
                current_value = values.get(field)
                if raw_value is None or current_value is None:
                    continue
                if abs(current_value - raw_value) <= max(1.0, abs(raw_value) * 1e-9):
                    values[field] = current_value * scale_hint
    if values["total_assets"] is None:
        values["total_assets"] = _statement_value(
            pages,
            ("资产负债表",),
            ("资产总计", "资产合计"),
            scaled=True,
        )
    if values["shareholders_equity"] is None:
        values["shareholders_equity"] = _statement_value(
            pages,
            ("资产负债表",),
            (
                "归属于母公司所有者权益合计",
                "归属于母公司股东权益合计",
                "归属于母公司所有者权益",
                "归属于母公司股东权益",
            ),
            scaled=True,
        )
    if values["net_profit_attributable"] is None:
        values["net_profit_attributable"] = _statement_value(
            pages,
            ("利润表",),
            (
                "归属于母公司所有者的净利润",
                "归属于母公司股东的净利润",
                "归属于上市公司股东的净利润",
            ),
            scaled=True,
        )
    # ROA is deliberately not scraped from free text; it is recomputed later
    # from the official net profit and average assets when both balance dates
    # are available.
    values["roa"] = None
    missing = [
        field for field, value in values.items() if value is None and field != "roa"
    ]
    return {
        "ts_code": ts_code,
        "period_end": period_end,
        "report_type": _report_type(period_end),
        "source": "cninfo_pdf",
        "source_file": member,
        "source_sha256": source_sha256,
        "parser_version": PARSER_VERSION,
        "cid_map": map_key,
        "page_count": len(pages),
        "status": "parsed" if not missing else "partial",
        "missing_fields": ",".join(missing),
        **values,
    }


def _write_dataset(
    output_dir: Path,
    name: str,
    rows: list[dict[str, Any]],
    columns: list[str],
) -> None:
    frame = pd.DataFrame(rows)
    if frame.empty:
        frame = pd.DataFrame(columns=columns)
    else:
        for column in columns:
            if column not in frame:
                frame[column] = pd.NA
        frame = frame[columns]
    frame.to_csv(output_dir / f"{name}.csv", index=False, encoding="utf-8-sig")


def _parse_report_task(task: tuple[str, str, str, str, str, str]) -> dict[str, Any]:
    """Worker entry point; each worker owns its ZIP handle and PDF reader."""
    archive_path, member, ts_code, period_end, source_sha256, cid_map_path = task
    logging.disable(logging.WARNING)
    with zipfile.ZipFile(archive_path) as archive:
        return _parse_report(
            archive,
            member,
            ts_code=ts_code,
            period_end=period_end,
            source_sha256=source_sha256,
            cid_map_path=Path(cid_map_path) if cid_map_path else None,
        )


def extract_reports(
    index_path: Path,
    archive_path: Path,
    output_dir: Path,
    *,
    workers: int = 4,
    ts_codes: set[str] | None = None,
    period_ends: set[str] | None = None,
    report_keys: set[tuple[str, str]] | None = None,
    cid_map_path: Path | None = None,
) -> dict[str, Any]:
    index = _load_index(index_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    archive_path = archive_path.resolve()
    with zipfile.ZipFile(archive_path) as archive:
        available = {
            Path(info.filename).name
            for info in archive.infolist()
            if not info.is_dir()
        }
    tasks: list[tuple[str, str, str, str, str]] = []
    for (ts_code, period_end), row in sorted(index.items()):
        if report_keys is not None and (ts_code, period_end) not in report_keys:
            continue
        if ts_codes and ts_code not in ts_codes:
            continue
        if period_ends and period_end not in period_ends:
            continue
        if row["status"] != "provided":
            continue
        member = _member_name(ts_code, period_end)
        if member in available:
            tasks.append(
                (
                    str(archive_path),
                    member,
                    ts_code,
                    period_end,
                    row["sha256"],
                    str(cid_map_path.resolve()) if cid_map_path is not None else "",
                )
            )

    parsed: list[dict[str, Any]] = []
    worker_count = max(1, int(workers))
    with concurrent.futures.ProcessPoolExecutor(max_workers=worker_count) as executor:
        futures = [executor.submit(_parse_report_task, task) for task in tasks]
        for index_number, future in enumerate(concurrent.futures.as_completed(futures), 1):
            parsed.append(future.result())
            if index_number % 10 == 0 or index_number == len(futures):
                print(f"extracted {index_number}/{len(futures)} reports", flush=True)
    parsed.sort(key=lambda row: (row["ts_code"], row["period_end"]))

    base_columns = ["ts_code", "ann_date", "f_ann_date", "end_date", "report_type", "update_flag"]
    common = {
        **{column: "" for column in base_columns},
        "update_flag": 0,
    }
    income_rows = []
    balance_rows = []
    cashflow_rows = []
    indicator_rows = []
    for row in parsed:
        base = {
            **common,
            "ts_code": row["ts_code"],
            "end_date": row["period_end"],
            "report_type": 1,
        }
        income_rows.append(
            {
                **base,
                "revenue": row["revenue"],
                "operate_profit": row["operating_profit"],
                "n_income": row["net_profit"],
                "n_income_attr_p": row["net_profit_attributable"],
                "basic_eps": row["eps"],
                "net_profit": row["net_profit"],
            }
        )
        balance_rows.append(
            {
                **base,
                "total_assets": row["total_assets"],
                "total_liab": row["total_liabilities"],
                "total_hldr_eqy_exc_min_int": row["shareholders_equity"],
            }
        )
        cashflow_rows.append(
            {
                **base,
                "n_cashflow_act": row["operating_cf"],
                "n_cashflow_inv_act": row["investing_cf"],
                "n_cash_flows_fnc_act": row["financing_cf"],
            }
        )
        indicator_rows.append(
            {
                **base,
                "eps": row["eps"],
                "roe": row["roe"],
                "roa": row["roa"],
            }
        )

    _write_dataset(
        output_dir,
        "income",
        income_rows,
        base_columns
        + ["revenue", "operate_profit", "n_income", "n_income_attr_p", "basic_eps", "net_profit"],
    )
    _write_dataset(
        output_dir,
        "balance_sheet",
        balance_rows,
        base_columns + ["total_assets", "total_liab", "total_hldr_eqy_exc_min_int"],
    )
    _write_dataset(
        output_dir,
        "cashflow",
        cashflow_rows,
        base_columns + ["n_cashflow_act", "n_cashflow_inv_act", "n_cash_flows_fnc_act"],
    )
    _write_dataset(
        output_dir,
        "fina_indicator",
        indicator_rows,
        base_columns + ["eps", "roe", "roa"],
    )
    cid_map_metadata: dict[str, Any] = {"path": "", "sha256": "", "row_count": 0}
    if cid_map_path is not None:
        cid_map = _load_cid_unicode_map(cid_map_path)
        cid_map_metadata = {
            "path": str(cid_map_path.resolve()),
            "sha256": hashlib.sha256(cid_map_path.read_bytes()).hexdigest(),
            "row_count": len(cid_map),
        }
    manifest = {
        "source": "cninfo_pdf",
        "parser_version": PARSER_VERSION,
        "cid_map": cid_map_metadata,
        "index": str(index_path.resolve()),
        "archive": str(archive_path.resolve()),
        "report_count": len(parsed),
        "filters": {
            "ts_codes": sorted(ts_codes) if ts_codes else [],
            "period_ends": sorted(period_ends) if period_ends else [],
            "report_keys": (
                [f"{ts_code}|{period_end}" for ts_code, period_end in sorted(report_keys)]
                if report_keys is not None
                else []
            ),
        },
        "status_counts": {
            status: sum(row["status"] == status for row in parsed)
            for status in sorted({row["status"] for row in parsed})
        },
        "missing_field_counts": {
            field: sum(field in row["missing_fields"].split(",") for row in parsed)
            for field in sorted(
                {
                    field
                    for row in parsed
                    for field in row["missing_fields"].split(",")
                    if field
                }
            )
        },
        "pit_note": "公告日期未从资料包中提取；本批仅做报告期覆盖和值字段比对。",
    }
    (output_dir / "official_financials.csv").write_text(
        pd.DataFrame(parsed).to_csv(index=False),
        encoding="utf-8-sig",
    )
    (output_dir / "extraction_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=4, help="PDF 解析进程数，默认 4")
    parser.add_argument(
        "--cid-map",
        type=Path,
        help=(
            "可选 Adobe-GB1/Adobe-CNS1 cid2code.txt；仅用于缺少 ToUnicode 的嵌入 CFF 字体，"
            "不填则保持保守缺失策略"
        ),
    )
    parser.add_argument(
        "--ts-code",
        action="append",
        default=[],
        help="只解析指定证券，可重复传入；默认解析索引中的全部证券",
    )
    parser.add_argument(
        "--period-end",
        action="append",
        default=[],
        help="只解析指定报告期（YYYY-MM-DD），可重复传入",
    )
    parser.add_argument(
        "--selection-file",
        type=Path,
        help="读取 audit-selected-batch.csv 的精确 ts_code + period_end 组合",
    )
    return parser


def _load_selection_file(path: Path | None) -> set[tuple[str, str]] | None:
    if path is None:
        return None
    frame = pd.read_csv(path)
    required = {"ts_code", "period_end"}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise SystemExit(f"selection 文件缺少列：{', '.join(missing)}")
    return {
        (str(row.ts_code), str(row.period_end))
        for row in frame[["ts_code", "period_end"]].itertuples(index=False)
    }


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    report_keys = _load_selection_file(args.selection_file)
    manifest = extract_reports(
        args.index,
        args.archive,
        args.output_dir,
        workers=args.workers,
        ts_codes=set(args.ts_code),
        period_ends=set(args.period_end),
        report_keys=report_keys,
        cid_map_path=args.cid_map,
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
