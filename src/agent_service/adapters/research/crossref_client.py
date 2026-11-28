"""Crossref 元数据核验、OpenCitations 开放引文图谱与可选 Semantic Scholar 增强客户端。"""

from __future__ import annotations

import asyncio
import os
import re
from typing import Any
from urllib.parse import quote

import httpx

from agent_service.adapters.research.models import (
    CitationItem,
    PaperRecord,
    canonical_paper_id,
    extract_code_urls,
    normalize_arxiv_id,
    normalize_doi,
    normalize_title,
    normalize_whitespace,
)
from agent_service.adapters.research.rate_limit import (
    AsyncRateLimiter,
    build_research_headers,
    get_default_limiter,
    resolve_contact_email,
)

CROSSREF_WORKS_ENDPOINT = "https://api.crossref.org/works"
OPENCITATIONS_INDEX_ENDPOINT = "https://api.opencitations.net/index/v2"
SEMANTIC_SCHOLAR_ENDPOINT = "https://api.semanticscholar.org/graph/v1"

DEFAULT_CROSSREF_TIMEOUT = 15.0
DEFAULT_OPENCITATIONS_TIMEOUT = 15.0
DEFAULT_S2_TIMEOUT = 15.0

_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _strip_jats(raw: str) -> str:
    if not raw:
        return ""
    return normalize_whitespace(_HTML_TAG_RE.sub(" ", raw))


def _extract_crossref_year(item: dict[str, Any]) -> tuple[int | None, str]:
    for key in ("published-print", "published-online", "issued", "created"):
        part = item.get(key)
        if not isinstance(part, dict):
            continue
        date_parts = part.get("date-parts")
        if (
            isinstance(date_parts, list)
            and date_parts
            and isinstance(date_parts[0], list)
            and date_parts[0]
        ):
            nums = [n for n in date_parts[0] if isinstance(n, int)]
            if nums:
                year = nums[0]
                if len(nums) >= 3:
                    return year, f"{nums[0]:04d}-{nums[1]:02d}-{nums[2]:02d}"
                if len(nums) == 2:
                    return year, f"{nums[0]:04d}-{nums[1]:02d}"
                return year, f"{nums[0]:04d}"
    return None, ""


def parse_crossref_work(item: dict[str, Any]) -> tuple[PaperRecord | None, list[CitationItem]]:
    """解析单条 Crossref work JSON，返回 (PaperRecord, references)。"""
    raw_titles = item.get("title")
    title = ""
    if isinstance(raw_titles, list) and raw_titles:
        title = normalize_whitespace(str(raw_titles[0]))
    elif isinstance(raw_titles, str):
        title = normalize_whitespace(raw_titles)
    if not title:
        return None, []

    doi = normalize_doi(str(item.get("DOI") or ""))
    authors_list: list[str] = []
    raw_authors = item.get("author")
    if isinstance(raw_authors, list):
        for author in raw_authors:
            if not isinstance(author, dict):
                continue
            given = str(author.get("given") or "").strip()
            family = str(author.get("family") or "").strip()
            full = normalize_whitespace(f"{given} {family}")
            if not full:
                full = normalize_whitespace(str(author.get("name") or ""))
            if full:
                authors_list.append(full)

    year, published_date = _extract_crossref_year(item)
    containers = item.get("container-title")
    venue = ""
    if isinstance(containers, list) and containers:
        venue = normalize_whitespace(str(containers[0]))
    elif isinstance(containers, str):
        venue = normalize_whitespace(containers)

    cr_type = str(item.get("type") or "").lower()
    if "proceedings" in cr_type:
        pub_type = "conference"
    elif "journal" in cr_type:
        pub_type = "journal"
    elif "posted-content" in cr_type:
        pub_type = "preprint"
    else:
        pub_type = ""

    abstract = _strip_jats(str(item.get("abstract") or ""))
    raw_cited = item.get("is-referenced-by-count")
    citation_count = int(raw_cited) if isinstance(raw_cited, int) and raw_cited >= 0 else None

    url = str(item.get("URL") or "").strip() or (f"https://doi.org/{doi}" if doi else "")
    pdf_url = ""
    raw_links = item.get("link")
    if isinstance(raw_links, list):
        for link in raw_links:
            if not isinstance(link, dict):
                continue
            ctype = str(link.get("content-type") or "").lower()
            link_url = str(link.get("URL") or "").strip()
            if "pdf" in ctype and link_url:
                pdf_url = link_url
                break

    arxiv_id = ""
    if doi.startswith("10.48550/arxiv."):
        arxiv_id = normalize_arxiv_id(doi.split("10.48550/arxiv.", 1)[1])

    pid = canonical_paper_id(arxiv_id=arxiv_id, doi=doi, fallback_title=title)
    record = PaperRecord(
        paper_id=pid,
        title=title,
        authors=tuple(authors_list),
        year=year,
        published_date=published_date,
        venue=venue,
        publication_type=pub_type,
        abstract=abstract,
        doi=doi,
        arxiv_id=arxiv_id,
        url=url,
        pdf_url=pdf_url,
        html_url=f"https://arxiv.org/html/{arxiv_id}" if arxiv_id else "",
        citation_count=citation_count,
        code_urls=extract_code_urls(abstract),
        sources=("Crossref",),
    )

    refs: list[CitationItem] = []
    raw_refs = item.get("reference")
    if isinstance(raw_refs, list):
        for ref in raw_refs:
            if not isinstance(ref, dict):
                continue
            ref_doi = normalize_doi(str(ref.get("DOI") or ""))
            ref_title = normalize_whitespace(
                str(
                    ref.get("article-title")
                    or ref.get("volume-title")
                    or ref.get("unstructured")
                    or ""
                )
            )
            if not ref_title and not ref_doi:
                continue
            ref_author = normalize_whitespace(str(ref.get("author") or ""))
            ref_year_raw = str(ref.get("year") or "").strip()
            ref_year = int(ref_year_raw[:4]) if len(ref_year_raw) >= 4 and ref_year_raw[:4].isdigit() else None
            ref_venue = normalize_whitespace(str(ref.get("journal-title") or ""))
            refs.append(
                CitationItem(
                    paper_id=canonical_paper_id(doi=ref_doi, fallback_title=ref_title or ref_doi),
                    title=ref_title or f"DOI: {ref_doi}",
                    authors=(ref_author,) if ref_author else (),
                    year=ref_year,
                    venue=ref_venue,
                    doi=ref_doi,
                )
            )
    return record, refs


class CrossrefClient:
    """免 Key 的 Crossref DOI 核验与元数据/参考文献客户端。"""

    def __init__(
        self,
        *,
        contact_email: str | None = None,
        timeout: float = DEFAULT_CROSSREF_TIMEOUT,
        transport: httpx.AsyncBaseTransport | None = None,
        rate_limiter: AsyncRateLimiter | None = None,
    ) -> None:
        self._contact_email = contact_email
        self._timeout = timeout
        self._transport = transport
        self._limiter = rate_limiter or get_default_limiter("crossref")

    async def _get_json(self, url: str, params: dict[str, str | int] | None = None) -> dict[str, Any]:
        query_params = dict(params or {})
        email = resolve_contact_email(self._contact_email)
        if email:
            query_params["mailto"] = email
        headers = build_research_headers(
            contact_email=self._contact_email,
            extra={"Accept": "application/json"},
        )
        await self._limiter.acquire()
        async with httpx.AsyncClient(
            timeout=self._timeout,
            follow_redirects=True,
            transport=self._transport,
        ) as client:
            response = await client.get(url, params=query_params, headers=headers)
            response.raise_for_status()
            data = response.json()
            return data if isinstance(data, dict) else {}

    async def fetch_by_doi(self, doi: str) -> tuple[PaperRecord | None, list[CitationItem]]:
        clean_doi = normalize_doi(doi)
        if not clean_doi:
            return None, []
        encoded = quote(clean_doi, safe="")
        try:
            payload = await self._get_json(f"{CROSSREF_WORKS_ENDPOINT}/{encoded}")
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return None, []
            raise
        message = payload.get("message")
        if not isinstance(message, dict):
            return None, []
        return parse_crossref_work(message)

    async def search_by_title(self, title: str, *, limit: int = 3) -> list[PaperRecord]:
        cleaned = normalize_whitespace(title)
        if not cleaned:
            return []
        payload = await self._get_json(
            CROSSREF_WORKS_ENDPOINT,
            params={"query.title": cleaned, "rows": max(1, min(limit, 10))},
        )
        message = payload.get("message")
        if not isinstance(message, dict):
            return []
        items = message.get("items")
        if not isinstance(items, list):
            return []
        records: list[PaperRecord] = []
        for entry in items:
            if isinstance(entry, dict):
                rec, _ = parse_crossref_work(entry)
                if rec is not None:
                    records.append(rec)
        return records

    async def lookup_exact_title(self, title: str) -> PaperRecord | None:
        candidates = await self.search_by_title(title, limit=5)
        target = normalize_title(title)
        for rec in candidates:
            if normalize_title(rec.title) == target:
                return rec
        return None


def parse_opencitations_dois(payload: Any, *, field_name: str, limit: int) -> list[str]:
    """从 OpenCitations Index v2 响应中提取去重后的 DOI 列表。"""
    if not isinstance(payload, list):
        return []
    dois: list[str] = []
    seen: set[str] = set()
    for row in payload:
        if not isinstance(row, dict):
            continue
        raw_val = str(row.get(field_name) or "")
        # OpenCitations 返回形如 "omid:br/06101801781 doi:10.1145/..."
        for token in raw_val.split():
            if token.lower().startswith("doi:"):
                doi = normalize_doi(token)
                if doi and doi not in seen:
                    seen.add(doi)
                    dois.append(doi)
                    break
        if len(dois) >= limit:
            break
    return dois


class OpenCitationsClient:
    """免 Key 的 OpenCitations Index v2 开放引文图谱客户端（CC0，180 RPM）。"""

    def __init__(
        self,
        *,
        access_token: str | None = None,
        timeout: float = DEFAULT_OPENCITATIONS_TIMEOUT,
        transport: httpx.AsyncBaseTransport | None = None,
        rate_limiter: AsyncRateLimiter | None = None,
    ) -> None:
        self._access_token = access_token
        self._timeout = timeout
        self._transport = transport
        self._limiter = rate_limiter or get_default_limiter("opencitations")

    async def _fetch_edge_dois(self, kind: str, doi: str, *, limit: int) -> list[str]:
        clean_doi = normalize_doi(doi)
        if not clean_doi:
            return []
        token = (self._access_token or os.getenv("OPENCITATIONS_ACCESS_TOKEN", "")).strip()
        extra_headers = {"Accept": "application/json"}
        if token:
            extra_headers["authorization"] = token
        headers = build_research_headers(extra=extra_headers)
        encoded = quote(clean_doi, safe="")
        url = f"{OPENCITATIONS_INDEX_ENDPOINT}/{kind}/doi:{encoded}"
        await self._limiter.acquire()
        async with httpx.AsyncClient(
            timeout=self._timeout,
            follow_redirects=True,
            transport=self._transport,
        ) as client:
            response = await client.get(url, headers=headers)
            if response.status_code == 404:
                return []
            response.raise_for_status()
            data = response.json()
        field_name = "citing" if kind == "citations" else "cited"
        return parse_opencitations_dois(data, field_name=field_name, limit=limit)

    async def fetch_citing_dois(self, doi: str, *, limit: int = 10) -> list[str]:
        return await self._fetch_edge_dois("citations", doi, limit=limit)

    async def fetch_referenced_dois(self, doi: str, *, limit: int = 10) -> list[str]:
        return await self._fetch_edge_dois("references", doi, limit=limit)


def _parse_s2_paper(raw: dict[str, Any]) -> PaperRecord | None:
    title = normalize_whitespace(str(raw.get("title") or ""))
    if not title:
        return None
    s2_id = str(raw.get("paperId") or "").strip()
    raw_ext = raw.get("externalIds")
    ext_ids: dict[str, Any] = raw_ext if isinstance(raw_ext, dict) else {}
    doi = normalize_doi(str(ext_ids.get("DOI") or ""))
    arxiv_id = normalize_arxiv_id(str(ext_ids.get("ArXiv") or ""))
    dblp_key = str(ext_ids.get("DBLP") or "").strip()

    raw_authors = raw.get("authors")
    authors = tuple(
        normalize_whitespace(str(a.get("name") or ""))
        for a in (raw_authors if isinstance(raw_authors, list) else [])
        if isinstance(a, dict) and str(a.get("name") or "").strip()
    )
    year_val = raw.get("year")
    year = int(year_val) if isinstance(year_val, int) else None
    venue = normalize_whitespace(str(raw.get("venue") or ""))
    abstract = normalize_whitespace(str(raw.get("abstract") or ""))
    tldr_obj = raw.get("tldr")
    tldr = (
        normalize_whitespace(str(tldr_obj.get("text") or ""))
        if isinstance(tldr_obj, dict)
        else ""
    )
    oa_pdf = raw.get("openAccessPdf")
    pdf_url = (
        str(oa_pdf.get("url") or "").strip()
        if isinstance(oa_pdf, dict)
        else ""
    )
    if not pdf_url and arxiv_id:
        pdf_url = f"https://arxiv.org/pdf/{arxiv_id}.pdf"

    cited = raw.get("citationCount")
    citation_count = int(cited) if isinstance(cited, int) and cited >= 0 else None
    inf_cited = raw.get("influentialCitationCount")
    influential_count = (
        int(inf_cited) if isinstance(inf_cited, int) and inf_cited >= 0 else None
    )
    url = str(raw.get("url") or "").strip() or (
        f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else (f"https://doi.org/{doi}" if doi else "")
    )

    pid = canonical_paper_id(
        arxiv_id=arxiv_id,
        doi=doi,
        dblp_key=dblp_key,
        s2_id=s2_id,
        fallback_title=title,
    )
    return PaperRecord(
        paper_id=pid,
        title=title,
        authors=authors,
        year=year,
        published_date=str(raw.get("publicationDate") or (str(year) if year else "")),
        venue=venue,
        abstract=abstract,
        tldr=tldr,
        doi=doi,
        arxiv_id=arxiv_id,
        dblp_key=dblp_key,
        s2_id=s2_id,
        url=url,
        pdf_url=pdf_url,
        html_url=f"https://arxiv.org/html/{arxiv_id}" if arxiv_id else "",
        citation_count=citation_count,
        influential_citation_count=influential_count,
        code_urls=extract_code_urls(abstract),
        sources=("Semantic Scholar",),
    )


def _parse_s2_citation_item(raw: dict[str, Any]) -> CitationItem | None:
    title = normalize_whitespace(str(raw.get("title") or ""))
    if not title:
        return None
    raw_ext = raw.get("externalIds")
    ext_ids: dict[str, Any] = raw_ext if isinstance(raw_ext, dict) else {}
    doi = normalize_doi(str(ext_ids.get("DOI") or ""))
    arxiv_id = normalize_arxiv_id(str(ext_ids.get("ArXiv") or ""))
    s2_id = str(raw.get("paperId") or "").strip()
    raw_authors = raw.get("authors")
    authors = tuple(
        normalize_whitespace(str(a.get("name") or ""))
        for a in (raw_authors if isinstance(raw_authors, list) else [])
        if isinstance(a, dict) and str(a.get("name") or "").strip()
    )
    year_val = raw.get("year")
    year = int(year_val) if isinstance(year_val, int) else None
    cited = raw.get("citationCount")
    citation_count = int(cited) if isinstance(cited, int) and cited >= 0 else None
    return CitationItem(
        paper_id=canonical_paper_id(
            arxiv_id=arxiv_id, doi=doi, s2_id=s2_id, fallback_title=title
        ),
        title=title,
        authors=authors,
        year=year,
        venue=normalize_whitespace(str(raw.get("venue") or "")),
        doi=doi,
        arxiv_id=arxiv_id,
        citation_count=citation_count,
    )


class SemanticScholarClient:
    """可选增强客户端：仅在显式传入或环境变量配置 SEMANTIC_SCHOLAR_API_KEY 时激活。"""

    _SEARCH_FIELDS = (
        "paperId,title,authors,year,venue,abstract,tldr,citationCount,"
        "influentialCitationCount,externalIds,openAccessPdf,url,publicationDate"
    )
    _EDGE_FIELDS = "paperId,title,authors,year,venue,citationCount,externalIds"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        timeout: float = DEFAULT_S2_TIMEOUT,
        transport: httpx.AsyncBaseTransport | None = None,
        rate_limiter: AsyncRateLimiter | None = None,
    ) -> None:
        self._explicit_key = api_key
        self._timeout = timeout
        self._transport = transport
        self._limiter = rate_limiter or get_default_limiter("semantic_scholar")

    def _resolve_key(self) -> str:
        if self._explicit_key is not None:
            return self._explicit_key.strip()
        return os.getenv("SEMANTIC_SCHOLAR_API_KEY", "").strip()

    def is_configured(self) -> bool:
        return bool(self._resolve_key())

    async def _get_json(self, path: str, params: dict[str, str | int]) -> dict[str, Any]:
        api_key = self._resolve_key()
        if not api_key:
            return {}
        headers = build_research_headers(
            extra={"Accept": "application/json", "x-api-key": api_key}
        )
        for attempt in range(2):
            await self._limiter.acquire()
            async with httpx.AsyncClient(
                timeout=self._timeout,
                follow_redirects=True,
                transport=self._transport,
            ) as client:
                response = await client.get(
                    f"{SEMANTIC_SCHOLAR_ENDPOINT}{path}",
                    params=params,
                    headers=headers,
                )
                if response.status_code == 429 and attempt == 0:
                    await asyncio.sleep(1.5)
                    continue
                if response.status_code == 404:
                    return {}
                response.raise_for_status()
                data = response.json()
                return data if isinstance(data, dict) else {}
        return {}

    async def search(
        self,
        query: str,
        *,
        year_from: int | None = None,
        year_to: int | None = None,
        max_results: int = 8,
    ) -> list[PaperRecord]:
        if not self.is_configured():
            return []
        params: dict[str, str | int] = {
            "query": normalize_whitespace(query),
            "limit": max(1, min(max_results, 20)),
            "fields": self._SEARCH_FIELDS,
            "fieldsOfStudy": "Computer Science",
        }
        if year_from is not None and year_to is not None:
            params["year"] = f"{year_from}-{year_to}"
        elif year_from is not None:
            params["year"] = f"{year_from}-"
        elif year_to is not None:
            params["year"] = f"-{year_to}"

        payload = await self._get_json("/paper/search", params)
        rows = payload.get("data")
        if not isinstance(rows, list):
            return []
        records: list[PaperRecord] = []
        for row in rows:
            if isinstance(row, dict):
                rec = _parse_s2_paper(row)
                if rec is not None:
                    records.append(rec)
        return records

    async def fetch_paper_and_graph(
        self,
        identifier: str,
        *,
        include_citations: bool = False,
        include_references: bool = False,
        limit: int = 10,
    ) -> tuple[PaperRecord | None, list[CitationItem], list[CitationItem]]:
        """按 ARXIV:{id} / DOI:{doi} / paperId 查询 S2 详情与可选引文图谱。"""
        if not self.is_configured():
            return None, [], []
        encoded = quote(identifier, safe=":")
        payload = await self._get_json(
            f"/paper/{encoded}",
            params={"fields": self._SEARCH_FIELDS},
        )
        if not payload:
            return None, [], []
        record = _parse_s2_paper(payload)
        if record is None:
            return None, [], []

        citations: list[CitationItem] = []
        references: list[CitationItem] = []
        paper_ref = record.s2_id or identifier

        if include_citations:
            c_data = await self._get_json(
                f"/paper/{quote(paper_ref, safe=':')}/citations",
                params={"fields": self._EDGE_FIELDS, "limit": max(1, min(limit, 30))},
            )
            for row in c_data.get("data") or []:
                if isinstance(row, dict) and isinstance(row.get("citingPaper"), dict):
                    item = _parse_s2_citation_item(row["citingPaper"])
                    if item is not None:
                        citations.append(item)

        if include_references:
            r_data = await self._get_json(
                f"/paper/{quote(paper_ref, safe=':')}/references",
                params={"fields": self._EDGE_FIELDS, "limit": max(1, min(limit, 30))},
            )
            for row in r_data.get("data") or []:
                if isinstance(row, dict) and isinstance(row.get("citedPaper"), dict):
                    item = _parse_s2_citation_item(row["citedPaper"])
                    if item is not None:
                        references.append(item)

        return record, references, citations
