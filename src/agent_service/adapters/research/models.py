"""学术论文元数据、引文、全文结构化模型及跨源去重/BibTeX 工具函数。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_WHITESPACE_RE = re.compile(r"\s+")
_TITLE_CLEAN_RE = re.compile(r"[^a-z0-9]+")
_ARXIV_NEW_ID_RE = re.compile(r"(?:arxiv:)?(\d{4}\.\d{4,5})(v\d+)?", re.IGNORECASE)
_ARXIV_OLD_ID_RE = re.compile(
    r"(?:arxiv:)?([a-z-]+(?:\.[a-z-]+)?/\d{7})(v\d+)?", re.IGNORECASE
)
_DOI_RE = re.compile(r"(10\.\d{4,9}/[^\s\"'<>]+)", re.IGNORECASE)
_CODE_URL_RE = re.compile(
    r"https?://(?:www\.)?(?:github\.com|gitlab\.com)/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:/[^\s)\]>\"',;]*)?",
    re.IGNORECASE,
)
_PREPRINT_VENUES = frozenset({"", "corr", "arxiv", "arxiv preprint"})
_STOP_WORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "on",
        "in",
        "of",
        "for",
        "to",
        "with",
        "and",
        "or",
        "by",
        "from",
        "via",
        "towards",
        "toward",
        "using",
    }
)


def normalize_whitespace(text: str) -> str:
    """将连续空白字符规整为单个空格并去除首尾空白。"""
    return _WHITESPACE_RE.sub(" ", text).strip()


def normalize_title(title: str) -> str:
    """将论文标题规范化为小写字母数字串，用于跨源去重匹配。"""
    return _TITLE_CLEAN_RE.sub("", title.lower())


def normalize_doi(raw: str) -> str:
    """将 DOI URL 或前缀形式统一清洗为标准小写 DOI（如 10.1145/...）。"""
    text = raw.strip()
    if not text:
        return ""
    for prefix in (
        "https://doi.org/",
        "http://doi.org/",
        "https://dx.doi.org/",
        "http://dx.doi.org/",
        "doi:",
    ):
        if text.lower().startswith(prefix):
            text = text[len(prefix) :].strip()
            break
    match = _DOI_RE.search(text)
    if not match:
        return ""
    return match.group(1).rstrip(".,;)").lower()


def normalize_arxiv_id(raw: str, *, keep_version: bool = False) -> str:
    """从原始字符串、arXiv URL 或前缀中提取规范 arXiv ID（默认去掉 vN 版本后缀）。"""
    text = raw.strip()
    if not text:
        return ""
    cleaned = re.sub(r"\.pdf$", "", text, flags=re.IGNORECASE)
    for pattern in (_ARXIV_NEW_ID_RE, _ARXIV_OLD_ID_RE):
        match = pattern.search(cleaned)
        if match:
            base = match.group(1)
            version = match.group(2) or ""
            return f"{base}{version}" if keep_version else base
    return ""


def extract_code_urls(text: str) -> tuple[str, ...]:
    """从摘要、注释或正文中提取去重后的 GitHub/GitLab 代码仓库链接。"""
    if not text:
        return ()
    seen: set[str] = set()
    urls: list[str] = []
    for match in _CODE_URL_RE.finditer(text):
        url = match.group(0).rstrip(".,;:!?)]}\"'/")
        # 去掉常见非仓库尾缀
        lowered = url.lower()
        if lowered.endswith(".git"):
            url = url[:-4].rstrip("/")
            lowered = url.lower()
        if lowered not in seen:
            seen.add(lowered)
            urls.append(url)
    return tuple(urls)


def parse_paper_identifier(raw: str) -> tuple[str, str]:
    """识别输入标识符类型，返回 (kind, normalized_value)。

    支持类型：'arxiv' | 'doi' | 'dblp' | 's2' | 'title'。
    """
    text = raw.strip()
    if not text:
        return "title", ""

    lowered = text.lower()
    if lowered.startswith("arxiv:") or "arxiv.org/" in lowered or "ar5iv" in lowered:
        arxiv_id = normalize_arxiv_id(text)
        if arxiv_id:
            return "arxiv", arxiv_id

    if lowered.startswith("dblp:"):
        return "dblp", text[5:].strip()
    if "dblp.org/rec/" in lowered:
        rec = text.split("dblp.org/rec/", 1)[1].split("?", 1)[0].split("#", 1)[0]
        rec = re.sub(r"\.(html|bib|xml)$", "", rec, flags=re.IGNORECASE)
        if rec:
            return "dblp", rec.strip("/")

    if lowered.startswith("s2:"):
        return "s2", text[3:].strip()

    if lowered.startswith("openalex:"):
        return "openalex", text[9:].strip().upper()
    if "openalex.org/" in lowered:
        tail = text.rstrip("/").split("/")[-1].split("?", 1)[0]
        if re.fullmatch(r"W\d+", tail, re.IGNORECASE):
            return "openalex", tail.upper()
    if re.fullmatch(r"W\d{6,}", text):
        return "openalex", text

    if lowered.startswith("doi:") or "doi.org/" in lowered or text.startswith("10."):
        doi = normalize_doi(text)
        if doi:
            # DataCite arXiv DOI 可同时识别为 arXiv 论文
            if doi.startswith("10.48550/arxiv."):
                arxiv_id = normalize_arxiv_id(doi.split("10.48550/arxiv.", 1)[1])
                if arxiv_id:
                    return "arxiv", arxiv_id
            return "doi", doi

    # 纯裸 arXiv ID（如 2310.06825 或 2310.06825v2）
    if re.fullmatch(r"\d{4}\.\d{4,5}(?:v\d+)?", text, re.IGNORECASE):
        return "arxiv", normalize_arxiv_id(text)

    # 40 位十六进制 Semantic Scholar paperId
    if re.fullmatch(r"[0-9a-f]{40}", lowered):
        return "s2", lowered

    return "title", normalize_whitespace(text)


def canonical_paper_id(
    *,
    arxiv_id: str = "",
    doi: str = "",
    dblp_key: str = "",
    openalex_id: str = "",
    s2_id: str = "",
    fallback_title: str = "",
) -> str:
    """按稳定性优先级生成统一 paper_id。"""
    if arxiv_id:
        return f"arxiv:{normalize_arxiv_id(arxiv_id)}"
    if doi:
        return f"doi:{normalize_doi(doi)}"
    if openalex_id:
        return f"openalex:{openalex_id.strip().upper()}"
    if dblp_key:
        return f"dblp:{dblp_key.strip()}"
    if s2_id:
        return f"s2:{s2_id.strip()}"
    return f"title:{normalize_title(fallback_title)[:40]}"


def is_preprint_venue(venue: str) -> bool:
    """判断期刊/会议名是否仅为预印本占位标识。"""
    cleaned = venue.strip().lower()
    return cleaned in _PREPRINT_VENUES or cleaned.startswith("arxiv")


@dataclass(frozen=True)
class PaperRecord:
    """跨源统一后的单篇论文元数据快照。"""

    paper_id: str
    title: str
    authors: tuple[str, ...] = ()
    year: int | None = None
    published_date: str = ""
    venue: str = ""
    publication_type: str = ""  # conference | journal | preprint | ""
    abstract: str = ""
    tldr: str = ""
    doi: str = ""
    arxiv_id: str = ""
    openalex_id: str = ""
    dblp_key: str = ""
    s2_id: str = ""
    url: str = ""
    pdf_url: str = ""
    html_url: str = ""
    citation_count: int | None = None
    influential_citation_count: int | None = None
    categories: tuple[str, ...] = ()
    code_urls: tuple[str, ...] = ()
    sources: tuple[str, ...] = ()
    comment: str = ""


@dataclass(frozen=True)
class CitationItem:
    """引文或参考文献精简条目。"""

    paper_id: str
    title: str
    authors: tuple[str, ...] = ()
    year: int | None = None
    venue: str = ""
    doi: str = ""
    arxiv_id: str = ""
    citation_count: int | None = None


@dataclass(frozen=True)
class SourceFailure:
    """单个上游数据源降级/失败记录。"""

    source: str
    reason: str


@dataclass
class PaperSearchResponse:
    """多源聚合文献检索结果。"""

    query: str
    papers: list[PaperRecord] = field(default_factory=list)
    active_sources: list[str] = field(default_factory=list)
    failures: list[SourceFailure] = field(default_factory=list)


@dataclass
class PaperInspection:
    """单篇论文详情、引文图谱与 BibTeX 透视结果。"""

    paper: PaperRecord
    bibtex: str = ""
    references: list[CitationItem] = field(default_factory=list)
    citations: list[CitationItem] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    failures: list[SourceFailure] = field(default_factory=list)


@dataclass(frozen=True)
class PaperSection:
    """论文全文解析后的单个逻辑章节。"""

    index: int
    heading: str
    content: str
    start_page: int | None = None
    end_page: int | None = None


@dataclass(frozen=True)
class ParsedFulltext:
    """已解析并可缓存复用的论文全文结构。"""

    paper_id: str
    title: str
    source_url: str
    source_format: str  # arxiv_html | pdf | html | text
    total_chars: int
    page_count: int | None
    code_urls: tuple[str, ...]
    sections: tuple[PaperSection, ...]
    pages: tuple[tuple[int, str], ...] = ()


def merge_paper_records(primary: PaperRecord, secondary: PaperRecord) -> PaperRecord:
    """将来自不同学术源的同一篇论文元数据无损合并。"""

    # 优先保留正式发表的会议/期刊名称，覆盖 CoRR / arXiv preprint 占位值
    if not is_preprint_venue(primary.venue):
        venue = primary.venue
        pub_type = primary.publication_type or secondary.publication_type
    elif not is_preprint_venue(secondary.venue):
        venue = secondary.venue
        pub_type = secondary.publication_type or primary.publication_type
    else:
        venue = primary.venue or secondary.venue
        pub_type = primary.publication_type or secondary.publication_type

    # 优先保留更完整的作者列表与摘要
    authors = (
        primary.authors
        if len(primary.authors) >= len(secondary.authors)
        else secondary.authors
    )
    abstract = (
        primary.abstract
        if len(primary.abstract) >= len(secondary.abstract)
        else secondary.abstract
    )
    tldr = primary.tldr or secondary.tldr
    doi = primary.doi or secondary.doi
    arxiv_id = primary.arxiv_id or secondary.arxiv_id
    openalex_id = primary.openalex_id or secondary.openalex_id
    dblp_key = primary.dblp_key or secondary.dblp_key
    s2_id = primary.s2_id or secondary.s2_id

    # 若正式发表年份存在，优先采用非预印本源或较早/明确的年份
    year = primary.year if primary.year is not None else secondary.year
    if (
        is_preprint_venue(primary.venue)
        and not is_preprint_venue(secondary.venue)
        and secondary.year is not None
    ):
        year = secondary.year

    published_date = primary.published_date or secondary.published_date

    # 被引数取两者中的非空较大值
    counts = [c for c in (primary.citation_count, secondary.citation_count) if c is not None]
    citation_count = max(counts) if counts else None

    inf_counts = [
        c
        for c in (
            primary.influential_citation_count,
            secondary.influential_citation_count,
        )
        if c is not None
    ]
    influential_count = max(inf_counts) if inf_counts else None

    categories = tuple(dict.fromkeys((*primary.categories, *secondary.categories)))
    code_urls = tuple(dict.fromkeys((*primary.code_urls, *secondary.code_urls)))
    sources = tuple(dict.fromkeys((*primary.sources, *secondary.sources)))

    title = primary.title if len(primary.title) >= len(secondary.title) else secondary.title
    url = primary.url or secondary.url
    pdf_url = primary.pdf_url or secondary.pdf_url
    html_url = primary.html_url or secondary.html_url
    comment = primary.comment or secondary.comment

    pid = canonical_paper_id(
        arxiv_id=arxiv_id,
        doi=doi,
        openalex_id=openalex_id,
        dblp_key=dblp_key,
        s2_id=s2_id,
        fallback_title=title,
    )
    return PaperRecord(
        paper_id=pid,
        title=title,
        authors=authors,
        year=year,
        published_date=published_date,
        venue=venue,
        publication_type=pub_type,
        abstract=abstract,
        tldr=tldr,
        doi=doi,
        arxiv_id=arxiv_id,
        openalex_id=openalex_id,
        dblp_key=dblp_key,
        s2_id=s2_id,
        url=url,
        pdf_url=pdf_url,
        html_url=html_url,
        citation_count=citation_count,
        influential_citation_count=influential_count,
        categories=categories,
        code_urls=code_urls,
        sources=sources,
        comment=comment,
    )


def _make_bibtex_key(paper: PaperRecord) -> str:
    author_part = "paper"
    if paper.authors:
        tokens = re.findall(r"[A-Za-z]+", paper.authors[0])
        if tokens:
            author_part = tokens[-1].lower()
    year_part = str(paper.year) if paper.year else "nd"
    title_words = [
        w.lower()
        for w in re.findall(r"[A-Za-z0-9]+", paper.title)
        if w.lower() not in _STOP_WORDS and len(w) > 1
    ]
    word_part = title_words[0] if title_words else "work"
    return f"{author_part}{year_part}{word_part}"


def _escape_bibtex(value: str) -> str:
    return normalize_whitespace(value).replace("{", "\\{").replace("}", "\\}")


def build_bibtex(paper: PaperRecord) -> str:
    """根据已核验的元数据确定性生成标准 BibTeX 引用条目。"""

    cite_key = _make_bibtex_key(paper)
    author_str = " and ".join(_escape_bibtex(a) for a in paper.authors) if paper.authors else "Unknown"
    title_str = _escape_bibtex(paper.title)

    has_formal_venue = bool(paper.venue and not is_preprint_venue(paper.venue))
    if has_formal_venue and paper.publication_type == "conference":
        entry_type = "inproceedings"
    elif has_formal_venue and paper.publication_type == "journal":
        entry_type = "article"
    elif has_formal_venue:
        entry_type = "inproceedings"
    else:
        entry_type = "misc"

    fields: list[tuple[str, str]] = [
        ("title", f"{{{title_str}}}"),
        ("author", author_str),
    ]
    if entry_type == "inproceedings":
        fields.append(("booktitle", _escape_bibtex(paper.venue)))
    elif entry_type == "article":
        fields.append(("journal", _escape_bibtex(paper.venue)))
    if paper.year is not None:
        fields.append(("year", str(paper.year)))
    if paper.arxiv_id:
        fields.append(("eprint", paper.arxiv_id))
        fields.append(("archivePrefix", "arXiv"))
        if paper.categories:
            fields.append(("primaryClass", paper.categories[0]))
    if paper.doi:
        fields.append(("doi", paper.doi))
    if paper.url:
        fields.append(("url", paper.url))

    lines = [f"@{entry_type}{{{cite_key},"]
    for idx, (k, v) in enumerate(fields):
        comma = "," if idx < len(fields) - 1 else ""
        lines.append(f"  {k} = {{{v}}}{comma}")
    lines.append("}")
    return "\n".join(lines)
