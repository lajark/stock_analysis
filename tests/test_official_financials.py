"""官方 PDF 财务字段抽取的回归测试。"""

from scripts.extract_official_financials import (
    _find_value,
    _income_q3_statement_index,
    _major_page_text,
    _normalize_pdf_text,
    _pick_values,
    _statement_value,
    _summary_cashflow_value,
    _unheaded_cashflow_value,
)


def test_pick_values_skips_short_parenthesized_footnote() -> None:
    text = "基本每股收益（元／股）(1) 1.51 1.54 -1.95"

    assert _pick_values(text, 0, "基本每股收益") == 1.51


def test_pick_values_keeps_parenthesized_financial_negative() -> None:
    text = "经营活动产生的现金流量净额 (1,208) (12,618) 90.43"

    assert _pick_values(text, 0, "经营活动产生的现金流量净额") == -1208.0


def test_pick_values_handles_pdf_parenthesis_spacing_and_full_width() -> None:
    assert (
        _pick_values(
            "投资活动使用的现金流量净额 (129,082,282  ) (125,663,644 )",
            0,
            "投资活动使用的现金流量净额",
        )
        == -129_082_282.0
    )


def test_pick_values_skips_full_width_parenthesized_footnote() -> None:
    text = "加权平均净资产收益率\n（2） 9.88 10.66"

    assert _pick_values(text, 0, "加权平均净资产收益率", skip_references=True) == 9.88


def test_pick_values_skips_statement_note_suffix_before_amount() -> None:
    text = "经营活动产生的现金流量净额(附注四、44) 1,417,002 1,404,657"

    assert (
        _pick_values(
            text,
            0,
            "经营活动产生的现金流量净额",
            skip_references=True,
        )
        == 1_417_002.0
    )


def test_find_value_prefers_basic_eps_over_deducted_eps() -> None:
    text = (
        "基本和稀释每股收益（人民币元） 0.18 0.32\n"
        "扣除非经常性损益后基本每股收益 0.19 1.25"
    )

    assert (
        _find_value(
            text,
            ("基本和稀释每股收益", "基本每股收益"),
            scaled=False,
            reject_prefixes=(
                "扣除非经常性损益后的",
                "扣除非经常性损益的",
                "扣除非经常性损益后",
            ),
        )
        == 0.18
    )


def test_pick_values_uses_ytd_when_q3_has_only_two_numeric_columns() -> None:
    text = "基本每股收益（元/股） -0.001 不适用 0.000 不适用"

    assert _pick_values(text, 0, "基本每股收益", value_index=2) == 0.0
    assert (
        _pick_values(
            "投资活动使用的现金流量净额 （22,659,014,171） （26,260,222,219）",
            0,
            "投资活动使用的现金流量净额",
        )
        == -22_659_014_171.0
    )


def test_pick_values_uses_ytd_column_in_six_value_q3_table() -> None:
    text = "营业收入（人民币百万元） 332,864 280,417 18.7 832,940 775,383 7.4"

    assert _pick_values(text, 0, "营业收入", value_index=3) == 832_940.0


def test_pick_values_uses_ytd_when_q3_change_cell_is_textual() -> None:
    text = "归属于上市公司股东的净利润 -3,819,080.55 不适用 13,082,309.31 160.84"

    assert (
        _pick_values(
            text,
            0,
            "归属于上市公司股东的净利润",
            value_index=2,
        )
        == 13_082_309.31
    )


def test_pick_values_stops_at_two_textual_q3_change_cells() -> None:
    text = (
        "归属于上市公司股东的净利润 -10,744,788.24 不适用 "
        "5,015,537.60 不适用归属于上市公司股东的扣除非经常性损益的净利润 "
        "-71,387,593.90 不适用"
    )

    assert (
        _pick_values(
            text,
            0,
            "归属于上市公司股东的净利润",
            value_index=2,
        )
        == 5_015_537.60
    )


def test_major_page_selection_skips_table_of_contents() -> None:
    pages = [
        "目录\n主要会计数据和财务指标",
        "公司简介和经营情况",
        "主要会计数据和财务指标\n单位：百万元\n营业收入 100",
    ]

    selected = _major_page_text(pages)

    assert "营业收入 100" in selected


def test_major_page_selection_prefers_table_over_preceding_chart() -> None:
    pages = [
        "业绩概览\n营业收入 8,066.51\n资产总额 8,066.51",
        "一、近三年主要会计数据和财务指标\n"
        "单位：元\n营业收入 36,577,585,349.48\n资产总额 905,508,388,594.64",
    ]

    selected = _major_page_text(pages)

    assert "36,577,585,349.48" in selected
    assert "8,066.51" not in selected


def test_major_page_selection_supports_interim_row_table_without_heading() -> None:
    pages = [
        "公司简介和主要财务指标\n基本每股收益 0.80\n加权平均净资产收益率 4.30",
        "报告期内主要经营情况\n单位：元\n营业收入 16,219,421,678.66\n"
        "归属于母公司股东的净利润 7,549,447,367.16\n基本每股收益 0.80\n"
        "加权平均净资产收益率 4.30",
    ]

    selected = _major_page_text(pages)

    assert "16,219,421,678.66" in selected


def test_major_page_selection_includes_annual_table_continuation_before_quarterly_table() -> None:
    pages = [
        "六、主要会计数据和财务指标\n单位：元\n"
        "2024年 2023年 营业收入（元） 777,102,455,000.00 602,315,354,000.00",
        "主要会计数据\n归属于上市公司股东的净利润（元） 40,254,346,000.00 30,040,811,000.00\n"
        "八、分季度主要财务指标\n第一季度 营业收入 124,944,397,000.00",
    ]

    selected = _major_page_text(pages)

    assert selected.index("777,102,455,000.00") < selected.index("124,944,397,000.00")
    assert _find_value(selected, ("营业收入",), scaled=True) == 777_102_455_000.0


def test_statement_value_uses_statement_unit_before_eps_unit() -> None:
    pages = [
        "合并利润表\n（除特别注明外，货币单位均以人民币百万元列示）\n"
        "营业利润 45185\n基本每股收益（人民币元） 1.51"
    ]

    value = _statement_value(pages, ("利润表",), ("营业利润",), scaled=True)

    assert value == 45_185_000_000.0


def test_statement_value_accepts_parent_company_statement_heading() -> None:
    pages = [
        "合并及母公司资产负债表\n（货币单位：元）",
        "负债合计 723,290,956,625.83",
        "附注：金融负债合计 (335,595,861,287.50)",
    ]

    value = _statement_value(pages, ("资产负债表",), ("负债合计",), scaled=True)

    assert value == 723_290_956_625.83


def test_statement_value_accepts_bank_statement_heading() -> None:
    pages = [
        "合并及银行资产负债表\n（货币单位：人民币百万元）\n负债合计 39,158,056",
        "合并及银行利润表\n（货币单位：人民币百万元）\n三、营业利润 79,647",
        "合并及银行现金流量表\n（货币单位：人民币百万元）\n"
        "经营活动产生的现金流量净额 783,563",
    ]

    assert (
        _statement_value(
            pages,
            ("资产负债表",),
            ("负债合计",),
            scaled=True,
        )
        == 39_158_056_000_000.0
    )
    assert (
        _statement_value(
            pages,
            ("利润表",),
            ("营业利润",),
            scaled=True,
        )
        == 79_647_000_000.0
    )
    assert (
        _statement_value(
            pages,
            ("现金流量表",),
            ("经营活动产生的现金流量净额",),
            scaled=True,
        )
        == 783_563_000_000.0
    )
    assert (
        _statement_value(
            [
                "合并及银行现金流量表\n（货币单位：人民币百万元）\n"
                "筹资活动产生 / (使用) 的现金流量净额 63,340"
            ],
            ("现金流量表",),
            (
                "筹资活动产生/(使用)的现金流量净额",
                "筹资活动产生 / (使用) 的现金流量净额",
            ),
            scaled=True,
        )
        == 63_340_000_000.0
    )
    assert (
        _statement_value(
            [
                "合并及银行现金流量表\n（货币单位：人民币百万元）\n"
                "经营活动 (使用) /产生的现金流量净额 (768,830)"
            ],
            ("现金流量表",),
            (
                "经营活动产生的现金流量净额",
                "经营活动 (使用) /产生的现金流量净额",
            ),
            scaled=True,
        )
        == -768_830_000_000.0
    )
    assert (
        _statement_value(
            [
                "合并及银行现金流量表\n（货币单位：人民币元）\n"
                "经营活动产生 / ( 使用 ) 的现金流量净额 13,319,223,794"
            ],
            ("现金流量表",),
            (
                "经营活动产生 / ( 使用 ) 的现金流量净额",
                "经营活动产生的现金流量净额",
            ),
            scaled=False,
        )
        == 13_319_223_794.0
    )
    assert (
        _statement_value(
            [
                "合并及银行现金流量表\n（货币单位：人民币元）\n"
                "投资活动 ( 使用 )/ 产生的现金流量净额 (14,017,694,055.68)"
            ],
            ("现金流量表",),
            (
                "投资活动 ( 使用 )/ 产生的现金流量净额",
                "投资活动产生的现金流量净额",
            ),
            scaled=False,
        )
        == -14_017_694_055.68
    )
    assert (
        _statement_value(
            [
                "合并及银行现金流量表\n（货币单位：人民币元）\n"
                "筹资活动产生 / （使用）的现金流量净额 5,942,905,527"
            ],
            ("现金流量表",),
            (
                "筹资活动产生 / （使用）的现金流量净额",
                "筹资活动产生的现金流量净额",
            ),
            scaled=False,
        )
        == 5_942_905_527.0
    )
    assert (
        _statement_value(
            [
                "合并及银行现金流量表\n（货币单位：人民币元）\n"
                "筹资活动产生 ╱ （使用） 的现金流量净额 30,951"
            ],
            ("现金流量表",),
            (
                "筹资活动产生 ╱ （使用） 的现金流量净额",
                "筹资活动产生的现金流量净额",
            ),
            scaled=False,
        )
        == 30_951.0
    )

    assert (
        _statement_value(
            [
                "4、合并现金流量表后附财务报表附注为本财务报表的组成部分\n"
                "附注七\n",
                "筹资活动产生/(使用)的现金流量净额 (12,358,424)",
            ],
            ("现金流量表",),
            ("筹资活动产生/(使用)的现金流量净额",),
            scaled=False,
        )
        == -12_358_424.0
    )
    assert (
        _statement_value(
            [
                "资产负债表结束\n3、合并利润表",
                "单位：元\n营业利润 2,206,067,517",
            ],
            ("利润表",),
            ("营业利润",),
            scaled=False,
        )
        == 2_206_067_517.0
    )


def test_summary_cashflow_value_requires_a_structured_summary_marker() -> None:
    labels = ("筹资活动产生的现金流量净额",)

    assert (
        _summary_cashflow_value(
            [
                "现金流分析：筹资活动产生的现金流量净额为人民币 48.28 亿元",
                "第九节\n主要指标\n单位：元\n"
                "筹资活动产生的现金流量净额 48,281,426,033.51 -",
            ],
            labels,
            scaled=False,
        )
        == 48_281_426_033.51
    )
    assert (
        _summary_cashflow_value(
            ["现金流分析：筹资活动产生的现金流量净额为人民币 48.28 亿元"],
            labels,
            scaled=False,
        )
        is None
    )


def test_statement_value_rejects_note_page_heading() -> None:
    pages = [
        "某公司 2023 年度财务报表附注\n"
        "（除特别注明外，金额单位为人民币千元）\n"
        "合并利润表相关会计政策说明",
        "净利润除以调整后的本公司发行在外普通股的加权平均数计算。\n"
        "于 2023 年度不存在稀释性潜在普通股。",
    ]

    assert _statement_value(pages, ("利润表",), ("净利润",), scaled=True) is None


def test_find_value_rejects_long_attributable_profit_prefix() -> None:
    text = "归属于上市公司股东的净利润 11,869,693\n五、净利润 38,637,827"

    assert (
        _find_value(
            text,
            ("净利润",),
            scaled=False,
            reject_prefixes=("归属于", "少数"),
        )
        == 38_637_827.0
    )


def test_statement_value_applies_profit_reject_prefixes() -> None:
    pages = [
        "合并利润表\n（货币单位：人民币千元）",
        "归属于上市公司股东的净利润 11,869,693\n五、净利润 38,637,827",
    ]

    assert (
        _statement_value(
            pages,
            ("利润表",),
            ("净利润",),
            scaled=True,
            reject_prefixes=("归属于", "少数"),
        )
        == 38_637_827_000.0
    )


def test_statement_value_rejects_pre_tax_operating_cashflow() -> None:
    pages = [
        "合并现金流量表\n（货币单位：人民币百万元）",
        "所得税前经营活动产生的现金流量净额 973,538\n"
        "经营活动产生的现金流量净额 942,479",
    ]

    assert (
        _statement_value(
            pages,
            ("现金流量表",),
            ("经营活动产生的现金流量净额",),
            scaled=True,
            reject_prefixes=("所得税前",),
        )
        == 942_479_000_000.0
    )


def test_pick_values_rejoins_spaces_after_thousands_separator() -> None:
    text = "营业收入 913, 789 1,110, 568 880, 355"

    assert _pick_values(text, 0, "营业收入") == 913789.0


def test_pick_values_rejoins_spaces_on_both_sides_of_separator() -> None:
    text = "营业收入 36,577 ,585,349.48 32,031,562,088.09"

    assert _pick_values(text, 0, "营业收入") == 36_577_585_349.48


def test_find_value_reads_unit_after_metric_label() -> None:
    text = "营业收入（人民币百万元） 245,569"

    assert _find_value(text, ("营业收入",), scaled=True) == 245_569_000_000.0


def test_find_value_can_prefer_narrow_revenue_over_total_revenue() -> None:
    text = "单位：千元\n营业总收入 114,797,077\n营业收入 114,218,209"

    assert (
        _find_value(
            text,
            ("营业收入", "营业总收入"),
            scaled=True,
            prefer_label_order=True,
        )
        == 114_218_209_000.0
    )


def test_find_value_does_not_use_following_per_share_unit_for_amount() -> None:
    text = (
        "单位：百万元人民币\n"
        "归属于母公司所有者权益合计 2,648,821 股本 294,388 "
        "每股净资产（元） 7.78"
    )

    assert (
        _find_value(
            text,
            ("归属于母公司所有者权益合计",),
            scaled=True,
        )
        == 2_648_821_000_000.0
    )


def test_statement_value_skips_directory_page_after_inherited_heading() -> None:
    pages = [
        "审阅报告……合并及母公司利润表……编制单位：某公司",
        "目录\n合并及母公司利润表 73\n净利润 170",
        "合并及母公司利润表（金额单位：百万元）\n五、净利润 126,536",
    ]

    assert (
        _statement_value(
            pages,
            ("利润表",),
            ("五、净利润", "净利润"),
            scaled=True,
            reject_prefixes=("归属于", "少数"),
        )
        == 126_536_000_000.0
    )


def test_income_q3_statement_index_reads_bank_header_column_order() -> None:
    assert (
        _income_q3_statement_index(
            [
                "合并及母公司利润表 2024年 2023年 2024年 2023年 "
                "7-9 月 7-9 月 1-9 月 1-9 月 一、营业收入"
            ]
        )
        == 2
    )
    assert (
        _income_q3_statement_index(
            [
                "合并利润表 2024年 2024年 2023年 2023年 "
                "7-9 月 1-9 月 7-9 月 1-9 月 一、营业收入"
            ]
        )
        == 1
    )
    assert (
        _income_q3_statement_index(
            [
                "合并利润表\n2025年 2025年 2024年 2024年\n"
                "7至9月 1至9月 7至9月 1至9月\n营业收入"
            ],
            fallback=2,
        )
        == 1
    )
    assert (
        _income_q3_statement_index(
            [
                "合并及银行利润表\n本集团本行\n"
                "截至自7月1日截至自7月1日"
                "9月30日止九个月至9月30日止三个月\n营业收入"
            ],
            fallback=2,
        )
        == 0
    )


def test_q3_compact_bank_eps_row_uses_ytd_value_before_change() -> None:
    assert (
        _find_value(
            "基本和稀释每股收益（人民币元） 0.35 - 1.01 (0.98)",
            ("基本和稀释每股收益",),
            scaled=False,
            value_index=1,
        )
        == 1.01
    )


def test_bank_cashflow_aliases_cover_used_and_generated_variants() -> None:
    page = (
        "合并及银行现金流量表\n（货币单位：人民币百万元）\n"
        "投资活动所用的现金流量净额 (135,041)\n"
        "筹资活动产生/(所用)的现金流量净额 105,108"
    )

    assert (
        _statement_value(
            [page],
            ("现金流量表",),
            (
                "投资活动所用的现金流量净额",
                "投资活动产生的现金流量净额",
            ),
            scaled=True,
        )
        == -135_041_000_000.0
    )
    assert (
        _statement_value(
            [page],
            ("现金流量表",),
            (
                "筹资活动产生/(所用)的现金流量净额",
                "筹资活动产生的现金流量净额",
            ),
            scaled=True,
        )
        == 105_108_000_000.0
    )


def test_unheaded_cashflow_value_requires_cashflow_table_totals() -> None:
    assert (
        _unheaded_cashflow_value(
            [
                "附注 2024年 2023年\n"
                "投资活动现金流入小计 2,994,077 2,195,781\n"
                "投资活动现金流出小计 (3,686,709) (3,017,035)\n"
                "投资活动所用的现金流量净额 (692,632) (821,254)"
            ],
            ("投资活动所用的现金流量净额",),
        )
        == -692_632.0
    )
    assert (
        _unheaded_cashflow_value(
            ["附注\n投资活动所用的现金流量净额 (692,632)"],
            ("投资活动所用的现金流量净额",),
        )
        is None
    )


def test_find_value_skips_plain_eps_footnote_marker() -> None:
    text = "基本每股收益 3 0.72 0.69 0.65"

    assert _find_value(text, ("基本每股收益",), scaled=False, skip_references=True) == 0.72


def test_normalize_pdf_text_joins_wrapped_chinese_label() -> None:
    assert _normalize_pdf_text("归属于母公司股\n东的净利润") == "归属于母公司股东的净利润"


def test_normalize_pdf_text_maps_traditional_accounting_labels() -> None:
    normalized = _normalize_pdf_text(
        "合併財務狀況表\n資產總額（人民幣百萬元）\n"
        "籌資活動(所用)/產生的現金流量淨額"
    )

    assert "合并资产负债表" in normalized
    assert "资产总额（人民币百万元）" in normalized
    assert "筹资活动(所用)/产生的现金流量净额" in normalized


def test_normalize_pdf_text_maps_mixed_traditional_ifrs_labels() -> None:
    normalized = _normalize_pdf_text(
        "營業收入\n归属於母公司股東的净利润\n"
        "加權平均权益回報率（%，年化）\n歸屬於母公司股東的權益\n"
        "所得稅前經營活動產生的現金流量淨額"
    )

    assert "营业收入" in normalized
    assert "归属于母公司股东的净利润" in normalized
    assert "加权平均净资产收益率（%，年化）" in normalized
    assert "归属于母公司股东的权益" in normalized
    assert "所得税前经营活动产生的现金流量净额" in normalized


def test_find_value_does_not_confuse_average_total_assets() -> None:
    text = "平均总资产回报率 0.73\n年末总资产 39,872,989"

    assert _find_value(text, ("总资产",), scaled=False) == 39_872_989.0


def test_find_value_prefers_table_sized_candidate_over_ratio_note() -> None:
    text = (
        "资产总额的平均值 4.0\n"
        "（除特别注明外，以人民币百万元列示）\n"
        "资产总额 44,432,848 40,571,149"
    )

    assert _find_value(text, ("资产总额",), scaled=True) == 44_432_848_000_000.0


def test_find_value_ignores_embedded_ratio_labels() -> None:
    text = (
        "（人民币百万元）\n"
        "手续费净收入对营业收入比率 13.99\n"
        "总权益对资产总额比率 8.24\n"
        "全年度业绩营业收入 750,151\n"
        "报告期末资产总额 40,571,149"
    )

    assert _find_value(text, ("营业收入",), scaled=True) == 750_151_000_000.0
    assert _find_value(text, ("资产总额",), scaled=True) == 40_571_149_000_000.0


def test_find_value_prefers_specific_overlapping_roe_label() -> None:
    text = (
        "净资产收益率和每股收益的计算及披露（2010年修订）\n"
        "加权平均净资产收益率2 10.69 11.56"
    )

    assert (
        _find_value(
            text,
            ("加权平均净资产收益率", "净资产收益率"),
            scaled=False,
            skip_references=True,
        )
        == 10.69
    )


def test_major_page_selection_prefers_bank_summary_over_ratio_page() -> None:
    pages = [
        "主要财务指标\n手续费净收入对营业收入比率 13.99\n"
        "总权益对资产总额比率 8.24\n",
        "本年度报告财务资料（人民币百万元）\n"
        "全年度业绩营业收入 750,151\n净利润 336,282\n"
        "归属于本行股东的净利润 335,577\n资产总额 40,571,149",
    ]

    selected = _major_page_text(pages)

    assert "750,151" in selected
    assert _find_value(selected, ("营业收入",), scaled=True) == 750_151_000_000.0


def test_major_page_selection_supports_bank_asset_total_alias() -> None:
    pages = [
        "财务摘要\n单位：百万元人民币\n营业收入 317,076\n"
        "归属于母公司所有者的净利润 118,601\n资产总计 33,907,267",
        "经营情况概览\n集团资产总计339,072.67亿元，营业收入3,170.76亿元",
    ]

    selected = _major_page_text(pages)

    assert "33,907,267" in selected
    assert _find_value(selected, ("资产总计",), scaled=True) == 33_907_267_000_000.0


def test_major_page_selection_ignores_note_page_marker() -> None:
    pages = [
        "单位：百万元人民币\n全年业绩营业收入 618,009\n资产总计 32,432,166\n"
        "基本每股收益 0.80",
        "中国银行股份有限公司\n2023年度财务报表附注（续）\n"
        "主要财务指标\n总资产 28,913,857",
    ]

    selected = _major_page_text(pages)

    assert "32,432,166" in selected


def test_major_page_selection_prefers_core_rows_over_repeated_heading() -> None:
    pages = [
        "主要会计数据\n单位：百万元\n营业收入 775,383\n"
        "归属于母公司股东的净利润 119,182\n基本每股收益 6.73\n总资产 10,000",
        "主要会计数据 主要会计数据 主要会计数据 主要会计数据\n"
        "归属于母公司股东的净利润 44,563\n基本每股收益 2.52",
    ]

    selected = _major_page_text(pages)

    assert "775,383" in selected
