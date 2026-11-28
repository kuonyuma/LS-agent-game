"""面向计算机科学（CS）的零配置学术检索适配层。

默认零 API Key 组合：arXiv + OpenAlex + Crossref + OpenCitations；
若环境可选配置 OPENALEX_API_KEY / SEMANTIC_SCHOLAR_API_KEY，则自动扩容与增强。
"""

from agent_service.adapters.research.models import (
    CitationItem,
    PaperInspection,
    PaperRecord,
    PaperSearchResponse,
    PaperSection,
    ParsedFulltext,
    SourceFailure,
    build_bibtex,
    extract_code_urls,
    merge_paper_records,
    normalize_arxiv_id,
    normalize_doi,
    normalize_title,
    parse_paper_identifier,
)
from agent_service.adapters.research.service import AcademicResearchService

__all__ = [
    "AcademicResearchService",
    "CitationItem",
    "PaperInspection",
    "PaperRecord",
    "PaperSearchResponse",
    "PaperSection",
    "ParsedFulltext",
    "SourceFailure",
    "build_bibtex",
    "extract_code_urls",
    "merge_paper_records",
    "normalize_arxiv_id",
    "normalize_doi",
    "normalize_title",
    "parse_paper_identifier",
]
