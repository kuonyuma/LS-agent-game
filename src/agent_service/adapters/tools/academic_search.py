"""面向计算机科学（CS）科研搜索的三大只读内置工具。

- search_papers: arXiv + DBLP（+ 可选 Semantic Scholar）多源并发检索、去重融合、顶会标注与代码仓库提取
- inspect_paper: 单篇详情、发表场所核验、引文/参考文献图谱追踪与标准 BibTeX 导出
- read_paper_fulltext: 优先 arXiv HTML、回退 PyMuPDF 解析开放获取 PDF，按目录/章节/关键词/页码切片精读
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, ClassVar, cast

import httpx

from agent_service.adapters.research.fulltext import (
    DEFAULT_FULLTEXT_MAX_CHARS,
    MAX_FULLTEXT_CHARS,
    VALID_READ_MODES,
    FulltextReadMode,
)
from agent_service.adapters.research.models import (
    CitationItem,
    PaperInspection,
    PaperRecord,
    PaperSearchResponse,
    is_preprint_venue,
)
from agent_service.adapters.research.service import AcademicResearchService
from agent_service.kernel.dispatch.base import Tool, ToolResult

DEFAULT_PAPER_RESULTS = 8
MAX_PAPER_RESULTS = 15
DEFAULT_CITATION_LIMIT = 10
MAX_CITATION_LIMIT = 20
_VALID_SORT_MODES = frozenset({"relevance", "date", "citations"})
_VALID_SOURCES = frozenset({"auto", "arxiv", "openalex", "dblp", "crossref", "semantic_scholar"})
_VALID_INCLUDES = frozenset({"metadata", "references", "citations", "bibtex"})


def _format_authors(authors: tuple[str, ...], max_shown: int = 5) -> str:
    if not authors:
        return "未知作者"
    if len(authors) <= max_shown:
        return ", ".join(authors)
    return f"{', '.join(authors[:max_shown])} 等（共 {len(authors)} 位）"


def _format_venue_badge(paper: PaperRecord) -> str:
    if paper.venue and not is_preprint_venue(paper.venue):
        return f"{paper.venue}（{paper.year}）" if paper.year else paper.venue
    if paper.arxiv_id:
        return f"arXiv 预印本（{paper.published_date or paper.year or '未知日期'}）"
    return paper.venue or (str(paper.year) if paper.year else "发表场所未知")


def format_paper_search_response(response: PaperSearchResponse) -> str:
    """将多源检索结果格式化为高信息密度 Markdown。"""
    source_summary = " + ".join(response.active_sources) if response.active_sources else "无可用源"
    lines: list[str] = [
        (
            f"### 🎓 CS 学术论文检索结果：`{response.query}`"
            f"（返回 {len(response.papers)} 篇 · 命中源：{source_summary}）"
        ),
    ]
    if response.failures:
        warn_parts = [f"{item.source}（{item.reason}）" for item in response.failures]
        lines.append(f"> ⚠️ 部分数据源降级：{'；'.join(warn_parts)}。已展示其余可用源结果。")
    lines.append("")

    if not response.papers:
        lines.append(
            "未检索到匹配的 CS 学术论文。建议：\n"
            "1. 使用规范的英文 CS 专业术语重新检索（中文关键词在 arXiv/DBLP 命中率较低）；\n"
            "2. 放宽 `venue`、`category`（如改为 `'cs'` 或 `'all'`）或 `year_from` 筛选条件。"
        )
        return "\n".join(lines)

    for idx, paper in enumerate(response.papers, start=1):
        venue_text = _format_venue_badge(paper)
        cite_text = (
            f" | 被引：{paper.citation_count}"
            + (
                f"（高影响 {paper.influential_citation_count}）"
                if paper.influential_citation_count is not None
                else ""
            )
            if paper.citation_count is not None
            else ""
        )
        lines.append(f"{idx}. **{paper.title}**")
        lines.append(
            f"   - **Paper ID**: `{paper.paper_id}` | **发表**: {venue_text}{cite_text}"
        )
        lines.append(f"   - **作者**: {_format_authors(paper.authors)}")

        links: list[str] = []
        if paper.doi:
            links.append(f"DOI: `{paper.doi}`")
        if paper.arxiv_id:
            links.append(f"arXiv: `{paper.arxiv_id}`")
        if paper.url:
            links.append(f"链接: `{paper.url}`")
        if paper.pdf_url:
            links.append(f"PDF: `{paper.pdf_url}`")
        if links:
            lines.append(f"   - **标识与链接**: {' | '.join(links)}")

        if paper.categories:
            lines.append(f"   - **分类**: `{', '.join(paper.categories[:4])}`")
        if paper.code_urls:
            lines.append(
                f"   - **代码仓库**: {', '.join(f'`{u}`' for u in paper.code_urls[:3])}"
            )
        if paper.tldr:
            lines.append(f"   - **TLDR**: {paper.tldr}")
        if paper.abstract:
            abstract_preview = (
                paper.abstract[:420] + "..."
                if len(paper.abstract) > 420
                else paper.abstract
            )
            lines.append(f"   - **摘要**: {abstract_preview}")
        else:
            lines.append("   - **摘要**: （当前源未返回摘要；可调用 `inspect_paper` 或 `read_paper_fulltext` 获取详情）")
        if paper.sources:
            lines.append(f"   - **来源**: {' + '.join(paper.sources)}")

    lines.append("")
    lines.append(
        "> 下一步建议：调用 `inspect_paper(paper_id=...)` 核验正式发表场所/被引数/引文图谱并导出 BibTeX；"
        "调用 `read_paper_fulltext(paper_id=..., mode='outline')` 查看章节目录后再按需精读 Method/Experiments。"
    )
    return "\n".join(lines)


def _format_citation_list(title: str, items: list[CitationItem]) -> list[str]:
    if not items:
        return []
    lines = ["", f"#### {title}（共 {len(items)} 条）"]
    for idx, item in enumerate(items, start=1):
        meta_parts: list[str] = []
        if item.year is not None:
            meta_parts.append(str(item.year))
        if item.venue:
            meta_parts.append(item.venue)
        if item.citation_count is not None:
            meta_parts.append(f"被引 {item.citation_count}")
        meta = f"（{' · '.join(meta_parts)}）" if meta_parts else ""
        author_text = f" — {_format_authors(item.authors, max_shown=3)}" if item.authors else ""
        lines.append(f"{idx}. **{item.title}**{meta}{author_text} | `{item.paper_id}`")
    return lines


def format_paper_inspection(inspection: PaperInspection) -> str:
    """将单篇论文深度透视结果格式化为高可读性 Markdown。"""
    p = inspection.paper
    lines = [
        f"### 🔬 论文核验与详情：{p.title}",
        f"- **Paper ID**：`{p.paper_id}`",
        f"- **作者**：{_format_authors(p.authors, max_shown=12)}",
        f"- **发表状态**：{_format_venue_badge(p)}",
    ]
    if p.citation_count is not None:
        inf = (
            f"（其中高影响力引用 {p.influential_citation_count} 次）"
            if p.influential_citation_count is not None
            else ""
        )
        lines.append(f"- **被引次数**：{p.citation_count}{inf}")
    if p.doi:
        lines.append(f"- **DOI**：`{p.doi}`")
    if p.arxiv_id:
        lines.append(f"- **arXiv ID**：`{p.arxiv_id}`")
    if p.openalex_id:
        lines.append(f"- **OpenAlex ID**：`{p.openalex_id}`")
    if p.dblp_key:
        lines.append(f"- **DBLP Key**：`{p.dblp_key}`")
    if p.categories:
        lines.append(f"- **学科分类**：`{', '.join(p.categories)}`")
    if p.comment:
        lines.append(f"- **作者备注**：{p.comment}")
    if p.url:
        lines.append(f"- **主页链接**：`{p.url}`")
    if p.pdf_url:
        lines.append(f"- **PDF 链接**：`{p.pdf_url}`")
    if p.html_url:
        lines.append(f"- **HTML 全文链接**：`{p.html_url}`")
    if p.code_urls:
        lines.append(f"- **官方代码**：{', '.join(f'`{u}`' for u in p.code_urls)}")
    if p.sources:
        lines.append(f"- **数据核验来源**：{' + '.join(p.sources)}")

    if p.tldr:
        lines.extend(["", "#### TLDR", p.tldr])
    if p.abstract:
        lines.extend(["", "#### Abstract", p.abstract])

    if inspection.bibtex:
        lines.extend(["", "#### BibTeX", "```bibtex", inspection.bibtex, "```"])

    lines.extend(_format_citation_list("参考文献（References / Backward）", inspection.references))
    lines.extend(_format_citation_list("施引论文（Citations / Forward）", inspection.citations))

    if inspection.notes:
        lines.append("")
        for note in inspection.notes:
            lines.append(f"> 说明：{note}")
    if inspection.failures:
        warn_parts = [f"{item.source}（{item.reason}）" for item in inspection.failures]
        lines.append(f"> ⚠️ 部分辅助源暂不可用：{'；'.join(warn_parts)}。")

    return "\n".join(lines)


class SearchPapersTool(Tool):
    """面向 CS 的零配置多源学术文献检索工具（arXiv + DBLP + 可选 Semantic Scholar）。"""

    name = "search_papers"
    description = (
        "面向计算机科学（CS）的多源学术论文检索工具，零配置免 Key 聚合 arXiv（最新预印本/摘要/代码链接）"
        "与 OpenAlex（NeurIPS/ICLR/ICML/CVPR/ACL/OSDI/ICSE 等顶会顶刊正式发表状态、真实被引数与 OA PDF），"
        f"自动跨源去重融合并提取官方 GitHub 代码仓库（当前年份：{datetime.now(UTC).year}）。"
        "注意：请务必传入规范的英文 CS 专业术语（中文需求请先翻译并拆分为精准英文检索词再调用）。"
        "查到候选论文后，可用 `inspect_paper` 追踪引文/导出 BibTeX，或用 `read_paper_fulltext` 精读方法与实验细节。"
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "OBJECT",
        "properties": {
            "query": {
                "type": "STRING",
                "description": "英文 CS 检索词或短语（如 'LLM agent episodic memory'、'flash attention'），建议精炼准确",
            },
            "category": {
                "type": "STRING",
                "description": (
                    "可选 CS 子方向过滤（默认 'cs' 限定全部计算机学科，传 'all' 不限学科）："
                    "'ai'、'ml'、'nlp'、'cv'、'se'、'systems'、'os'、'distributed'、'security'、'db'、'net'、'robotics'、'pl'、'theory'、'hci'、'ir'，"
                    "或原生 arXiv 分类（如 'cs.LG'、'cs.CL'）"
                ),
            },
            "venue": {
                "type": "STRING",
                "description": "可选：按 CS 顶会或期刊筛选（如 'NeurIPS'、'ICLR'、'ICML'、'CVPR'、'ACL'、'ICSE'、'OSDI'、'SOSP'、'CCS'）",
            },
            "year_from": {
                "type": "INTEGER",
                "description": "可选：起始发表年份（如 2023）",
            },
            "year_to": {
                "type": "INTEGER",
                "description": "可选：截止发表年份（如 2026）",
            },
            "sort_by": {
                "type": "STRING",
                "enum": ["relevance", "date", "citations"],
                "description": "排序方式：'relevance'（默认，相关性优先）、'date'（最新时间优先）、'citations'（被引数优先）",
            },
            "sources": {
                "type": "ARRAY",
                "description": "可选数据源列表（默认 ['auto']）：支持 'auto'、'arxiv'、'openalex'、'crossref'、'dblp'、'semantic_scholar'",
                "items": {"type": "STRING"},
            },
            "max_results": {
                "type": "INTEGER",
                "description": f"返回论文数量（默认 {DEFAULT_PAPER_RESULTS}，范围 1~{MAX_PAPER_RESULTS}）",
            },
        },
        "required": ["query"],
    }
    read_only = True

    def __init__(self, service: AcademicResearchService | None = None) -> None:
        self._service = service or AcademicResearchService()

    async def run(self, parameter: dict[str, Any]) -> ToolResult:
        raw_query = parameter.get("query", "")
        if not isinstance(raw_query, str) or not raw_query.strip():
            return ToolResult(
                content="学术检索失败：query 不能为空，请提供英文 CS 检索词。",
                is_error=True,
            )

        raw_max = parameter.get("max_results", DEFAULT_PAPER_RESULTS)
        if isinstance(raw_max, bool) or not isinstance(raw_max, int):
            return ToolResult(
                content=f"学术检索失败：max_results 必须是 1 到 {MAX_PAPER_RESULTS} 之间的整数。",
                is_error=True,
            )
        max_results = max(1, min(raw_max, MAX_PAPER_RESULTS))

        year_from = parameter.get("year_from")
        if year_from is not None and (isinstance(year_from, bool) or not isinstance(year_from, int)):
            return ToolResult(content="学术检索失败：year_from 必须是整数年份。", is_error=True)

        year_to = parameter.get("year_to")
        if year_to is not None and (isinstance(year_to, bool) or not isinstance(year_to, int)):
            return ToolResult(content="学术检索失败：year_to 必须是整数年份。", is_error=True)

        if year_from is not None and year_to is not None and year_from > year_to:
            return ToolResult(
                content=f"学术检索失败：year_from（{year_from}）不能大于 year_to（{year_to}）。",
                is_error=True,
            )

        raw_sort = parameter.get("sort_by", "relevance")
        sort_by = (
            raw_sort.strip().lower()
            if isinstance(raw_sort, str) and raw_sort.strip()
            else "relevance"
        )
        if sort_by not in _VALID_SORT_MODES:
            return ToolResult(
                content=f"学术检索失败：不支持的 sort_by='{sort_by}'，仅支持 relevance / date / citations。",
                is_error=True,
            )

        raw_sources = parameter.get("sources")
        sources: list[str] | None = None
        if raw_sources is not None:
            if not isinstance(raw_sources, list):
                return ToolResult(content="学术检索失败：sources 必须是字符串列表。", is_error=True)
            sources = [str(s).strip().lower() for s in raw_sources if str(s).strip()]
            invalid = [s for s in sources if s not in _VALID_SOURCES]
            if invalid:
                return ToolResult(
                    content=f"学术检索失败：不支持的数据源 {', '.join(invalid)}。",
                    is_error=True,
                )

        raw_category = parameter.get("category", "cs")
        category = str(raw_category).strip() if isinstance(raw_category, str) else "cs"
        raw_venue = parameter.get("venue")
        venue = str(raw_venue).strip() if isinstance(raw_venue, str) and raw_venue.strip() else None

        response = await self._service.search_papers(
            raw_query.strip(),
            category=category,
            venue=venue,
            year_from=year_from,
            year_to=year_to,
            sort_by=sort_by,
            sources=sources,
            max_results=max_results,
        )

        # 仅当全部数据源均不可用时返回 is_error=True；部分降级仍返回有效结果，避免触发策略锁死
        if not response.active_sources and response.failures:
            reasons = "；".join(f"{f.source}: {f.reason}" for f in response.failures)
            return ToolResult(
                content=f"学术检索失败：所有请求的数据源均暂时不可用（{reasons}）。请稍后重试或检查网络连接。",
                is_error=True,
            )

        return ToolResult(
            content=format_paper_search_response(response),
            is_error=False,
        )


class InspectPaperTool(Tool):
    """单篇 CS 论文核验、引文图谱追踪与标准 BibTeX 生成工具。"""

    name = "inspect_paper"
    description = (
        "按 paper_id、arXiv ID、DOI、DBLP key 或精确论文标题，深度核验单篇论文的元数据、"
        "是否已在顶会/顶刊正式发表、真实被引次数，支持追踪参考文献（references）与后续施引论文（citations），"
        "并确定性生成可直接复制的标准 BibTeX（严禁手写编造引用）。"
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "OBJECT",
        "properties": {
            "paper_id": {
                "type": "STRING",
                "description": (
                    "论文标识符：支持 search_papers 返回的 paper_id（如 'arxiv:2310.06825'、'doi:10.1145/...'），"
                    "裸 arXiv ID/URL、裸 DOI 或精确英文论文标题"
                ),
            },
            "include": {
                "type": "ARRAY",
                "description": (
                    "返回内容组合（默认 ['metadata', 'bibtex']）："
                    "'metadata'（完整元数据与代码链接）、'bibtex'（标准 BibTeX）、"
                    "'references'（参考文献/向后溯源）、'citations'（施引论文/向前追踪）"
                ),
                "items": {"type": "STRING"},
            },
            "limit": {
                "type": "INTEGER",
                "description": f"references / citations 各自最多返回的条数（默认 {DEFAULT_CITATION_LIMIT}，范围 1~{MAX_CITATION_LIMIT}）",
            },
        },
        "required": ["paper_id"],
    }
    read_only = True

    def __init__(self, service: AcademicResearchService | None = None) -> None:
        self._service = service or AcademicResearchService()

    async def run(self, parameter: dict[str, Any]) -> ToolResult:
        raw_id = parameter.get("paper_id", "")
        if not isinstance(raw_id, str) or not raw_id.strip():
            return ToolResult(
                content="论文核验失败：paper_id 不能为空。",
                is_error=True,
            )

        raw_include = parameter.get("include")
        if raw_include is None:
            include = ["metadata", "bibtex"]
        elif isinstance(raw_include, list):
            include = [str(item).strip().lower() for item in raw_include if str(item).strip()]
            invalid = [item for item in include if item not in _VALID_INCLUDES]
            if invalid:
                return ToolResult(
                    content=(
                        f"论文核验失败：不支持的 include 项 {', '.join(invalid)}，"
                        "仅支持 metadata / references / citations / bibtex。"
                    ),
                    is_error=True,
                )
        else:
            return ToolResult(
                content="论文核验失败：include 必须是字符串列表。",
                is_error=True,
            )

        raw_limit = parameter.get("limit", DEFAULT_CITATION_LIMIT)
        if isinstance(raw_limit, bool) or not isinstance(raw_limit, int):
            return ToolResult(
                content=f"论文核验失败：limit 必须是 1 到 {MAX_CITATION_LIMIT} 之间的整数。",
                is_error=True,
            )
        limit = max(1, min(raw_limit, MAX_CITATION_LIMIT))

        inspection = await self._service.inspect_paper(
            raw_id.strip(),
            include=include,
            limit=limit,
        )
        if inspection is None:
            return ToolResult(
                content=(
                    f"未在 arXiv / OpenAlex / Crossref 中核验到与 `{raw_id.strip()}` 匹配的论文。\n"
                    "请检查 arXiv ID / DOI / OpenAlex ID 拼写是否正确，或先用 `search_papers` 模糊搜索确认真实标题后再核验；"
                    "切勿在未核验的情况下引用该文献。"
                ),
                is_error=False,
            )

        return ToolResult(
            content=format_paper_inspection(inspection),
            is_error=False,
        )


class ReadPaperFulltextTool(Tool):
    """CS 论文全文结构化精读工具（arXiv HTML 优先，PyMuPDF 解析 OA PDF 兜底，支持按节/关键词切片）。"""

    name = "read_paper_fulltext"
    description = (
        "读取开放获取 CS 论文的完整正文（优先解析结构清晰的 arXiv HTML，回退使用 PyMuPDF 解析 PDF 并缓存），"
        "避免仅靠摘要猜测论文方法、公式、超参数与实验数据。"
        "推荐两步法：先用 mode='outline' 低成本获取章节目录与代码链接，再用 mode='section'（如 'Method'/'Experiments'/'Limitations'）"
        "或 mode='query' 精准读取关键片段。"
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "OBJECT",
        "properties": {
            "paper_id": {
                "type": "STRING",
                "description": "论文标识（如 'arxiv:2310.06825'、DOI 或标题；与 url 至少提供一项）",
            },
            "url": {
                "type": "STRING",
                "description": "可选：公开可访问的论文 PDF 或 HTML 链接（http:// 或 https://）",
            },
            "mode": {
                "type": "STRING",
                "enum": ["outline", "section", "query", "pages"],
                "description": (
                    "阅读模式：'outline'（默认，返回章节目录与首节预览）、"
                    "'section'（按章节名或序号读取整节）、"
                    "'query'（在全文中按关键词检索最相关段落）、"
                    "'pages'（按 PDF 页码范围读取）"
                ),
            },
            "section_query": {
                "type": "STRING",
                "description": "mode='section' 时填章节名或序号（如 'Method'、'Experiments'、'3'）；mode='query' 时填要定位的关键词或指标名",
            },
            "page_range": {
                "type": "STRING",
                "description": "mode='pages' 时的页码范围（如 '1-3' 或 '2,5-6'）",
            },
            "max_chars": {
                "type": "INTEGER",
                "description": f"返回正文的最大字符数（默认 {DEFAULT_FULLTEXT_MAX_CHARS}，上限 {MAX_FULLTEXT_CHARS}）",
            },
        },
    }
    read_only = True

    def __init__(self, service: AcademicResearchService | None = None) -> None:
        self._service = service or AcademicResearchService()

    async def run(self, parameter: dict[str, Any]) -> ToolResult:
        raw_paper_id = parameter.get("paper_id", "")
        raw_url = parameter.get("url", "")
        paper_id = raw_paper_id.strip() if isinstance(raw_paper_id, str) else ""
        url = raw_url.strip() if isinstance(raw_url, str) else ""
        if not paper_id and not url:
            return ToolResult(
                content="论文全文读取失败：paper_id 与 url 至少需要提供一项。",
                is_error=True,
            )

        raw_mode = parameter.get("mode", "outline")
        mode_str = (
            raw_mode.strip().lower()
            if isinstance(raw_mode, str) and raw_mode.strip()
            else "outline"
        )
        if mode_str not in VALID_READ_MODES:
            return ToolResult(
                content=(
                    f"论文全文读取失败：不支持的 mode='{mode_str}'，"
                    "仅支持 'outline'、'section'、'query' 或 'pages'。"
                ),
                is_error=True,
            )
        mode = cast(FulltextReadMode, mode_str)

        raw_sq = parameter.get("section_query", "")
        section_query = raw_sq.strip() if isinstance(raw_sq, str) else ""
        if mode in {"section", "query"} and not section_query:
            return ToolResult(
                content=f"论文全文读取失败：mode='{mode}' 时必须提供 section_query 参数。",
                is_error=True,
            )

        raw_pages = parameter.get("page_range", "")
        page_range = raw_pages.strip() if isinstance(raw_pages, str) else ""

        raw_max_chars = parameter.get("max_chars", DEFAULT_FULLTEXT_MAX_CHARS)
        if isinstance(raw_max_chars, bool) or not isinstance(raw_max_chars, int):
            return ToolResult(
                content="论文全文读取失败：max_chars 必须是正整数。",
                is_error=True,
            )
        max_chars = max(1000, min(raw_max_chars, MAX_FULLTEXT_CHARS))

        try:
            content = await self._service.read_paper_fulltext(
                paper_id=paper_id,
                url=url,
                mode=mode,
                section_query=section_query,
                page_range=page_range,
                max_chars=max_chars,
            )
        except ValueError as exc:
            return ToolResult(content=str(exc), is_error=True)
        except httpx.TimeoutException:
            return ToolResult(
                content=f"论文全文下载超时：`{paper_id or url}`，请稍后重试。",
                is_error=True,
            )
        except (httpx.HTTPError, OSError) as exc:
            return ToolResult(
                content=f"论文全文网络请求失败：{exc}",
                is_error=True,
            )

        return ToolResult(content=content, is_error=False)
