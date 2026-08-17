"""巨潮资讯网公告查询客户端。

该模块只负责把巨潮公告查询结果转换为可审计的结构化记录，不负责投资结论。
公告事件的正负面标签使用固定关键词规则，无法判断的公告统一标记为
``unknown``，交由上层按中性证据处理。

巨潮的公告查询接口属于网页端公开查询接口，未承诺长期稳定的 SDK 契约，因而
URL、超时、分页和是否包含港股公告均可配置，并保留原始公告 PDF 链接。
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd  # type: ignore[import-untyped]

from src.data.providers.base import DataProviderError

CNINFO_SITE_BASE_URL = "https://www.cninfo.com.cn"
CNINFO_TOP_SEARCH_PATH = "/new/information/topSearch/detailOfQuery"
CNINFO_ANNOUNCEMENT_PATH = "/new/hisAnnouncement/query"
CNINFO_STATIC_BASE_URL = "https://static.cninfo.com.cn"
CNINFO_SOURCE = "cninfo"

_CODE_PATTERN = re.compile(r"^(?P<code>\d{6})(?:\.(?P<exchange>SH|SZ|BJ))?$", re.IGNORECASE)

# 规则保持短小、可复核；不要在这里引入 LLM 或不可重复的自由文本分类。
_EVENT_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("退市风险", ("退市风险", "终止上市风险")),
    ("行政处罚", ("行政处罚", "纪律处分", "公开谴责")),
    ("监管问询", ("监管问询", "问询函", "关注函")),
    ("重大诉讼", ("重大诉讼", "重大仲裁")),
    ("股东减持", ("股东减持", "减持计划", "减持股份")),
    ("停产", ("停产", "临时停产", "生产线停产")),
    ("风险提示", ("风险提示", "风险公告", "特别风险")),
    ("业绩预亏", ("业绩预亏", "预计亏损", "预告亏损")),
    ("业绩预减", ("业绩预减", "预计下降", "预告下降")),
    ("业绩快报下降", ("业绩快报", "下降")),
    ("业绩预增", ("业绩预增", "预计增长", "预告增长")),
    ("业绩快报增长", ("业绩快报", "增长")),
    ("重大合同", ("重大合同", "战略合作协议")),
    ("重大订单", ("重大订单", "订单金额")),
    ("中标", ("中标", "项目中标")),
    ("股份回购", ("股份回购", "回购公司股份", "回购进展")),
    ("股东增持", ("股东增持", "增持计划", "增持股份")),
    ("分红", ("利润分配", "现金分红", "分红派息")),
)


def classify_official_event_title(title: str) -> str:
    """按固定关键词把公告标题映射为官方事件类型。"""
    text = str(title or "").strip()
    if not text:
        return "unknown"
    for event_type, keywords in _EVENT_KEYWORDS:
        if event_type in {"业绩快报下降", "业绩快报增长"}:
            matched = all(keyword in text for keyword in keywords)
        else:
            matched = any(keyword in text for keyword in keywords)
        if matched:
            return event_type
    return "unknown"


def _normalise_code(code: str) -> tuple[str, str]:
    """Return bare six-digit code and exchange suffix."""
    match = _CODE_PATTERN.fullmatch(str(code).strip().upper())
    if match is None:
        raise ValueError("股票代码应为六位数字，可带 .SH/.SZ/.BJ 后缀")
    bare = match.group("code")
    exchange = match.group("exchange")
    if exchange is None:
        exchange = "SH" if bare.startswith(("5", "6", "9")) else "SZ"
    return bare, exchange


def _market_query(code: str, exchange: str) -> tuple[str, str]:
    """Map the local code to CNINFO's column/plate query fields."""
    if exchange == "SH":
        return "sse", "shkcp" if code.startswith("68") else "sh"
    if exchange == "BJ":
        return "bjse", "bj"
    return "szse", "sz"


def _normalise_date(value: str | datetime | pd.Timestamp) -> str:
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        raise ValueError("日期必须是 YYYY-MM-DD 或 YYYYMMDD")
    return parsed.strftime("%Y-%m-%d")


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class CninfoSecurity:
    """CNINFO 查询所需的证券标识。"""

    code: str
    exchange: str
    name: str
    org_id: str
    column: str
    plate: str


class CninfoAnnouncementClient:
    """查询巨潮公告并返回可直接送入官方事件归一化层的记录。"""

    def __init__(
        self,
        *,
        base_url: str = CNINFO_SITE_BASE_URL,
        top_search_url: str | None = None,
        announcement_url: str | None = None,
        static_base_url: str = CNINFO_STATIC_BASE_URL,
        timeout: int = 30,
        page_size: int = 30,
        max_pages: int = 20,
        include_hk: bool = False,
        user_agent: str = "StockAnalysis/1.0 (+https://www.cninfo.com.cn)",
    ) -> None:
        if timeout <= 0:
            raise ValueError("CNINFO timeout 必须大于 0")
        if not 1 <= page_size <= 100:
            raise ValueError("CNINFO page_size 必须在 1 到 100 之间")
        if max_pages <= 0:
            raise ValueError("CNINFO max_pages 必须大于 0")
        self._base_url = base_url.rstrip("/")
        self._top_search_url = top_search_url or f"{self._base_url}{CNINFO_TOP_SEARCH_PATH}"
        self._announcement_url = announcement_url or (
            f"{self._base_url}{CNINFO_ANNOUNCEMENT_PATH}"
        )
        self._static_base_url = static_base_url.rstrip("/")
        self._timeout = timeout
        self._page_size = page_size
        self._max_pages = max_pages
        self._include_hk = include_hk
        self._user_agent = user_agent

    def _post_form(self, url: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        request = Request(
            url,
            data=urlencode({key: str(value) for key, value in payload.items()}).encode(
                "utf-8"
            ),
            method="POST",
            headers={
                "Accept": "application/json, text/plain, */*",
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "Referer": f"{self._base_url}/",
                "User-Agent": self._user_agent,
            },
        )
        try:
            with urlopen(request, timeout=self._timeout) as response:
                body = response.read()
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            raise DataProviderError(
                f"CNINFO 请求失败: {exc}", provider=CNINFO_SOURCE, original=exc
            ) from exc
        try:
            decoded = json.loads(body.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DataProviderError(
                "CNINFO 返回内容不是有效 JSON", provider=CNINFO_SOURCE, original=exc
            ) from exc
        if not isinstance(decoded, dict):
            raise DataProviderError("CNINFO 返回结构不是对象", provider=CNINFO_SOURCE)
        return decoded

    def resolve_security(self, code: str) -> CninfoSecurity:
        """通过代码搜索 CNINFO 的 orgId。"""
        bare, exchange = _normalise_code(code)
        column, plate = _market_query(bare, exchange)
        payload = self._post_form(
            self._top_search_url,
            {"keyWord": bare, "maxSecNum": 10, "maxListNum": 5},
        )
        candidates = payload.get("keyBoardList") or []
        if not isinstance(candidates, list):
            candidates = []
        selected: Mapping[str, Any] | None = None
        for candidate in candidates:
            if isinstance(candidate, Mapping) and str(candidate.get("code", "")) == bare:
                selected = candidate
                break
        if selected is None or not str(selected.get("orgId", "")).strip():
            raise DataProviderError(
                f"CNINFO 未找到股票 {bare}", provider=CNINFO_SOURCE
            )
        return CninfoSecurity(
            code=bare,
            exchange=exchange,
            name=str(selected.get("zwjc") or selected.get("name") or "").strip(),
            org_id=str(selected["orgId"]).strip(),
            column=column,
            plate=plate,
        )

    def fetch_announcements(
        self,
        code: str,
        start_date: str | datetime | pd.Timestamp,
        end_date: str | datetime | pd.Timestamp,
        *,
        max_pages: int | None = None,
    ) -> pd.DataFrame:
        """分页获取 A 股公告元数据和 PDF 链接。"""
        start = _normalise_date(start_date)
        end = _normalise_date(end_date)
        if start > end:
            raise ValueError("CNINFO start_date 不能晚于 end_date")
        security = self.resolve_security(code)
        page_limit = max_pages if max_pages is not None else self._max_pages
        if page_limit <= 0:
            raise ValueError("CNINFO max_pages 必须大于 0")

        rows: list[dict[str, Any]] = []
        total_pages = page_limit
        for page_num in range(1, page_limit + 1):
            payload = self._post_form(
                self._announcement_url,
                {
                    "pageNum": page_num,
                    "pageSize": self._page_size,
                    "tabName": "fulltext",
                    "column": security.column,
                    "plate": security.plate,
                    "stock": f"{security.code},{security.org_id}",
                    "searchkey": "",
                    "secid": "",
                    "category": "",
                    "trade": "",
                    "seDate": f"{start}~{end}",
                    "sortName": "announcementTime",
                    "sortType": "desc",
                    "isHLtitle": "true",
                },
            )
            announcements = payload.get("announcements") or []
            if not isinstance(announcements, list) or not announcements:
                break
            for raw in announcements:
                if not isinstance(raw, Mapping):
                    continue
                record = self._normalise_announcement(raw, security)
                if record is not None:
                    rows.append(record)
            total_pages = max(1, _as_int(payload.get("totalpages"), page_num))
            if page_num >= total_pages or not bool(payload.get("hasMore", page_num < total_pages)):
                break

        if not rows:
            return self._empty_frame()
        frame = pd.DataFrame(rows)
        frame = frame.drop_duplicates(subset=["item_id"], keep="first")
        frame = frame.sort_values(["published_at", "item_id"]).reset_index(drop=True)
        return frame[self._frame_columns()]

    def fetch_event_records(
        self,
        code: str,
        start_date: str | datetime | pd.Timestamp,
        end_date: str | datetime | pd.Timestamp,
        *,
        max_pages: int | None = None,
    ) -> list[dict[str, Any]]:
        """获取公告并转换为 normalize_official_event_evidence 所需的记录。"""
        frame = self.fetch_announcements(
            code,
            start_date,
            end_date,
            max_pages=max_pages,
        )
        records: list[dict[str, Any]] = []
        for row in frame.to_dict(orient="records"):
            published_at = pd.Timestamp(row["published_at"]).strftime("%Y-%m-%d")
            records.append(
                {
                    "item_id": str(row["item_id"]),
                    "source": CNINFO_SOURCE,
                    "published_at": published_at,
                    "event_type": str(row["event_type"]),
                    "title": str(row["title"]),
                    "source_url": str(row["source_url"]),
                    "announcement_id": str(row["announcement_id"]),
                    "security_code": str(row["security_code"]),
                }
            )
        return records

    def _normalise_announcement(
        self,
        raw: Mapping[str, Any],
        security: CninfoSecurity,
    ) -> dict[str, Any] | None:
        raw_code = str(raw.get("secCode", "")).strip()
        if raw_code and raw_code.zfill(6) != security.code:
            return None
        title = str(raw.get("announcementTitle") or raw.get("title") or "").strip()
        if not title:
            return None
        if not self._include_hk and (
            title.startswith(("H股公告", "港股公告", "[H股公告]"))
            or "H股公告-" in title
            or "H股公告——" in title
        ):
            return None
        announcement_id = str(raw.get("announcementId") or "").strip()
        published = pd.to_datetime(raw.get("announcementTime"), unit="ms", errors="coerce")
        if pd.isna(published):
            published = pd.to_datetime(raw.get("announcementTime"), errors="coerce")
        if pd.isna(published):
            return None
        published_date = pd.Timestamp(published).normalize()
        adjunct_url = str(raw.get("adjunctUrl") or "").strip()
        source_url = self._source_url(adjunct_url, security, announcement_id)
        if not announcement_id:
            digest = hashlib.sha1(
                f"{security.code}|{published_date.date()}|{title}|{source_url}".encode()
            ).hexdigest()[:16]
            announcement_id = digest
        return {
            "item_id": f"{CNINFO_SOURCE}:{security.code}:{announcement_id}",
            "security_code": security.code,
            "security_name": security.name,
            "announcement_id": announcement_id,
            "title": title,
            "event_type": classify_official_event_title(title),
            "published_at": published_date,
            "source": CNINFO_SOURCE,
            "source_url": source_url,
            "announcement_type": str(
                raw.get("announcementType") or raw.get("category") or ""
            ),
            "adjunct_size_kb": _as_int(raw.get("adjunctSize"), 0),
        }

    def _source_url(
        self,
        adjunct_url: str,
        security: CninfoSecurity,
        announcement_id: str,
    ) -> str:
        if adjunct_url.startswith(("http://", "https://")):
            return adjunct_url
        if adjunct_url:
            return f"{self._static_base_url}/{adjunct_url.lstrip('/')}"
        return (
            f"{self._base_url}/new/disclosure/detail?plate={security.plate}"
            f"&orgId={security.org_id}&stockCode={security.code}"
            f"&announcementId={announcement_id}"
        )

    @staticmethod
    def _frame_columns() -> list[str]:
        return [
            "item_id",
            "security_code",
            "security_name",
            "announcement_id",
            "title",
            "event_type",
            "published_at",
            "source",
            "source_url",
            "announcement_type",
            "adjunct_size_kb",
        ]

    @classmethod
    def _empty_frame(cls) -> pd.DataFrame:
        return pd.DataFrame(columns=cls._frame_columns())
