"""CNINFO 报告公告日期匹配规则测试。"""

import pandas as pd

from scripts.enrich_official_pit_dates import _match_report


def test_match_report_prefers_issuer_report_over_subsidiary() -> None:
    row = pd.Series(
        {
            "name": "中国平安",
            "period_end": "2024-03-31",
            "report_type": "q1",
        }
    )
    announcements = pd.DataFrame(
        [
            {
                "published_at": "2024-04-19",
                "title": "平安银行股份有限公司2024年第一季度报告",
                "item_id": "bank",
                "announcement_id": "bank",
                "source_url": "https://example.test/bank.pdf",
            },
            {
                "published_at": "2024-04-23",
                "title": "中国平安2024年第一季度报告",
                "item_id": "parent",
                "announcement_id": "parent",
                "source_url": "https://example.test/parent.pdf",
            },
        ]
    )

    result = _match_report(row, announcements)

    assert result["announcement_title"] == "中国平安2024年第一季度报告"
    assert result["announcement_match"] == "issuer_preferred_candidate"
    assert result["announcement_candidate_count"] == 2


def test_match_report_excludes_half_year_attachments() -> None:
    row = pd.Series(
        {
            "name": "立讯精密",
            "period_end": "2024-06-30",
            "report_type": "semiannual",
        }
    )
    announcements = pd.DataFrame(
        [
            {
                "published_at": "2024-08-23",
                "title": "半年报财务报表",
                "item_id": "statement",
                "announcement_id": "statement",
                "source_url": "https://example.test/statement.pdf",
            },
            {
                "published_at": "2024-08-23",
                "title": "2024年半年度报告",
                "item_id": "report",
                "announcement_id": "report",
                "source_url": "https://example.test/report.pdf",
            },
        ]
    )

    result = _match_report(row, announcements)

    assert result["announcement_title"] == "2024年半年度报告"
    assert result["announcement_match"] == "exact"
    assert result["announcement_candidate_count"] == 1
