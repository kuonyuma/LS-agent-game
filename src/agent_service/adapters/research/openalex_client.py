"""OpenAlex 开放学术图谱客户端：零配置免 Key 即用（可选 OPENALEX_API_KEY 扩容 10 倍）。

提供 CS 顶会/顶刊正式发表场所、真实被引数、开放获取 PDF、倒排摘要还原与一步引文图谱查询。
"""

from __future__ import annotations

import asyncio
import os
from typing import Any
from urllib.parse import quote

import httpx

from agent_service.adapters.research.models import (
    CitationItem,
    PaperRecord,
    canonical_paper_id,
    extract_code_urls,
    is_preprint_venue,
    normalize_arxiv_id,
    normalize_doi,
    normalize_whitespace,
)
from agent_service.adapters.research.rate_limit import (
    AsyncRateLimiter,
    build_research_headers,
    get_default_limiter,
    resolve_contact_email,
)

OPENALEX_WORKS_ENDPOINT = "https://api.openalex.org/works"
DEFAULT_OPENALEX_TIMEOUT = 18.0
_CS_FIELD_FILTER = "primary_topic.field.id:17"

_SELECT_FIELDS = (
    "id,doi,ids,title,publication_year,publication_date,type,"
    "cited_by_count,authorships,primary_location,best_oa_location,"
    "open_access,abstract_inverted_index,referenced_works"
)
_EDGE_SELECT_FIELDS = (
    "id,doi,title,publication_year,cited_by_count,primary_location,authorships"
)

# CS 顶会缩写与 OpenAlex 正式全称映射（用于按顶会筛选）
_OPENALEX_VENUE_ALIASES: dict[str, tuple[str, ...]] = {
    "neurips": ("neural information processing systems", "neurips", "nips"),
    "nips": ("neural information processing systems", "neurips", "nips"),
    "icml": ("international conference on machine learning", "icml"),
    "iclr": ("international conference on learning representations", "iclr"),
    "cvpr": ("computer vision and pattern recognition", "cvpr"),
    "iccv": ("international conference on computer vision", "iccv"),
    "eccv": ("european conference on computer vision", "eccv"),
    "acl": ("association for computational linguistics", "acl"),
    "emnlp": ("empirical methods in natural language processing", "emnlp"),
    "naacl": ("north american chapter", "naacl"),
    "aaai": ("aaai conference on artificial intelligence", "aaai"),
    "ijcai": ("international joint conference on artificial intelligence", "ijcai"),
    "sigmod": ("management of data", "sigmod"),
    "vldb": ("very large data bases", "vldb", "pvldb"),
    "osdi": ("operating systems design and implementation", "osdi"),
    "sosp": ("symposium on operating systems principles", "sosp"),
    "nsdi": ("networked systems design and implementation", "nsdi"),
    "sigcomm": ("sigcomm",),
    "ccs": ("computer and communications security", "ccs"),
    "sp": ("symposium on security and privacy",),
    "icse": ("international conference on software engineering", "icse"),
    "fse": ("foundations of software engineering", "fse"),
    "ase": ("automated software engineering", "ase"),
    "pldi": ("programming language design and implementation", "pldi"),
    "popl": ("principles of programming languages", "popl"),
    "kdd": ("knowledge discovery and data mining", "kdd"),
    "www": ("web conference", "world wide web", "www"),
    "sigir": ("research and development in information retrieval", "sigir"),
}


def reconstruct_abstract(inverted_index: Any) -> str:
    """将 OpenAlex 的 abstract_inverted_index 还原为可读明文摘要。"""
    if not isinstance(inverted_index, dict) or not inverted_index:
        return ""
    word_positions: list[tuple[int, str]] = []
    for word, positions in inverted_index.items():
        if not isinstance(word, str) or not isinstance(positions, list):
            continue
        for pos in positions:
            if isinstance(pos, int):
                word_positions.append((pos, word))
    if not word_positions:
        return ""
    word_positions.sort(key=lambda pair: pair[0])
    return normalize_whitespace(" ".join(word for _, word in word_positions))


def _short_openalex_id(raw_id: str) -> str:
    text = raw_id.strip().rstrip("/")
    if not text:
        return ""
    return text.rsplit("/", 1)[-1].upper()


def _extract_location_info(loc: Any) -> tuple[str, str, str]:
    """从 OpenAlex location 对象提取 (source_name, landing_url, pdf_url)。"""
    if not isinstance(loc, dict):
        return "", "", ""
    source_obj = loc.get("source")
    source_name = (
        normalize_whitespace(str(source_obj.get("display_name") or ""))
        if isinstance(source_obj, dict)
        else ""
    )
    landing = str(loc.get("landing_page_url") or "").strip()
    pdf_url = str(loc.get("pdf_url") or "").strip()
    return source_name, landing, pdf_url


def _classify_openalex_type(raw_type: str, venue: str) -> str:
    lowered = raw_type.lower()
    if is_preprint_venue(venue) or "preprint" in lowered:
        return "preprint"
    if "conference" in lowered or "proceedings" in lowered:
        return "conference"
    if "article" in lowered or "journal" in lowered:
        return "journal"
    return ""


def parse_openalex_work(raw: dict[str, Any]) -> tuple[PaperRecord | None, list[str]]:
    """解析单条 OpenAlex work JSON，返回 (PaperRecord, referenced_work_ids)。"""
    title = normalize_whitespace(str(raw.get("title") or ""))
    if not title:
        return None, []

    openalex_id = _short_openalex_id(str(raw.get("id") or ""))
    doi = normalize_doi(str(raw.get("doi") or ""))

    arxiv_id = ""
    if doi.startswith("10.48550/arxiv."):
        arxiv_id = normalize_arxiv_id(doi.split("10.48550/arxiv.", 1)[1])

    primary_venue, primary_landing, primary_pdf = _extract_location_info(
        raw.get("primary_location")
    )
    best_venue, best_landing, best_pdf = _extract_location_info(
        raw.get("best_oa_location")
    )
    oa_obj = raw.get("open_access")
    oa_url = (
        str(oa_obj.get("oa_url") or "").strip()
        if isinstance(oa_obj, dict)
        else ""
    )

    for candidate_url in (primary_landing, primary_pdf, best_landing, best_pdf, oa_url):
        if not arxiv_id and "arxiv.org/" in candidate_url.lower():
            arxiv_id = normalize_arxiv_id(candidate_url)

    venue = primary_venue if not is_preprint_venue(primary_venue) else (best_venue or primary_venue)
    if venue.lower().startswith("arxiv"):
        venue = "arXiv preprint"

    pub_type = _classify_openalex_type(str(raw.get("type") or ""), venue)

    authors_list: list[str] = []
    raw_authorships = raw.get("authorships")
    if isinstance(raw_authorships, list):
        for auth in raw_authorships:
            if not isinstance(auth, dict):
                continue
            author_obj = auth.get("author")
            name = (
                normalize_whitespace(str(author_obj.get("display_name") or ""))
                if isinstance(author_obj, dict)
                else ""
            )
            if not name:
                name = normalize_whitespace(str(auth.get("raw_author_name") or ""))
            if name:
                authors_list.append(name)

    year_val = raw.get("publication_year")
    year = int(year_val) if isinstance(year_val, int) else None
    published_date = str(raw.get("publication_date") or (str(year) if year else ""))

    cited = raw.get("cited_by_count")
    citation_count = int(cited) if isinstance(cited, int) and cited >= 0 else None

    abstract = reconstruct_abstract(raw.get("abstract_inverted_index"))
    pdf_url = primary_pdf or best_pdf or (oa_url if oa_url.lower().endswith(".pdf") else "")
    if not pdf_url and arxiv_id:
        pdf_url = f"https://arxiv.org/pdf/{arxiv_id}.pdf"
    html_url = f"https://arxiv.org/html/{arxiv_id}" if arxiv_id else ""
    url = primary_landing or (f"https://doi.org/{doi}" if doi else "") or (
        f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else str(raw.get("id") or "")
    )

    # 正式出版商 DOI 优先保留；若仅为 DataCite arXiv DOI，保持在 doi 字段便于交叉核验
    pid = canonical_paper_id(
        arxiv_id=arxiv_id,
        doi=doi,
        openalex_id=openalex_id,
        fallback_title=title,
    )
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
        openalex_id=openalex_id,
        url=url,
        pdf_url=pdf_url,
        html_url=html_url,
        citation_count=citation_count,
        code_urls=extract_code_urls(abstract),
        sources=("OpenAlex",),
    )

    raw_refs = raw.get("referenced_works")
    ref_ids: list[str] = []
    if isinstance(raw_refs, list):
        for ref in raw_refs:
            if isinstance(ref, str) and ref.strip():
                short_id = _short_openalex_id(ref)
                if short_id:
                    ref_ids.append(short_id)

    return record, ref_ids


def _matches_openalex_venue(venue: str, requested: str) -> bool:
    vl = venue.strip().lower()
    rl = requested.strip().lower()
    if not rl:
        return True
    if not vl or is_preprint_venue(vl):
        return False
    aliases = _OPENALEX_VENUE_ALIASES.get(rl, (rl,))
    return any(alias in vl for alias in aliases)


class OpenAlexClient:
    """OpenAlex 异步客户端：零配置免 Key 即用，配置 OPENALEX_API_KEY 自动扩容。"""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        contact_email: str | None = None,
        timeout: float = DEFAULT_OPENALEX_TIMEOUT,
        transport: httpx.AsyncBaseTransport | None = None,
        rate_limiter: AsyncRateLimiter | None = None,
    ) -> None:
        self._explicit_key = api_key
        self._contact_email = contact_email
        self._timeout = timeout
        self._transport = transport
        self._limiter = rate_limiter or get_default_limiter("openalex")

    def _resolve_key(self) -> str:
        if self._explicit_key is not None:
            return self._explicit_key.strip()
        return os.getenv("OPENALEX_API_KEY", "").strip()

    async def _get_json(self, url: str, params: dict[str, str | int]) -> dict[str, Any]:
        query_params = dict(params)
        api_key = self._resolve_key()
        if api_key:
            query_params["api_key"] = api_key
        email = resolve_contact_email(self._contact_email)
        if email:
            query_params["mailto"] = email

        headers = build_research_headers(
            contact_email=self._contact_email,
            extra={"Accept": "application/json"},
        )
        for attempt in range(2):
            await self._limiter.acquire()
            async with httpx.AsyncClient(
                timeout=self._timeout,
                follow_redirects=True,
                transport=self._transport,
            ) as client:
                response = await client.get(url, params=query_params, headers=headers)
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
        category: str | None = "cs",
        venue: str | None = None,
        year_from: int | None = None,
        year_to: int | None = None,
        sort_by: str = "relevance",
        max_results: int = 8,
    ) -> list[PaperRecord]:
        cleaned = normalize_whitespace(query)
        if not cleaned:
            return []
        clean_venue = normalize_whitespace(venue or "")

        filters: list[str] = []
        cat_lower = (category or "cs").strip().lower()
        if cat_lower != "all":
            filters.append(_CS_FIELD_FILTER)
        if year_from is not None and year_to is not None:
            filters.append(f"publication_year:{year_from}-{year_to}")
        elif year_from is not None:
            filters.append(f"publication_year:>{year_from - 1}")
        elif year_to is not None:
            filters.append(f"publication_year:<{year_to + 1}")

        search_expr = f"{cleaned} {clean_venue}" if clean_venue else cleaned
        fetch_count = min(max_results * 3, 25) if clean_venue else min(max(max_results, 1), 20)
        params: dict[str, str | int] = {
            "search": search_expr,
            "per-page": fetch_count,
            "select": _SELECT_FIELDS,
        }
        if filters:
            params["filter"] = ",".join(filters)
        if sort_by == "citations":
            params["sort"] = "cited_by_count:desc"
        elif sort_by == "date":
            params["sort"] = "publication_date:desc"

        payload = await self._get_json(OPENALEX_WORKS_ENDPOINT, params)
        raw_results = payload.get("results")
        if not isinstance(raw_results, list):
            return []

        papers: list[PaperRecord] = []
        for entry in raw_results:
            if not isinstance(entry, dict):
                continue
            rec, _ = parse_openalex_work(entry)
            if rec is None:
                continue
            if clean_venue and not _matches_openalex_venue(rec.venue, clean_venue):
                continue
            papers.append(rec)
            if len(papers) >= max_results:
                break
        return papers

    async def fetch_work(
        self,
        *,
        openalex_id: str = "",
        doi: str = "",
        arxiv_id: str = "",
    ) -> tuple[PaperRecord | None, list[str]]:
        """单篇精确查询（在 OpenAlex 上属于免费 $0 单实体读取）。"""
        target = ""
        if openalex_id:
            target = _short_openalex_id(openalex_id)
        elif doi:
            target = f"https://doi.org/{normalize_doi(doi)}"
        elif arxiv_id:
            target = f"https://doi.org/10.48550/arxiv.{normalize_arxiv_id(arxiv_id)}"
        if not target:
            return None, []

        encoded = quote(target, safe=":/")
        payload = await self._get_json(
            f"{OPENALEX_WORKS_ENDPOINT}/{encoded}",
            params={"select": _SELECT_FIELDS},
        )
        if not payload or not payload.get("id"):
            return None, []
        return parse_openalex_work(payload)

    async def fetch_citing_works(
        self,
        openalex_id: str,
        *,
        limit: int = 10,
    ) -> list[CitationItem]:
        """按高被引排序一次拉取施引论文（Forward Citations）。"""
        wid = _short_openalex_id(openalex_id)
        if not wid:
            return []
        payload = await self._get_json(
            OPENALEX_WORKS_ENDPOINT,
            params={
                "filter": f"cites:{wid}",
                "sort": "cited_by_count:desc",
                "per-page": max(1, min(limit, 25)),
                "select": _EDGE_SELECT_FIELDS,
            },
        )
        return self._results_to_citation_items(payload.get("results"))

    async def fetch_works_by_ids(
        self,
        work_ids: list[str],
        *,
        limit: int = 10,
    ) -> list[CitationItem]:
        """用一次批量过滤请求把多个 referenced_works ID 转为带标题/年份的参考文献条目。"""
        short_ids = [_short_openalex_id(wid) for wid in work_ids if wid.strip()]
        unique_ids = list(dict.fromkeys(short_ids))[: max(1, min(limit, 25))]
        if not unique_ids:
            return []
        payload = await self._get_json(
            OPENALEX_WORKS_ENDPOINT,
            params={
                "filter": f"openalex_id:{'|'.join(unique_ids)}",
                "per-page": len(unique_ids),
                "select": _EDGE_SELECT_FIELDS,
            },
        )
        return self._results_to_citation_items(payload.get("results"))

    @staticmethod
    def _results_to_citation_items(raw_results: Any) -> list[CitationItem]:
        if not isinstance(raw_results, list):
            return []
        items: list[CitationItem] = []
        for entry in raw_results:
            if not isinstance(entry, dict):
                continue
            rec, _ = parse_openalex_work(entry)
            if rec is None:
                continue
            items.append(
                CitationItem(
                    paper_id=rec.paper_id,
                    title=rec.title,
                    authors=rec.authors,
                    year=rec.year,
                    venue=rec.venue,
                    doi=rec.doi,
                    arxiv_id=rec.arxiv_id,
                    citation_count=rec.citation_count,
                )
            )
        return items
