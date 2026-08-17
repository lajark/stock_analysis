"""巨潮公告客户端的本地归一化测试。"""

from datetime import datetime
from types import SimpleNamespace

from src.app.service import _fetch_cninfo_events
from src.data.cninfo import (
    CninfoAnnouncementClient,
    CninfoSecurity,
    _market_query,
    _normalise_code,
    classify_official_event_title,
)


def test_cninfo_code_and_market_mapping() -> None:
    assert _normalise_code("600519.SH") == ("600519", "SH")
    assert _normalise_code("000858") == ("000858", "SZ")
    assert _market_query("688981", "SH") == ("sse", "shkcp")
    assert _market_query("002594", "SZ") == ("szse", "sz")


def test_official_title_classifier_is_deterministic() -> None:
    assert classify_official_event_title("关于股份回购进展的公告") == "股份回购"
    assert classify_official_event_title("股票交易风险提示公告") == "风险提示"
    assert classify_official_event_title("年度业绩快报") == "unknown"
    assert classify_official_event_title("年度业绩快报：净利润增长") == "业绩快报增长"
    assert classify_official_event_title("股东大会通知") == "unknown"


def test_cninfo_normalizes_pdf_metadata_and_filters_hk_titles() -> None:
    client = CninfoAnnouncementClient()
    security = CninfoSecurity(
        code="002594",
        exchange="SZ",
        name="比亚迪",
        org_id="gshk0001211",
        column="szse",
        plate="sz",
    )
    row = client._normalise_announcement(
        {
            "secCode": "002594",
            "announcementId": "1224855906",
            "announcementTitle": "关于重大合同的公告",
            "announcementTime": 1764950400000,
            "adjunctUrl": "finalpage/2025-12-06/1224855906.PDF",
            "adjunctSize": 55,
        },
        security,
    )

    assert row is not None
    assert row["event_type"] == "重大合同"
    assert row["source_url"] == (
        "https://static.cninfo.com.cn/finalpage/2025-12-06/1224855906.PDF"
    )
    assert client._normalise_announcement(
        {
            "secCode": "002594",
            "announcementId": "hk-1",
            "announcementTitle": "H股公告：董事会会议通知",
            "announcementTime": 1764950400000,
        },
        security,
    ) is None
    assert client._normalise_announcement(
        {
            "secCode": "002594",
            "announcementId": "hk-2",
            "announcementTitle": "比亚迪H股公告-2025年第三季度报告",
            "announcementTime": 1764950400000,
        },
        security,
    ) is None


def test_service_passes_analysis_window_to_cninfo(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeClient:
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)

        def fetch_event_records(self, code, start, end):
            captured["request"] = (code, start, end)
            return [{"item_id": "cninfo:1"}]

    monkeypatch.setattr("src.data.cninfo.CninfoAnnouncementClient", FakeClient)
    config = SimpleNamespace(
        cninfo=SimpleNamespace(
            enabled=True,
            base_url="https://www.cninfo.com.cn",
            static_base_url="https://static.cninfo.com.cn",
            timeout=5,
            page_size=10,
            max_pages=2,
            lookback_days=30,
            include_hk=False,
        )
    )

    records, warning = _fetch_cninfo_events("002594.SZ", "2025-02-11", config)

    assert warning is None
    assert records == [{"item_id": "cninfo:1"}]
    assert captured["request"] == (
        "002594.SZ",
        datetime(2025, 1, 12),
        datetime(2025, 2, 11),
    )
    assert captured["page_size"] == 10
