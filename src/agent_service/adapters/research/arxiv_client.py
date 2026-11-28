"""arXiv Atom API 客户端：支持 CS 分类聚焦、代码仓库链接提取与严格 3 秒限流。"""

from __future__ import annotations

import asyncio
import re
import xml.etree.ElementTree as ET
from typing import Literal

import httpx

from agent_service.adapters.research.models import (
    PaperRecord,
    canonical_paper_id,
    extract_code_urls,
    normalize_arxiv_id,
    normalize_doi,
    normalize_whitespace,
)
from agent_service.adapters.research.rate_limit import (
    AsyncRateLimiter,
    build_research_headers,
    get_default_limiter,
)

ARXIV_API_ENDPOINT = "https://export.arxiv.org/api/query"
DEFAULT_ARXIV_TIMEOUT = 20.0

_ATOM_NS = "{http://www.w3.org/2005/Atom}"
_ARXIV_NS = "{http://arxiv.org/schemas/atom}"

# CS 研究者常用子方向别名到 arXiv 分类列表的映射
CS_CATEGORY_ALIASES: dict[str, tuple[str, ...]] = {
    "cs": ("cs.*",),
    "all": (),
    "ai": ("cs.AI",),
    "ml": ("cs.LG", "stat.ML"),
    "nlp": ("cs.CL",),
    "cv": ("cs.CV",),
    "se": ("cs.SE",),
    "systems": ("cs.OS", "cs.DC", "cs.AR"),
    "os": ("cs.OS",),
    "distributed": ("cs.DC",),
    "dc": ("cs.DC",),
    "security": ("cs.CR",),
    "crypto": ("cs.CR",),
    "db": ("cs.DB",),
    "database": ("cs.DB",),
    "net": ("cs.NI",),
    "network": ("cs.NI",),
    "robotics": ("cs.RO",),
    "ro": ("cs.RO",),
    "pl": ("cs.PL",),
    "theory": ("cs.DS", "cs.CC", "cs.LO"),
    "hci": ("cs.HC",),
    "ir": ("cs.IR",),
    "graphics": ("cs.GR",),
    "arch": ("cs.AR",),
    "mm": ("cs.MM",),
    "multiagent": ("cs.MA",),
}

_TOKEN_RE = re.compile(r'"([^"]+)"|([A-Za-z0-9][A-Za-z0-9_+.-]*)')
_RAW_CATEGORY_RE = re.compile(r"^[a-z-]+(?:\.[A-Za-z-]+|\.\*)?$")

ArxivSortBy = Literal["relevance", "date"]


def resolve_arxiv_categories(category: str | None) -> tuple[str, ...]:
    """将用户友好的方向名或原生 arXiv 分类解析为标准分类元组。"""
    if category is None:
        return ("cs.*",)
    cleaned = category.strip()
    if not cleaned:
        return ("cs.*",)
    lowered = cleaned.lower()
    if lowered in CS_CATEGORY_ALIASES:
        return CS_CATEGORY_ALIASES[lowered]

    parts = [item.strip() for item in cleaned.split(",") if item.strip()]
    resolved: list[str] = []
    for part in parts:
        pl = part.lower()
        if pl in CS_CATEGORY_ALIASES:
            resolved.extend(CS_CATEGORY_ALIASES[pl])
        elif _RAW_CATEGORY_RE.match(part):
            resolved.append(part)
    return tuple(dict.fromkeys(resolved))


def build_arxiv_search_query(
    query: str,
    *,
    category: str | None = "cs",
) -> str:
    """将关键词查询构造成稳定的 arXiv search_query 表达式。"""
    parts: list[str] = []
    for match in _TOKEN_RE.finditer(query):
        quoted, bare = match.group(1), match.group(2)
        if quoted:
            phrase = normalize_whitespace(quoted)
            if phrase:
                parts.append(f'all:"{phrase}"')
        elif bare:
            parts.append(f"all:{bare}")

    if not parts:
        cleaned = normalize_whitespace(query)
        text_expr = f'all:"{cleaned}"' if cleaned else ""
    elif len(parts) == 1:
        text_expr = parts[0]
    else:
        text_expr = "(" + " AND ".join(parts[:12]) + ")"

    categories = resolve_arxiv_categories(category)
    if not categories:
        return text_expr
    cat_expr = (
        f"cat:{categories[0]}"
        if len(categories) == 1
        else "(" + " OR ".join(f"cat:{c}" for c in categories) + ")"
    )
    return f"{text_expr} AND {cat_expr}" if text_expr else cat_expr


def parse_arxiv_atom(xml_text: str) -> list[PaperRecord]:
    """解析 arXiv Atom XML 响应为统一 PaperRecord 列表。"""
    if not xml_text.strip():
        return []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise ValueError(f"arXiv XML 解析失败：{exc}") from exc

    records: list[PaperRecord] = []
    for entry in root.findall(f"{_ATOM_NS}entry"):
        raw_id = (entry.findtext(f"{_ATOM_NS}id") or "").strip()
        arxiv_id = normalize_arxiv_id(raw_id)
        title = normalize_whitespace(entry.findtext(f"{_ATOM_NS}title") or "")
        if not title:
            continue
        # arXiv 错误响应有时会以特殊 entry 返回
        if title.lower() == "error" and not arxiv_id:
            continue

        abstract = normalize_whitespace(entry.findtext(f"{_ATOM_NS}summary") or "")
        authors = tuple(
            normalize_whitespace(author.findtext(f"{_ATOM_NS}name") or "")
            for author in entry.findall(f"{_ATOM_NS}author")
            if (author.findtext(f"{_ATOM_NS}name") or "").strip()
        )
        published_raw = (entry.findtext(f"{_ATOM_NS}published") or "").strip()
        published_date = published_raw.split("T", 1)[0] if published_raw else ""
        year: int | None = None
        if len(published_date) >= 4 and published_date[:4].isdigit():
            year = int(published_date[:4])

        doi = normalize_doi(entry.findtext(f"{_ARXIV_NS}doi") or "")
        comment = normalize_whitespace(entry.findtext(f"{_ARXIV_NS}comment") or "")
        journal_ref = normalize_whitespace(
            entry.findtext(f"{_ARXIV_NS}journal_ref") or ""
        )

        categories: list[str] = []
        primary_cat = entry.find(f"{_ARXIV_NS}primary_category")
        if primary_cat is not None:
            term = (primary_cat.attrib.get("term") or "").strip()
            if term:
                categories.append(term)
        for cat in entry.findall(f"{_ATOM_NS}category"):
            term = (cat.attrib.get("term") or "").strip()
            if term and term not in categories:
                categories.append(term)

        pdf_url = ""
        abs_url = f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else raw_id
        for link in entry.findall(f"{_ATOM_NS}link"):
            href = (link.attrib.get("href") or "").strip()
            link_title = (link.attrib.get("title") or "").strip().lower()
            link_type = (link.attrib.get("type") or "").strip().lower()
            if link_title == "pdf" or link_type == "application/pdf":
                pdf_url = href
            elif link.attrib.get("rel") == "alternate" and href:
                abs_url = href

        if arxiv_id and not pdf_url:
            pdf_url = f"https://arxiv.org/pdf/{arxiv_id}.pdf"
        html_url = f"https://arxiv.org/html/{arxiv_id}" if arxiv_id else ""

        venue = journal_ref or "arXiv preprint"
        pub_type = "journal" if journal_ref else "preprint"
        code_urls = extract_code_urls(f"{abstract}\n{comment}")
        pid = canonical_paper_id(
            arxiv_id=arxiv_id,
            doi=doi,
            fallback_title=title,
        )
        records.append(
            PaperRecord(
                paper_id=pid,
                title=title,
                authors=authors,
                year=year,
                published_date=published_date,
                venue=venue,
                publication_type=pub_type,
                abstract=abstract,
                doi=doi,
                arxiv_id=arxiv_id,
                url=abs_url,
                pdf_url=pdf_url,
                html_url=html_url,
                categories=tuple(categories),
                code_urls=code_urls,
                sources=("arXiv",),
                comment=comment,
            )
        )
    return records


class ArxivClient:
    """面向 CS 的异步 arXiv 客户端（带 3 秒礼貌限流与 429 重试）。"""

    def __init__(
        self,
        *,
        timeout: float = DEFAULT_ARXIV_TIMEOUT,
        transport: httpx.AsyncBaseTransport | None = None,
        rate_limiter: AsyncRateLimiter | None = None,
    ) -> None:
        self._timeout = timeout
        self._transport = transport
        self._limiter = rate_limiter or get_default_limiter("arxiv")

    async def _get_xml(self, params: dict[str, str | int]) -> str:
        headers = build_research_headers(extra={"Accept": "application/atom+xml,text/xml"})
        last_exc: Exception | None = None
        for attempt in range(2):
            await self._limiter.acquire()
            async with httpx.AsyncClient(
                timeout=self._timeout,
                follow_redirects=True,
                transport=self._transport,
            ) as client:
                try:
                    response = await client.get(
                        ARXIV_API_ENDPOINT,
                        params=params,
                        headers=headers,
                    )
                    if response.status_code == 429 and attempt == 0:
                        retry_after = response.headers.get("Retry-After", "")
                        wait_s = float(retry_after) if retry_after.isdigit() else 3.5
                        await asyncio.sleep(min(wait_s, 6.0))
                        continue
                    response.raise_for_status()
                    return response.text
                except (httpx.HTTPError, OSError) as exc:
                    last_exc = exc
                    if attempt == 0 and isinstance(exc, httpx.TimeoutException):
                        continue
                    raise
        if last_exc is not None:
            raise last_exc
        return ""

    async def search(
        self,
        query: str,
        *,
        category: str | None = "cs",
        year_from: int | None = None,
        year_to: int | None = None,
        sort_by: ArxivSortBy = "relevance",
        max_results: int = 8,
    ) -> list[PaperRecord]:
        search_expr = build_arxiv_search_query(query, category=category)
        if not search_expr:
            return []
        # 当需要按年份后过滤时，多取一倍候选减少有效条数不足
        fetch_count = (
            min(max_results * 3, 30)
            if (year_from is not None or year_to is not None)
            else min(max(max_results, 1), 25)
        )
        arxiv_sort = "submittedDate" if sort_by == "date" else "relevance"
        xml_text = await self._get_xml(
            {
                "search_query": search_expr,
                "start": 0,
                "max_results": fetch_count,
                "sortBy": arxiv_sort,
                "sortOrder": "descending",
            }
        )
        items = parse_arxiv_atom(xml_text)
        filtered: list[PaperRecord] = []
        for item in items:
            if year_from is not None and (item.year is None or item.year < year_from):
                continue
            if year_to is not None and (item.year is None or item.year > year_to):
                continue
            filtered.append(item)
            if len(filtered) >= max_results:
                break
        return filtered

    async def fetch_by_id(self, arxiv_id: str) -> PaperRecord | None:
        clean_id = normalize_arxiv_id(arxiv_id)
        if not clean_id:
            return None
        xml_text = await self._get_xml({"id_list": clean_id, "max_results": 1})
        items = parse_arxiv_atom(xml_text)
        return items[0] if items else None

    async def fetch_by_title(self, title: str) -> PaperRecord | None:
        cleaned = normalize_whitespace(title)
        if not cleaned:
            return None
        xml_text = await self._get_xml(
            {
                "search_query": f'ti:"{cleaned}"',
                "start": 0,
                "max_results": 3,
                "sortBy": "relevance",
                "sortOrder": "descending",
            }
        )
        items = parse_arxiv_atom(xml_text)
        return items[0] if items else None
