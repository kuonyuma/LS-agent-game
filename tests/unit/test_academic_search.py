"""面向 CS 的零配置科研搜索适配层与三大只读工具的离线单元测试。"""

from __future__ import annotations

import httpx
import pymupdf
import pytest

from agent_service.adapters.research.arxiv_client import (
    ArxivClient,
    build_arxiv_search_query,
    parse_arxiv_atom,
)
from agent_service.adapters.research.crossref_client import (
    CrossrefClient,
    OpenCitationsClient,
    SemanticScholarClient,
    parse_opencitations_dois,
)
from agent_service.adapters.research.dblp_client import DblpClient, parse_dblp_json
from agent_service.adapters.research.fulltext import PaperFulltextReader
from agent_service.adapters.research.models import (
    PaperRecord,
    build_bibtex,
    extract_code_urls,
    merge_paper_records,
    parse_paper_identifier,
)
from agent_service.adapters.research.openalex_client import (
    OpenAlexClient,
    parse_openalex_work,
    reconstruct_abstract,
)
from agent_service.adapters.research.rate_limit import AsyncRateLimiter
from agent_service.adapters.research.service import AcademicResearchService
from agent_service.adapters.tools.academic_search import (
    InspectPaperTool,
    ReadPaperFulltextTool,
    SearchPapersTool,
)
from agent_service.adapters.tools.index import create_tool_registry
from agent_service.adapters.web.runtime_manager import create_web_tool_registry

SAMPLE_ARXIV_XML = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/2310.06825v2</id>
    <published>2023-10-10T17:00:00Z</published>
    <title>FlashAttention-2: Faster Attention with Better Parallelism and Work Partitioning</title>
    <summary>We propose FlashAttention-2 with better work partitioning across warps. Code available at https://github.com/Dao-AILab/flash-attention.</summary>
    <author><name>Tri Dao</name></author>
    <arxiv:comment>Accepted at ICLR 2024. Official repo: https://github.com/Dao-AILab/flash-attention/</arxiv:comment>
    <arxiv:primary_category term="cs.LG"/>
    <category term="cs.LG"/>
    <category term="cs.AI"/>
    <link rel="alternate" type="text/html" href="https://arxiv.org/abs/2310.06825v2"/>
    <link title="pdf" rel="related" type="application/pdf" href="https://arxiv.org/pdf/2310.06825v2.pdf"/>
  </entry>
</feed>
"""

SAMPLE_OPENALEX_WORK = {
    "id": "https://openalex.org/W4384648639",
    "doi": "https://doi.org/10.48550/arxiv.2310.06825",
    "title": "FlashAttention-2: Faster Attention with Better Parallelism and Work Partitioning",
    "publication_year": 2024,
    "publication_date": "2024-05-07",
    "type": "conference-paper",
    "cited_by_count": 3771,
    "authorships": [{"author": {"display_name": "Tri Dao"}}],
    "primary_location": {
        "source": {"display_name": "International Conference on Learning Representations"},
        "landing_page_url": "https://openreview.net/forum?id=flash2",
        "pdf_url": "https://proceedings.iclr.cc/paper_files/paper/2024/file/flash2.pdf",
    },
    "open_access": {
        "oa_url": "https://proceedings.iclr.cc/paper_files/paper/2024/file/flash2.pdf"
    },
    "abstract_inverted_index": {
        "FlashAttention-2": [0],
        "improves": [1],
        "GPU": [2],
        "utilization.": [3],
    },
    "referenced_works": ["https://openalex.org/W4281728479"],
}

SAMPLE_OPENALEX_REF = {
    "id": "https://openalex.org/W4281728479",
    "doi": "https://doi.org/10.48550/arxiv.2205.14135",
    "title": "FlashAttention: Fast and Memory-Efficient Exact Attention with IO-Awareness",
    "publication_year": 2022,
    "cited_by_count": 5100,
    "primary_location": {"source": {"display_name": "NeurIPS"}},
    "authorships": [{"author": {"display_name": "Tri Dao"}}],
}

SAMPLE_OPENALEX_CITING = {
    "id": "https://openalex.org/W4400000001",
    "doi": "https://doi.org/10.48550/arxiv.2407.08608",
    "title": "FlashAttention-3: Fast and Accurate Attention with Asynchrony",
    "publication_year": 2024,
    "cited_by_count": 480,
    "primary_location": {"source": {"display_name": "NeurIPS"}},
    "authorships": [{"author": {"display_name": "Jay Shah"}}],
}

SAMPLE_DBLP_JSON = {
    "result": {
        "hits": {
            "hit": [
                {
                    "info": {
                        "title": "FlashAttention-2: Faster Attention with Better Parallelism and Work Partitioning.",
                        "authors": {"author": {"text": "Tri Dao 0001"}},
                        "venue": "ICLR",
                        "year": "2024",
                        "type": "Conference and Workshop Papers",
                        "key": "conf/iclr/Dao24",
                        "doi": "10.1234/iclr.2024.flash2",
                        "ee": ["https://doi.org/10.1234/iclr.2024.flash2"],
                        "url": "https://dblp.org/rec/conf/iclr/Dao24",
                    }
                },
                {
                    "info": {
                        "title": "Memory-Efficient Agent Planning via Hierarchical Graphs",
                        "authors": {
                            "author": [
                                {"text": "Alice Chen"},
                                {"text": "Bob Zhang 0002"},
                            ]
                        },
                        "venue": "CoRR",
                        "volume": "abs/2501.01234",
                        "year": "2025",
                        "type": "Informal and Other Publications",
                        "key": "journals/corr/abs-2501-01234",
                        "ee": "https://arxiv.org/abs/2501.01234",
                        "url": "https://dblp.org/rec/journals/corr/abs-2501-01234",
                    }
                },
            ]
        }
    }
}

SAMPLE_ARXIV_HTML = """<!doctype html>
<html>
<head><title>FlashAttention-2</title></head>
<body>
  <article>
    <h1>FlashAttention-2: Faster Attention with Better Parallelism</h1>
    <p>Abstract: Scaling Transformers to long sequences is bottlenecked by attention IO complexity. Official code at https://github.com/Dao-AILab/flash-attention.</p>
    <h2>1 Introduction</h2>
    <p>Standard attention materializes the quadratic attention matrix in HBM, causing memory bandwidth bottlenecks across modern GPUs.</p>
    <h2>3 Method: Work Partitioning</h2>
    <p>We parallelize across sequence length in addition to batch and head dimensions, and split Q across warps to avoid shared-memory communication between warps.</p>
    <h2>4 Experiments</h2>
    <p>On A100 GPUs, FlashAttention-2 achieves 2x speedup over FlashAttention-1 and reaches up to 73% of theoretical peak TFLOPs/s with sequence length 16k.</p>
    <h2>References</h2>
    <p>Dao et al., FlashAttention, NeurIPS 2022.</p>
  </article>
</body>
</html>
"""


def _make_sample_pdf() -> bytes:
    doc = pymupdf.open()
    try:
        p1 = doc.new_page()
        p1.insert_text(
            (50, 60),
            "FlashAttention-2: Faster Attention\n"
            "Abstract\n"
            "We improve attention GPU utilization. Code: https://github.com/Dao-AILab/flash-attention\n"
            "1 Introduction\n"
            "Attention scaling is limited by memory IO on modern accelerators.",
            fontsize=11,
        )
        p2 = doc.new_page()
        p2.insert_text(
            (50, 60),
            "3 Method\n"
            "We partition query blocks across warps to reduce shared memory sync overhead.\n"
            "4 Experiments\n"
            "Our kernel delivers 2.1x speedup on A100 at 16k sequence length.",
            fontsize=11,
        )
        return bytes(doc.tobytes())
    finally:
        doc.close()


def _make_service(
    handler,
    *,
    s2_api_key: str | None = "",
    openalex_api_key: str | None = "",
) -> AcademicResearchService:
    transport = httpx.MockTransport(handler)
    zero_limit = AsyncRateLimiter(0.0)
    return AcademicResearchService(
        arxiv=ArxivClient(transport=transport, rate_limiter=zero_limit),
        openalex=OpenAlexClient(
            api_key=openalex_api_key, transport=transport, rate_limiter=zero_limit
        ),
        dblp=DblpClient(transport=transport, rate_limiter=zero_limit),
        crossref=CrossrefClient(transport=transport, rate_limiter=zero_limit),
        opencitations=OpenCitationsClient(transport=transport, rate_limiter=zero_limit),
        semantic_scholar=SemanticScholarClient(
            api_key=s2_api_key, transport=transport, rate_limiter=zero_limit
        ),
        fulltext_reader=PaperFulltextReader(transport=transport),
    )


def test_identifiers_openalex_abstract_code_extraction_and_bibtex():
    assert parse_paper_identifier("2310.06825v2") == ("arxiv", "2310.06825")
    assert parse_paper_identifier("https://arxiv.org/pdf/2310.06825v1.pdf") == (
        "arxiv",
        "2310.06825",
    )
    assert parse_paper_identifier("https://doi.org/10.48550/arXiv.2310.06825") == (
        "arxiv",
        "2310.06825",
    )
    assert parse_paper_identifier("doi:10.1145/3589334.3645562.") == (
        "doi",
        "10.1145/3589334.3645562",
    )
    assert parse_paper_identifier("https://openalex.org/W4384648639") == (
        "openalex",
        "W4384648639",
    )
    assert parse_paper_identifier("W4384648639") == ("openalex", "W4384648639")
    assert parse_paper_identifier("https://dblp.org/rec/conf/iclr/Dao24.html") == (
        "dblp",
        "conf/iclr/Dao24",
    )
    assert parse_paper_identifier("Attention Is All You Need") == (
        "title",
        "Attention Is All You Need",
    )

    assert (
        reconstruct_abstract({"world.": [1], "Hello": [0]}) == "Hello world."
    )

    urls = extract_code_urls(
        "Repo: https://github.com/Dao-AILab/flash-attention.git, mirror: https://github.com/Dao-AILab/flash-attention/."
    )
    assert urls == ("https://github.com/Dao-AILab/flash-attention",)

    # arXiv + OpenAlex 合并后应保留 ICLR 正式会议名、被引数、OpenAlex ID、完整摘要与去重代码链接
    arxiv_item = parse_arxiv_atom(SAMPLE_ARXIV_XML)[0]
    oa_item, oa_refs = parse_openalex_work(SAMPLE_OPENALEX_WORK)
    assert oa_item is not None
    assert oa_refs == ["W4281728479"]
    assert oa_item.abstract == "FlashAttention-2 improves GPU utilization."

    merged = merge_paper_records(arxiv_item, oa_item)
    assert merged.venue == "International Conference on Learning Representations"
    assert merged.publication_type == "conference"
    assert merged.citation_count == 3771
    assert merged.openalex_id == "W4384648639"
    assert merged.arxiv_id == "2310.06825"
    assert merged.code_urls == ("https://github.com/Dao-AILab/flash-attention",)
    assert merged.sources == ("arXiv", "OpenAlex")

    # DBLP 解析兼容性
    dblp_items = parse_dblp_json(SAMPLE_DBLP_JSON)
    assert dblp_items[0].authors == ("Tri Dao",)
    assert dblp_items[1].arxiv_id == "2501.01234"
    assert dblp_items[1].venue == "arXiv preprint"

    bib = build_bibtex(merged)
    assert bib.startswith("@inproceedings{dao2024flashattention,")
    assert "booktitle = {International Conference on Learning Representations}" in bib
    assert "eprint = {2310.06825}" in bib

    preprint_bib = build_bibtex(
        PaperRecord(
            paper_id="arxiv:2501.01234",
            title="A Preprint Paper",
            authors=("Alice Chen",),
            year=2025,
            venue="arXiv preprint",
            publication_type="preprint",
            arxiv_id="2501.01234",
            categories=("cs.AI",),
        )
    )
    assert preprint_bib.startswith("@misc{chen2025preprint,")
    assert "archivePrefix = {arXiv}" in preprint_bib


def test_arxiv_query_builder_and_opencitations_parser():
    q_default = build_arxiv_search_query("agent memory")
    assert q_default == "(all:agent AND all:memory) AND cat:cs.*"

    q_systems = build_arxiv_search_query('"kv cache" scheduling', category="systems")
    assert 'all:"kv cache"' in q_systems
    assert "cat:cs.OS OR cat:cs.DC OR cat:cs.AR" in q_systems

    q_all = build_arxiv_search_query("quantum", category="all")
    assert "cat:" not in q_all

    dois = parse_opencitations_dois(
        [
            {"citing": "omid:br/1 doi:10.1234/a.1 openalex:W1"},
            {"citing": "omid:br/2 doi:10.1234/a.1"},
            {"citing": "omid:br/3 doi:10.1234/b.2"},
        ],
        field_name="citing",
        limit=5,
    )
    assert dois == ["10.1234/a.1", "10.1234/b.2"]


@pytest.mark.asyncio
async def test_search_papers_merges_arxiv_openalex_and_degrades_gracefully():
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "export.arxiv.org" in url:
            return httpx.Response(200, text=SAMPLE_ARXIV_XML)
        if "api.openalex.org/works" in url:
            return httpx.Response(200, json={"results": [SAMPLE_OPENALEX_WORK]})
        raise AssertionError(f"unexpected request: {request.url}")

    tool = SearchPapersTool(_make_service(handler))
    res = await tool.run({"query": "FlashAttention-2", "category": "ml"})
    assert not res.is_error
    assert "International Conference on Learning Representations（2024）" in res.content
    assert "被引：3771" in res.content
    assert "arxiv:2310.06825" in res.content
    assert "https://github.com/Dao-AILab/flash-attention" in res.content
    assert "arXiv + OpenAlex" in res.content
    assert res.content.count("FlashAttention-2: Faster Attention") == 1

    # 按顶会缩写（ICLR）筛选时自动通过 OpenAlex 别名匹配
    venue_res = await tool.run({"query": "FlashAttention", "venue": "ICLR"})
    assert not venue_res.is_error
    assert "FlashAttention-2" in venue_res.content

    # 单源（OpenAlex）触发 429 时，自动降级返回 arXiv 结果且 is_error=False（避免触发 TaskPolicy 锁死）
    def degraded_handler(request: httpx.Request) -> httpx.Response:
        if "export.arxiv.org" in str(request.url):
            return httpx.Response(200, text=SAMPLE_ARXIV_XML)
        if "api.openalex.org" in str(request.url):
            return httpx.Response(429)
        raise AssertionError(f"unexpected request: {request.url}")

    degraded_tool = SearchPapersTool(_make_service(degraded_handler))
    degraded_res = await degraded_tool.run({"query": "FlashAttention"})
    assert not degraded_res.is_error
    assert "部分数据源降级" in degraded_res.content
    assert "OpenAlex" in degraded_res.content
    assert "FlashAttention-2" in degraded_res.content

    # DBLP 显式请求遇到 Anubis 反爬质询（HTTP 202 HTML）时给出清晰可读提示而非原始 JSON 报错
    def anubis_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            202,
            text="<!doctype html><title>Making sure you're not a bot!</title>",
            headers={"content-type": "text/html; charset=utf-8"},
        )

    dblp_tool = SearchPapersTool(_make_service(anubis_handler))
    dblp_res = await dblp_tool.run({"query": "FlashAttention", "sources": ["dblp"]})
    assert dblp_res.is_error
    assert "反爬虫质询" in dblp_res.content


@pytest.mark.asyncio
async def test_inspect_paper_uses_openalex_free_singleton_and_citation_graph():
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "export.arxiv.org" in url:
            return httpx.Response(200, text=SAMPLE_ARXIV_XML)
        if "api.openalex.org/works/https://doi.org/10.48550/arxiv.2310.06825" in url:
            return httpx.Response(200, json=SAMPLE_OPENALEX_WORK)
        if "api.openalex.org/works" in url and "cites%3AW4384648639" in url:
            return httpx.Response(200, json={"results": [SAMPLE_OPENALEX_CITING]})
        if "api.openalex.org/works" in url and "openalex_id%3AW4281728479" in url:
            return httpx.Response(200, json={"results": [SAMPLE_OPENALEX_REF]})
        return httpx.Response(404)

    tool = InspectPaperTool(_make_service(handler))
    res = await tool.run(
        {
            "paper_id": "arxiv:2310.06825",
            "include": ["metadata", "references", "citations", "bibtex"],
            "limit": 5,
        }
    )
    assert not res.is_error
    assert "International Conference on Learning Representations（2024）" in res.content
    assert "3771" in res.content
    assert "W4384648639" in res.content
    assert "@inproceedings{dao2024flashattention," in res.content
    assert "FlashAttention: Fast and Memory-Efficient Exact Attention" in res.content
    assert "FlashAttention-3: Fast and Accurate Attention with Asynchrony" in res.content

    # 未核验到论文时返回明确提示且 is_error=False
    empty_tool = InspectPaperTool(_make_service(lambda _: httpx.Response(404)))
    missing = await empty_tool.run({"paper_id": "doi:10.9999/nonexistent"})
    assert not missing.is_error
    assert "未在 arXiv / OpenAlex / Crossref 中核验到" in missing.content


@pytest.mark.asyncio
async def test_inspect_paper_optional_semantic_scholar_enhancement():
    s2_called = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "api.semanticscholar.org" in url:
            assert request.headers.get("x-api-key") == "test-s2-key"
            s2_called["count"] += 1
            if url.endswith("/citations") or "/citations?" in url:
                return httpx.Response(
                    200,
                    json={
                        "data": [
                            {
                                "citingPaper": {
                                    "paperId": "a" * 40,
                                    "title": "Follow-up Kernel Paper",
                                    "year": 2025,
                                    "venue": "ICML",
                                    "citationCount": 42,
                                    "externalIds": {"ArXiv": "2502.00001"},
                                }
                            }
                        ]
                    },
                )
            return httpx.Response(
                200,
                json={
                    "paperId": "b" * 40,
                    "title": "FlashAttention-2: Faster Attention with Better Parallelism and Work Partitioning",
                    "year": 2024,
                    "venue": "ICLR",
                    "tldr": {"text": "Better warp partitioning doubles attention throughput."},
                    "citationCount": 1600,
                    "influentialCitationCount": 180,
                    "externalIds": {"ArXiv": "2310.06825"},
                },
            )
        if "export.arxiv.org" in url:
            return httpx.Response(200, text=SAMPLE_ARXIV_XML)
        return httpx.Response(404)

    tool = InspectPaperTool(_make_service(handler, s2_api_key="test-s2-key"))
    res = await tool.run(
        {"paper_id": "arxiv:2310.06825", "include": ["metadata", "citations"]}
    )
    assert not res.is_error
    assert s2_called["count"] == 2
    assert "Better warp partitioning doubles attention throughput." in res.content
    assert "高影响力引用 180 次" in res.content
    assert "Follow-up Kernel Paper" in res.content


@pytest.mark.asyncio
async def test_read_paper_fulltext_html_caching_and_pdf_fallback_and_ssrf():
    html_hits = {"count": 0}

    def html_handler(request: httpx.Request) -> httpx.Response:
        if "arxiv.org/html/2310.06825" in str(request.url):
            html_hits["count"] += 1
            return httpx.Response(
                200,
                text=SAMPLE_ARXIV_HTML,
                headers={"content-type": "text/html; charset=utf-8"},
            )
        return httpx.Response(404)

    tool = ReadPaperFulltextTool(_make_service(html_handler))
    outline = await tool.run({"paper_id": "arxiv:2310.06825", "mode": "outline"})
    assert not outline.is_error
    assert "3 Method: Work Partitioning" in outline.content
    assert "4 Experiments" in outline.content

    method = await tool.run(
        {"paper_id": "arxiv:2310.06825", "mode": "section", "section_query": "Method"}
    )
    assert not method.is_error
    assert "split Q across warps" in method.content

    queried = await tool.run(
        {"paper_id": "arxiv:2310.06825", "mode": "query", "section_query": "73% TFLOPs"}
    )
    assert not queried.is_error
    assert "73% of theoretical peak" in queried.content
    assert html_hits["count"] == 1

    # HTML 404 时自动回退到 PyMuPDF 解析 PDF，并支持按页码读取
    sample_pdf = _make_sample_pdf()

    def pdf_handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "arxiv.org/html/" in url:
            return httpx.Response(404)
        if "arxiv.org/pdf/2310.06825.pdf" in url:
            return httpx.Response(
                200,
                content=sample_pdf,
                headers={"content-type": "application/pdf"},
            )
        return httpx.Response(404)

    pdf_tool = ReadPaperFulltextTool(_make_service(pdf_handler))
    pdf_outline = await pdf_tool.run({"paper_id": "arxiv:2310.06825", "mode": "outline"})
    assert not pdf_outline.is_error
    assert "共 2 页" in pdf_outline.content
    assert "3 Method" in pdf_outline.content

    pdf_pages = await pdf_tool.run(
        {"paper_id": "arxiv:2310.06825", "mode": "pages", "page_range": "2"}
    )
    assert not pdf_pages.is_error
    assert "2.1x speedup" in pdf_pages.content

    # SSRF 内网地址必须被拒绝
    ssrf_res = await pdf_tool.run({"url": "http://127.0.0.1:8000/secret.pdf"})
    assert ssrf_res.is_error
    assert "安全策略禁止访问" in ssrf_res.content


def test_registry_isolation_and_shared_web_mode_visibility():
    reg1 = create_tool_registry()
    reg2 = create_tool_registry()
    for name in ("search_papers", "inspect_paper", "read_paper_fulltext"):
        assert name in reg1
        assert reg1[name].read_only is True
        assert reg1[name].requires_approval() is False
        assert reg1[name] is not reg2[name]

    # 共享工作台开放 3 个科研只读工具与公开网页读取，同时拦截本地文件工具。
    shared_registry = create_web_tool_registry(workspace=None)
    assert {"search_papers", "inspect_paper", "read_paper_fulltext"}.issubset(
        shared_registry.keys()
    )
    assert "fetch_web_page" in shared_registry
    assert "read_file" not in shared_registry
