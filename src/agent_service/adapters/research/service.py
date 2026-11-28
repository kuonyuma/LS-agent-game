"""面向 CS 科研搜索的多源编排服务：并发检索、跨源去重融合、引文追踪与局部降级。

默认零配置主链路：arXiv + OpenAlex（单篇查询免费 $0，免 Key 即支持 CS 顶会、被引数与引文图谱）
+ Crossref / OpenCitations 权威兜底；可选配置 OPENALEX_API_KEY / SEMANTIC_SCHOLAR_API_KEY 扩容增强。
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

import httpx

from agent_service.adapters.research.arxiv_client import ArxivClient, ArxivSortBy
from agent_service.adapters.research.crossref_client import (
    CrossrefClient,
    OpenCitationsClient,
    SemanticScholarClient,
)
from agent_service.adapters.research.dblp_client import DblpClient
from agent_service.adapters.research.fulltext import (
    DEFAULT_FULLTEXT_MAX_CHARS,
    FulltextReadMode,
    PaperFulltextReader,
    extract_fulltext_pages,
    extract_fulltext_section,
    format_fulltext_outline,
    search_in_fulltext,
)
from agent_service.adapters.research.models import (
    CitationItem,
    PaperInspection,
    PaperRecord,
    PaperSearchResponse,
    SourceFailure,
    build_bibtex,
    is_preprint_venue,
    merge_paper_records,
    normalize_arxiv_id,
    normalize_title,
    normalize_whitespace,
    parse_paper_identifier,
)
from agent_service.adapters.research.openalex_client import OpenAlexClient
from agent_service.adapters.research.rate_limit import TTLCache


def _format_exception_reason(exc: BaseException) -> str:
    if isinstance(exc, httpx.TimeoutException):
        return "请求超时"
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        if code == 429:
            return "触发上游频率限制（HTTP 429）"
        return f"HTTP {code}"
    return str(exc) or exc.__class__.__name__


def deduplicate_and_merge_papers(
    buckets: Sequence[Sequence[PaperRecord]],
    *,
    max_results: int,
    sort_by: str = "relevance",
) -> list[PaperRecord]:
    """按 arXiv ID / DOI / 规范化标题对多源论文结果去重融合，并按策略排序。"""
    merged: list[PaperRecord] = []
    index_by_arxiv: dict[str, int] = {}
    index_by_doi: dict[str, int] = {}
    index_by_openalex: dict[str, int] = {}
    index_by_title: dict[str, int] = {}

    for bucket in buckets:
        for paper in bucket:
            existing_idx: int | None = None
            if paper.arxiv_id and paper.arxiv_id in index_by_arxiv:
                existing_idx = index_by_arxiv[paper.arxiv_id]
            elif paper.doi and paper.doi in index_by_doi:
                existing_idx = index_by_doi[paper.doi]
            elif paper.openalex_id and paper.openalex_id in index_by_openalex:
                existing_idx = index_by_openalex[paper.openalex_id]
            else:
                norm_t = normalize_title(paper.title)
                if norm_t and norm_t in index_by_title:
                    existing_idx = index_by_title[norm_t]

            if existing_idx is not None:
                combined = merge_paper_records(merged[existing_idx], paper)
                merged[existing_idx] = combined
                idx = existing_idx
            else:
                combined = paper
                idx = len(merged)
                merged.append(combined)

            if combined.arxiv_id:
                index_by_arxiv[combined.arxiv_id] = idx
            if combined.doi:
                index_by_doi[combined.doi] = idx
            if combined.openalex_id:
                index_by_openalex[combined.openalex_id] = idx
            norm_t = normalize_title(combined.title)
            if norm_t:
                index_by_title[norm_t] = idx

    if sort_by == "date":
        merged.sort(
            key=lambda p: (
                p.published_date or (f"{p.year:04d}-00-00" if p.year else ""),
                0 if is_preprint_venue(p.venue) else 1,
            ),
            reverse=True,
        )
    elif sort_by == "citations":
        merged.sort(
            key=lambda p: (
                p.citation_count if p.citation_count is not None else -1,
                p.year or 0,
            ),
            reverse=True,
        )
    else:
        indexed = list(enumerate(merged))
        indexed.sort(
            key=lambda pair: (
                pair[0]
                - (1.2 if len(pair[1].sources) >= 2 else 0.0)
                - (0.4 if not is_preprint_venue(pair[1].venue) else 0.0)
            )
        )
        merged = [item for _, item in indexed]

    return merged[:max_results]


class AcademicResearchService:
    """零配置 CS 学术检索统一服务层（arXiv + OpenAlex + Crossref + OpenCitations + 可选 S2/DBLP）。"""

    def __init__(
        self,
        *,
        arxiv: ArxivClient | None = None,
        openalex: OpenAlexClient | None = None,
        dblp: DblpClient | None = None,
        crossref: CrossrefClient | None = None,
        opencitations: OpenCitationsClient | None = None,
        semantic_scholar: SemanticScholarClient | None = None,
        fulltext_reader: PaperFulltextReader | None = None,
    ) -> None:
        self.arxiv = arxiv or ArxivClient()
        self.openalex = openalex or OpenAlexClient()
        self.dblp = dblp or DblpClient()
        self.crossref = crossref or CrossrefClient()
        self.opencitations = opencitations or OpenCitationsClient()
        self.semantic_scholar = semantic_scholar or SemanticScholarClient()
        self.fulltext_reader = fulltext_reader or PaperFulltextReader()
        self._search_cache: TTLCache[PaperSearchResponse] = TTLCache(
            max_size=64, ttl_seconds=600.0
        )
        self._paper_cache: TTLCache[PaperRecord] = TTLCache(
            max_size=128, ttl_seconds=1800.0
        )

    def _remember_papers(self, papers: Sequence[PaperRecord]) -> None:
        for paper in papers:
            self._paper_cache.set(paper.paper_id, paper)
            if paper.arxiv_id:
                self._paper_cache.set(f"arxiv:{paper.arxiv_id}", paper)
            if paper.doi:
                self._paper_cache.set(f"doi:{paper.doi}", paper)
            if paper.openalex_id:
                self._paper_cache.set(f"openalex:{paper.openalex_id}", paper)
            if paper.dblp_key:
                self._paper_cache.set(f"dblp:{paper.dblp_key}", paper)

    async def search_papers(
        self,
        query: str,
        *,
        category: str | None = "cs",
        venue: str | None = None,
        year_from: int | None = None,
        year_to: int | None = None,
        sort_by: str = "relevance",
        sources: Sequence[str] | None = None,
        max_results: int = 8,
    ) -> PaperSearchResponse:
        """并发查询适配的 CS 学术源，去重融合并做单源局部降级。"""
        clean_query = normalize_whitespace(query)
        clean_venue = normalize_whitespace(venue or "")
        normalized_sources = [s.strip().lower() for s in (sources or ["auto"]) if s.strip()]
        if not normalized_sources:
            normalized_sources = ["auto"]

        cache_key = (
            f"{clean_query}|{category or ''}|{clean_venue}|{year_from}|{year_to}|"
            f"{sort_by}|{','.join(sorted(normalized_sources))}|{max_results}"
        )
        cached = self._search_cache.get(cache_key)
        if cached is not None:
            return cached

        use_auto = "auto" in normalized_sources
        selected: list[str] = []
        if use_auto:
            # 指定顶会/顶刊或按被引排序时优先 OpenAlex；常规检索并发 OpenAlex + arXiv 互补融合
            if clean_venue:
                selected.append("openalex")
            else:
                selected.extend(["arxiv", "openalex"])
            if self.semantic_scholar.is_configured():
                selected.append("semantic_scholar")
        else:
            for src in normalized_sources:
                if (
                    src in {"arxiv", "openalex", "dblp", "crossref", "semantic_scholar"}
                    and src not in selected
                ):
                    selected.append(src)

        arxiv_sort: ArxivSortBy = "date" if sort_by == "date" else "relevance"
        tasks: list[tuple[str, asyncio.Task[list[PaperRecord]]]] = []

        for src in selected:
            if src == "arxiv":
                tasks.append(
                    (
                        "arXiv",
                        asyncio.create_task(
                            self.arxiv.search(
                                clean_query,
                                category=category,
                                year_from=year_from,
                                year_to=year_to,
                                sort_by=arxiv_sort,
                                max_results=max_results,
                            )
                        ),
                    )
                )
            elif src == "openalex":
                tasks.append(
                    (
                        "OpenAlex",
                        asyncio.create_task(
                            self.openalex.search(
                                clean_query,
                                category=category,
                                venue=clean_venue or None,
                                year_from=year_from,
                                year_to=year_to,
                                sort_by=sort_by,
                                max_results=max_results,
                            )
                        ),
                    )
                )
            elif src == "dblp":
                tasks.append(
                    (
                        "DBLP",
                        asyncio.create_task(
                            self.dblp.search(
                                clean_query,
                                venue=clean_venue or None,
                                year_from=year_from,
                                year_to=year_to,
                                max_results=max_results,
                            )
                        ),
                    )
                )
            elif src == "crossref":
                tasks.append(
                    (
                        "Crossref",
                        asyncio.create_task(
                            self.crossref.search_by_title(
                                clean_query, limit=min(max_results, 10)
                            )
                        ),
                    )
                )
            elif src == "semantic_scholar" and self.semantic_scholar.is_configured():
                tasks.append(
                    (
                        "Semantic Scholar",
                        asyncio.create_task(
                            self.semantic_scholar.search(
                                clean_query,
                                year_from=year_from,
                                year_to=year_to,
                                max_results=max_results,
                            )
                        ),
                    )
                )

        raw_outcomes = await asyncio.gather(
            *(task for _, task in tasks), return_exceptions=True
        )

        buckets: list[list[PaperRecord]] = []
        active_sources: list[str] = []
        failures: list[SourceFailure] = []
        for (label, _), outcome in zip(tasks, raw_outcomes, strict=True):
            if isinstance(outcome, BaseException):
                if isinstance(outcome, asyncio.CancelledError):
                    raise outcome
                failures.append(
                    SourceFailure(source=label, reason=_format_exception_reason(outcome))
                )
            else:
                active_sources.append(label)
                buckets.append(outcome)

        merged_papers = deduplicate_and_merge_papers(
            buckets, max_results=max_results, sort_by=sort_by
        )
        self._remember_papers(merged_papers)

        response = PaperSearchResponse(
            query=clean_query,
            papers=merged_papers,
            active_sources=active_sources,
            failures=failures,
        )
        if active_sources:
            self._search_cache.set(cache_key, response)
        return response

    async def resolve_paper(
        self,
        identifier: str,
    ) -> tuple[PaperRecord | None, list[str], list[CitationItem], list[SourceFailure]]:
        """解析任意 paper_id / arXiv ID / DOI / OpenAlex ID / 标题，并跨源补全发表场所与被引数。"""
        kind, value = parse_paper_identifier(identifier)
        if not value:
            return None, [], [], []

        cached_record = self._paper_cache.get(f"{kind}:{value}") or self._paper_cache.get(
            identifier.strip()
        )
        record: PaperRecord | None = cached_record
        openalex_ref_ids: list[str] = []
        crossref_refs: list[CitationItem] = []
        failures: list[SourceFailure] = []

        if kind == "arxiv":
            arxiv_task = (
                asyncio.create_task(self.arxiv.fetch_by_id(value))
                if (record is None or not record.abstract)
                else None
            )
            oa_task = asyncio.create_task(self.openalex.fetch_work(arxiv_id=value))
            if arxiv_task is not None:
                outcomes = await asyncio.gather(arxiv_task, oa_task, return_exceptions=True)
                ax_out, oa_out = outcomes[0], outcomes[1]
                if isinstance(ax_out, BaseException):
                    if isinstance(ax_out, asyncio.CancelledError):
                        raise ax_out
                    failures.append(
                        SourceFailure(source="arXiv", reason=_format_exception_reason(ax_out))
                    )
                elif ax_out is not None:
                    record = merge_paper_records(ax_out, record) if record else ax_out
            else:
                oa_out = (await asyncio.gather(oa_task, return_exceptions=True))[0]

            if isinstance(oa_out, BaseException):
                if isinstance(oa_out, asyncio.CancelledError):
                    raise oa_out
                failures.append(
                    SourceFailure(source="OpenAlex", reason=_format_exception_reason(oa_out))
                )
            else:
                oa_rec, oa_refs = oa_out
                openalex_ref_ids = oa_refs
                if oa_rec is not None:
                    record = merge_paper_records(record, oa_rec) if record else oa_rec

        elif kind == "openalex":
            try:
                oa_rec, oa_refs = await self.openalex.fetch_work(openalex_id=value)
                openalex_ref_ids = oa_refs
                if oa_rec is not None:
                    record = merge_paper_records(oa_rec, record) if record else oa_rec
            except Exception as exc:  # noqa: BLE001
                failures.append(
                    SourceFailure(source="OpenAlex", reason=_format_exception_reason(exc))
                )

        elif kind == "doi":
            oa_t = asyncio.create_task(self.openalex.fetch_work(doi=value))
            cr_t = asyncio.create_task(self.crossref.fetch_by_doi(value))
            oa_out, cr_out = await asyncio.gather(oa_t, cr_t, return_exceptions=True)
            if isinstance(oa_out, BaseException):
                if isinstance(oa_out, asyncio.CancelledError):
                    raise oa_out
                failures.append(
                    SourceFailure(source="OpenAlex", reason=_format_exception_reason(oa_out))
                )
            else:
                oa_rec, oa_refs = oa_out
                openalex_ref_ids = oa_refs
                if oa_rec is not None:
                    record = merge_paper_records(oa_rec, record) if record else oa_rec

            if isinstance(cr_out, BaseException):
                if isinstance(cr_out, asyncio.CancelledError):
                    raise cr_out
                failures.append(
                    SourceFailure(source="Crossref", reason=_format_exception_reason(cr_out))
                )
            else:
                cr_rec, cr_refs = cr_out
                crossref_refs = cr_refs
                if cr_rec is not None:
                    record = merge_paper_records(record, cr_rec) if record else cr_rec

        elif kind == "dblp":
            if record is None:
                try:
                    hits = await self.dblp.search(value, max_results=3)
                    for hit in hits:
                        if hit.dblp_key == value:
                            record = hit
                            break
                    if record is None and hits:
                        record = hits[0]
                except Exception as exc:  # noqa: BLE001
                    failures.append(
                        SourceFailure(source="DBLP", reason=_format_exception_reason(exc))
                    )

        elif kind == "title":
            oa_t = asyncio.create_task(self.openalex.search(value, category="all", max_results=3))
            arxiv_t = asyncio.create_task(self.arxiv.fetch_by_title(value))
            cr_t = asyncio.create_task(self.crossref.lookup_exact_title(value))
            oa_out, ax_out, cr_out = await asyncio.gather(
                oa_t, arxiv_t, cr_t, return_exceptions=True
            )
            target_norm = normalize_title(value)

            if isinstance(oa_out, BaseException):
                if isinstance(oa_out, asyncio.CancelledError):
                    raise oa_out
                failures.append(
                    SourceFailure(source="OpenAlex", reason=_format_exception_reason(oa_out))
                )
            else:
                for cand in oa_out:
                    if normalize_title(cand.title) == target_norm:
                        record = merge_paper_records(record, cand) if record else cand
                        break
                if record is None and oa_out:
                    record = oa_out[0]

            if isinstance(ax_out, BaseException):
                if isinstance(ax_out, asyncio.CancelledError):
                    raise ax_out
                failures.append(
                    SourceFailure(source="arXiv", reason=_format_exception_reason(ax_out))
                )
            elif ax_out is not None and (
                normalize_title(ax_out.title) == target_norm or record is None
            ):
                record = merge_paper_records(record, ax_out) if record else ax_out

            if isinstance(cr_out, BaseException):
                if isinstance(cr_out, asyncio.CancelledError):
                    raise cr_out
                failures.append(
                    SourceFailure(source="Crossref", reason=_format_exception_reason(cr_out))
                )
            elif cr_out is not None and (
                normalize_title(cr_out.title) == target_norm or record is None
            ):
                record = merge_paper_records(record, cr_out) if record else cr_out

        if record is None:
            return None, openalex_ref_ids, crossref_refs, failures

        # 补全 OpenAlex 单体元数据（如通过标题或缓存命中但缺少 openalex_id/被引数）
        if (not record.openalex_id or record.citation_count is None) and (
            record.doi or record.arxiv_id
        ):
            try:
                oa_rec, oa_refs = await self.openalex.fetch_work(
                    doi=record.doi,
                    arxiv_id=record.arxiv_id,
                )
                if oa_refs and not openalex_ref_ids:
                    openalex_ref_ids = oa_refs
                if oa_rec is not None:
                    record = merge_paper_records(record, oa_rec)
            except Exception as exc:  # noqa: BLE001
                failures.append(
                    SourceFailure(source="OpenAlex", reason=_format_exception_reason(exc))
                )

        # 若已有标题但缺少 arXiv ID 且缺 PDF，补查 arXiv 拿到摘要/HTML/代码链接
        if record.title and not record.arxiv_id and not record.pdf_url:
            try:
                arxiv_match = await self.arxiv.fetch_by_title(record.title)
                if arxiv_match is not None and normalize_title(arxiv_match.title) == normalize_title(
                    record.title
                ):
                    record = merge_paper_records(record, arxiv_match)
            except Exception as exc:  # noqa: BLE001
                failures.append(
                    SourceFailure(source="arXiv", reason=_format_exception_reason(exc))
                )

        # 若有正式非 arXiv DOI 且仍缺被引数或参考文献，再用 Crossref 补全
        if (
            record.doi
            and not record.doi.startswith("10.48550/arxiv.")
            and (record.citation_count is None or (not crossref_refs and not openalex_ref_ids))
        ):
            try:
                cr_rec, cr_refs = await self.crossref.fetch_by_doi(record.doi)
                if cr_rec is not None:
                    record = merge_paper_records(record, cr_rec)
                if cr_refs:
                    crossref_refs = cr_refs
            except Exception as exc:  # noqa: BLE001
                failures.append(
                    SourceFailure(source="Crossref", reason=_format_exception_reason(exc))
                )

        self._remember_papers([record])
        return record, openalex_ref_ids, crossref_refs, failures

    async def _resolve_dois_to_citations(
        self,
        dois: Sequence[str],
        *,
        limit: int,
    ) -> list[CitationItem]:
        """并发将若干 DOI 解析为带标题、年份、会议的引文条目（限并发防限流）。"""
        semaphore = asyncio.Semaphore(3)

        async def _one(doi: str) -> CitationItem:
            async with semaphore:
                try:
                    rec, _ = await self.crossref.fetch_by_doi(doi)
                except (httpx.HTTPError, OSError, ValueError):
                    rec = None
                if rec is not None:
                    return CitationItem(
                        paper_id=rec.paper_id,
                        title=rec.title,
                        authors=rec.authors,
                        year=rec.year,
                        venue=rec.venue,
                        doi=rec.doi,
                        arxiv_id=rec.arxiv_id,
                        citation_count=rec.citation_count,
                    )
                return CitationItem(
                    paper_id=f"doi:{doi}",
                    title=f"DOI: {doi}",
                    doi=doi,
                )

        targets = list(dois[:limit])
        if not targets:
            return []
        return list(await asyncio.gather(*(_one(d) for d in targets)))

    async def inspect_paper(
        self,
        paper_id: str,
        *,
        include: Sequence[str] = ("metadata", "bibtex"),
        limit: int = 10,
    ) -> PaperInspection | None:
        """单篇论文深度透视：元数据核验、发表状态反查、BibTeX 生成与引文图谱。"""
        wanted = {item.strip().lower() for item in include if item.strip()}
        if not wanted:
            wanted = {"metadata", "bibtex"}

        want_citations = "citations" in wanted
        want_references = "references" in wanted
        want_bibtex = "bibtex" in wanted

        record, openalex_ref_ids, crossref_refs, failures = await self.resolve_paper(paper_id)
        notes: list[str] = []
        references: list[CitationItem] = []
        citations: list[CitationItem] = []

        # 可选 Semantic Scholar 增强（配置了 SEMANTIC_SCHOLAR_API_KEY 才激活）
        if self.semantic_scholar.is_configured():
            s2_lookup = ""
            if record is not None:
                if record.s2_id:
                    s2_lookup = record.s2_id
                elif record.arxiv_id:
                    s2_lookup = f"ARXIV:{record.arxiv_id}"
                elif record.doi:
                    s2_lookup = f"DOI:{record.doi}"
            else:
                kind, val = parse_paper_identifier(paper_id)
                if kind == "arxiv":
                    s2_lookup = f"ARXIV:{val}"
                elif kind == "doi":
                    s2_lookup = f"DOI:{val}"
                elif kind == "s2":
                    s2_lookup = val

            if s2_lookup:
                try:
                    s2_rec, s2_refs, s2_cites = await self.semantic_scholar.fetch_paper_and_graph(
                        s2_lookup,
                        include_citations=want_citations,
                        include_references=want_references,
                        limit=limit,
                    )
                    if s2_rec is not None:
                        record = merge_paper_records(record, s2_rec) if record else s2_rec
                    if s2_refs:
                        references = s2_refs[:limit]
                    if s2_cites:
                        citations = s2_cites[:limit]
                except Exception as exc:  # noqa: BLE001
                    failures.append(
                        SourceFailure(
                            source="Semantic Scholar",
                            reason=_format_exception_reason(exc),
                        )
                    )

        if record is None:
            return None

        self._remember_papers([record])
        effective_doi = record.doi or (
            f"10.48550/arxiv.{record.arxiv_id}" if record.arxiv_id else ""
        )

        # 参考文献（Backward）：优先 Crossref/OpenAlex 批量解析 → 回退 OpenCitations
        if want_references and not references:
            if openalex_ref_ids:
                try:
                    references = await self.openalex.fetch_works_by_ids(
                        openalex_ref_ids, limit=limit
                    )
                except Exception as exc:  # noqa: BLE001
                    failures.append(
                        SourceFailure(source="OpenAlex", reason=_format_exception_reason(exc))
                    )
            if not references and crossref_refs:
                references = crossref_refs[:limit]
            if not references and effective_doi:
                try:
                    ref_dois = await self.opencitations.fetch_referenced_dois(
                        effective_doi, limit=limit
                    )
                    if ref_dois:
                        references = await self._resolve_dois_to_citations(
                            ref_dois, limit=limit
                        )
                except Exception as exc:  # noqa: BLE001
                    failures.append(
                        SourceFailure(
                            source="OpenCitations",
                            reason=_format_exception_reason(exc),
                        )
                    )
            if not references:
                notes.append(
                    "未从开放元数据接口获取到结构化参考文献列表（可调用 "
                    "`read_paper_fulltext(..., mode='section', section_query='References')` 直接查看原文参考文献）。"
                )

        # 施引论文（Forward）：优先 OpenAlex 高被引排序一次拉取 → 回退 OpenCitations
        if want_citations and not citations:
            if record.openalex_id:
                try:
                    citations = await self.openalex.fetch_citing_works(
                        record.openalex_id, limit=limit
                    )
                except Exception as exc:  # noqa: BLE001
                    failures.append(
                        SourceFailure(source="OpenAlex", reason=_format_exception_reason(exc))
                    )
            if not citations and effective_doi:
                try:
                    citing_dois = await self.opencitations.fetch_citing_dois(
                        effective_doi, limit=limit
                    )
                    if citing_dois:
                        citations = await self._resolve_dois_to_citations(
                            citing_dois, limit=limit
                        )
                except Exception as exc:  # noqa: BLE001
                    failures.append(
                        SourceFailure(
                            source="OpenCitations",
                            reason=_format_exception_reason(exc),
                        )
                    )
            if not citations:
                notes.append("开放引文图谱暂未收录该论文的施引记录（可能为近期新发预印本）。")

        bibtex = build_bibtex(record) if want_bibtex else ""
        return PaperInspection(
            paper=record,
            bibtex=bibtex,
            references=references,
            citations=citations,
            notes=notes,
            failures=failures,
        )

    async def read_paper_fulltext(
        self,
        *,
        paper_id: str = "",
        url: str = "",
        mode: FulltextReadMode = "outline",
        section_query: str = "",
        page_range: str = "",
        max_chars: int = DEFAULT_FULLTEXT_MAX_CHARS,
    ) -> str:
        """解析并按需切片返回 CS 论文全文（outline / section / query / pages）。"""
        resolved_id = paper_id.strip()
        title = ""
        html_url = ""
        pdf_url = url.strip()

        if resolved_id:
            kind, val = parse_paper_identifier(resolved_id)
            if kind == "arxiv":
                resolved_id = f"arxiv:{val}"
                html_url = f"https://arxiv.org/html/{val}"
                if not pdf_url:
                    pdf_url = f"https://arxiv.org/pdf/{val}.pdf"
                cached = self._paper_cache.get(resolved_id)
                if cached is not None:
                    title = cached.title
            else:
                record, _, _, _ = await self.resolve_paper(resolved_id)
                if record is not None:
                    resolved_id = record.paper_id
                    title = record.title
                    html_url = record.html_url
                    if not pdf_url:
                        pdf_url = record.pdf_url or (
                            f"https://arxiv.org/pdf/{record.arxiv_id}.pdf"
                            if record.arxiv_id
                            else ""
                        )
        elif pdf_url:
            arxiv_from_url = normalize_arxiv_id(pdf_url)
            if arxiv_from_url:
                resolved_id = f"arxiv:{arxiv_from_url}"
                html_url = f"https://arxiv.org/html/{arxiv_from_url}"
                pdf_url = f"https://arxiv.org/pdf/{arxiv_from_url}.pdf"
            else:
                resolved_id = pdf_url

        if not html_url and not pdf_url:
            raise ValueError(
                f"未找到论文 `{paper_id or url}` 的开放获取全文链接（HTML/PDF）。"
                "若该论文为闭源出版商论文，请先用 `inspect_paper` 确认是否存在 arXiv 预印本或提供公开 PDF URL。"
            )

        doc = await self.fulltext_reader.load(
            paper_id=resolved_id,
            title=title,
            html_url=html_url,
            pdf_url=pdf_url,
            prefer_pdf=(mode == "pages"),
        )
        if mode == "outline":
            return format_fulltext_outline(doc)
        if mode == "section":
            return extract_fulltext_section(doc, section_query, max_chars=max_chars)
        if mode == "query":
            return search_in_fulltext(doc, section_query, max_chars=max_chars)
        return extract_fulltext_pages(doc, page_range, max_chars=max_chars)
