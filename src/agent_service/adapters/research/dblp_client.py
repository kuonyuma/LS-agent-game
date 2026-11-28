"""DBLP 计算机科学文献库客户端：免 Key 获取 CS 顶会/顶刊发表记录与权威 DOI。"""

from __future__ import annotations

import asyncio
import re
from typing import Any

import httpx

from agent_service.adapters.research.models import (
    PaperRecord,
    canonical_paper_id,
    is_preprint_venue,
    normalize_arxiv_id,
    normalize_doi,
    normalize_title,
    normalize_whitespace,
)
from agent_service.adapters.research.rate_limit import (
    AsyncRateLimiter,
    build_research_headers,
    get_default_limiter,
)

DBLP_PUBL_ENDPOINT = "https://dblp.org/search/publ/api"
DEFAULT_DBLP_TIMEOUT = 18.0

# 常见 CS 顶会别名映射（用于按会议名筛选时兼顾历史/缩写差异）
_VENUE_ALIASES: dict[str, tuple[str, ...]] = {
    "neurips": ("neurips", "nips"),
    "nips": ("neurips", "nips"),
    "icml": ("icml",),
    "iclr": ("iclr",),
    "cvpr": ("cvpr",),
    "iccv": ("iccv",),
    "eccv": ("eccv",),
    "acl": ("acl",),
    "emnlp": ("emnlp",),
    "naacl": ("naacl",),
    "aaai": ("aaai",),
    "ijcai": ("ijcai",),
    "sigmod": ("sigmod",),
    "vldb": ("vldb", "pvldb"),
    "osdi": ("osdi",),
    "sosp": ("sosp",),
    "nsdi": ("nsdi",),
    "sigcomm": ("sigcomm",),
    "ccs": ("ccs",),
    "sp": ("ieee symposium on security and privacy", "sp"),
    "usenix": ("usenix",),
    "icse": ("icse",),
    "fse": ("fse", "esec/sigsoft fse", "sigsoft fse"),
    "ase": ("ase",),
    "pldi": ("pldi",),
    "popl": ("popl",),
    "kdd": ("kdd",),
    "www": ("www", "thewebconf"),
    "sigir": ("sigir",),
}


def _extract_authors(raw_authors: Any) -> tuple[str, ...]:
    if not isinstance(raw_authors, dict):
        return ()
    author_field = raw_authors.get("author")
    entries = author_field if isinstance(author_field, list) else [author_field]
    names: list[str] = []
    for entry in entries:
        if isinstance(entry, str):
            text = entry
        elif isinstance(entry, dict):
            text = str(entry.get("text") or "")
        else:
            continue
        # DBLP 同名作者常带四位消歧编号（如 "Wei Li 0001"），展示时清理后缀
        cleaned = re.sub(r"\s+\d{4}$", "", normalize_whitespace(text))
        if cleaned:
            names.append(cleaned)
    return tuple(names)


def _extract_ee_urls(raw_ee: Any) -> list[str]:
    if isinstance(raw_ee, str) and raw_ee.strip():
        return [raw_ee.strip()]
    if isinstance(raw_ee, list):
        return [str(item).strip() for item in raw_ee if isinstance(item, str) and item.strip()]
    return []


def _classify_dblp_type(raw_type: str, venue: str) -> str:
    lowered = raw_type.lower()
    if is_preprint_venue(venue) or "informal" in lowered:
        return "preprint"
    if "conference" in lowered or "workshop" in lowered:
        return "conference"
    if "journal" in lowered:
        return "journal"
    return ""


def parse_dblp_json(payload: dict[str, Any]) -> list[PaperRecord]:
    """将 DBLP JSON 检索响应解析为统一 PaperRecord 列表。"""
    result = payload.get("result")
    if not isinstance(result, dict):
        return []
    hits_obj = result.get("hits")
    if not isinstance(hits_obj, dict):
        return []
    raw_hits = hits_obj.get("hit")
    if not isinstance(raw_hits, list):
        return []

    records: list[PaperRecord] = []
    for hit in raw_hits:
        if not isinstance(hit, dict):
            continue
        info = hit.get("info")
        if not isinstance(info, dict):
            continue

        title = normalize_whitespace(str(info.get("title") or "")).rstrip(".")
        if not title:
            continue

        authors = _extract_authors(info.get("authors"))
        raw_venue = info.get("venue")
        if isinstance(raw_venue, list):
            venue = normalize_whitespace(" ".join(str(v) for v in raw_venue if v))
        else:
            venue = normalize_whitespace(str(raw_venue or ""))

        raw_year = str(info.get("year") or "").strip()
        year = int(raw_year) if raw_year.isdigit() else None
        raw_type = str(info.get("type") or "")
        pub_type = _classify_dblp_type(raw_type, venue)

        doi = normalize_doi(str(info.get("doi") or ""))
        dblp_key = str(info.get("key") or "").strip()
        dblp_url = str(info.get("url") or "").strip()
        ee_urls = _extract_ee_urls(info.get("ee"))

        arxiv_id = ""
        for ee in ee_urls:
            if "arxiv.org/" in ee.lower():
                arxiv_id = normalize_arxiv_id(ee)
                if arxiv_id:
                    break
            if not doi and "doi.org/" in ee.lower():
                doi = normalize_doi(ee)
        if not arxiv_id and venue.lower() == "corr":
            volume = str(info.get("volume") or "")
            if "abs/" in volume:
                arxiv_id = normalize_arxiv_id(volume)

        display_venue = "arXiv preprint" if venue.lower() == "corr" else venue
        primary_url = (
            f"https://arxiv.org/abs/{arxiv_id}"
            if arxiv_id and is_preprint_venue(display_venue)
            else (ee_urls[0] if ee_urls else dblp_url)
        )
        pdf_url = f"https://arxiv.org/pdf/{arxiv_id}.pdf" if arxiv_id else ""
        html_url = f"https://arxiv.org/html/{arxiv_id}" if arxiv_id else ""

        pid = canonical_paper_id(
            arxiv_id=arxiv_id,
            doi=doi,
            dblp_key=dblp_key,
            fallback_title=title,
        )
        records.append(
            PaperRecord(
                paper_id=pid,
                title=title,
                authors=authors,
                year=year,
                published_date=str(year) if year else "",
                venue=display_venue,
                publication_type=pub_type,
                doi=doi,
                arxiv_id=arxiv_id,
                dblp_key=dblp_key,
                url=primary_url,
                pdf_url=pdf_url,
                html_url=html_url,
                sources=("DBLP",),
            )
        )
    return records


def _matches_venue(paper_venue: str, requested_venue: str) -> bool:
    pv = paper_venue.strip().lower()
    rv = requested_venue.strip().lower()
    if not rv:
        return True
    if not pv or is_preprint_venue(pv):
        return False
    aliases = _VENUE_ALIASES.get(rv, (rv,))
    return any(alias in pv for alias in aliases)


class DblpClient:
    """面向 CS 会议/期刊的异步 DBLP 客户端。"""

    def __init__(
        self,
        *,
        timeout: float = DEFAULT_DBLP_TIMEOUT,
        transport: httpx.AsyncBaseTransport | None = None,
        rate_limiter: AsyncRateLimiter | None = None,
    ) -> None:
        self._timeout = timeout
        self._transport = transport
        self._limiter = rate_limiter or get_default_limiter("dblp")

    async def _query_json(self, query_str: str, hits: int) -> dict[str, Any]:
        headers = build_research_headers(extra={"Accept": "application/json"})
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
                        DBLP_PUBL_ENDPOINT,
                        params={
                            "q": query_str,
                            "format": "json",
                            "h": max(1, min(hits, 40)),
                        },
                        headers=headers,
                    )
                    if response.status_code == 429 and attempt == 0:
                        retry_after = response.headers.get("Retry-After", "")
                        wait_s = float(retry_after) if retry_after.isdigit() else 2.0
                        await asyncio.sleep(min(wait_s, 5.0))
                        continue
                    response.raise_for_status()
                    ctype = response.headers.get("content-type", "").lower()
                    if response.status_code == 202 or "text/html" in ctype:
                        raise ValueError(
                            f"DBLP 公共接口返回反爬虫质询页（HTTP {response.status_code}），无法直接读取 JSON"
                        )
                    data = response.json()
                    return data if isinstance(data, dict) else {}
                except (httpx.HTTPError, OSError, ValueError) as exc:
                    last_exc = exc
                    if attempt == 0 and isinstance(exc, httpx.TimeoutException):
                        continue
                    raise
        if last_exc is not None:
            raise last_exc
        return {}

    async def search(
        self,
        query: str,
        *,
        venue: str | None = None,
        year_from: int | None = None,
        year_to: int | None = None,
        max_results: int = 8,
    ) -> list[PaperRecord]:
        cleaned = normalize_whitespace(query)
        if not cleaned:
            return []
        clean_venue = normalize_whitespace(venue or "")
        query_expr = f"{cleaned} venue:{clean_venue}:" if clean_venue else cleaned
        fetch_hits = (
            min(max_results * 3, 35)
            if (clean_venue or year_from is not None or year_to is not None)
            else min(max(max_results * 2, 10), 30)
        )
        payload = await self._query_json(query_expr, hits=fetch_hits)
        items = parse_dblp_json(payload)

        # 若带 venue: 精确过滤无命中，回退为普通关键词追加 venue 再做本地匹配
        if clean_venue and not items:
            fallback_payload = await self._query_json(
                f"{cleaned} {clean_venue}", hits=fetch_hits
            )
            items = parse_dblp_json(fallback_payload)

        filtered: list[PaperRecord] = []
        for item in items:
            if clean_venue and not _matches_venue(item.venue, clean_venue):
                continue
            if year_from is not None and (item.year is None or item.year < year_from):
                continue
            if year_to is not None and (item.year is None or item.year > year_to):
                continue
            filtered.append(item)
            if len(filtered) >= max_results:
                break
        return filtered

    async def lookup_by_title(self, title: str) -> PaperRecord | None:
        """按论文标题在 DBLP 查找正式发表版本（优先返回非 CoRR 顶会/期刊记录）。"""
        cleaned = normalize_whitespace(title)
        if not cleaned:
            return None
        payload = await self._query_json(cleaned, hits=8)
        items = parse_dblp_json(payload)
        target_norm = normalize_title(cleaned)
        exact_matches = [
            item for item in items if normalize_title(item.title) == target_norm
        ]
        if not exact_matches:
            return None
        # 优先选择正式会议/期刊记录，而非 CoRR 预印本
        for item in exact_matches:
            if not is_preprint_venue(item.venue):
                return item
        return exact_matches[0]
