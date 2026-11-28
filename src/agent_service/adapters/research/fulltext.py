"""CS 论文全文结构化阅读器：优先 arXiv HTML，回退 PyMuPDF 解析 OA PDF，支持章节与关键词切片。"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Literal

import httpx

from agent_service.adapters.research.models import (
    PaperSection,
    ParsedFulltext,
    extract_code_urls,
    normalize_whitespace,
)
from agent_service.adapters.research.rate_limit import TTLCache, build_research_headers
from agent_service.adapters.storage.pdf_parser import ParsedPage, parse_document_bytes
from agent_service.adapters.tools.fetch_web_page import validate_public_http_url

FulltextReadMode = Literal["outline", "section", "query", "pages"]
VALID_READ_MODES: tuple[FulltextReadMode, ...] = ("outline", "section", "query", "pages")

MAX_PAPER_PDF_BYTES = 20 * 1024 * 1024  # 20 MB（覆盖绝大多数 CS 顶会含图论文）
MAX_PAPER_HTML_BYTES = 8 * 1024 * 1024  # 8 MB
DEFAULT_FULLTEXT_TIMEOUT = 30.0
DEFAULT_FULLTEXT_MAX_CHARS = 10_000
MAX_FULLTEXT_CHARS = 25_000

_SKIP_HTML_TAGS = frozenset(
    {
        "script",
        "style",
        "noscript",
        "nav",
        "footer",
        "aside",
        "svg",
        "canvas",
        "button",
        "form",
        "iframe",
        "annotation",  # 避免 MathML annotation 重复输出 LaTeX/XML 噪声
    }
)
_HEADING_TAGS = frozenset({"h1", "h2", "h3", "h4"})
_BLOCK_TAGS = frozenset(
    {"p", "div", "section", "article", "li", "tr", "blockquote", "pre", "figcaption"}
)

# 匹配常见 CS 学术论文章节标题行（如 "1 Introduction", "3.1 Model Architecture", "Abstract", "References"）
_PDF_HEADING_RE = re.compile(
    r"^(?:"
    r"(?:abstract|introduction|background|related work|preliminaries|"
    r"methodology|method|methods|approach|architecture|system design|"
    r"implementation|evaluation|experiments?|experimental setup|results?|"
    r"ablation(?: study)?|discussion|limitations?|broader impact|ethics(?: statement)?|"
    r"conclusion|conclusions|future work|acknowledg(?:e)?ments?|references|appendix)"
    r"|(?:[0-9]{1,2}(?:\.[0-9]{1,2}){0,2}\.?|[IVX]{1,4}\.|[A-Z]\.)\s+[A-Z][A-Za-z0-9 ,:/&()\-]{2,80}"
    r")$",
    re.IGNORECASE,
)
_WORD_TOKEN_RE = re.compile(r"[a-z0-9]{2,}|[\u4e00-\u9fff]{2,}")
_MULTI_NEWLINES_RE = re.compile(r"\n{3,}")


class _AcademicHTMLSectionParser(HTMLParser):
    """解析 arXiv HTML / 学术网页，按标题标签切分为结构化章节列表。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.doc_title = ""
        self.sections: list[tuple[str, str]] = []

        self._skip_depth = 0
        self._in_title = False
        self._title_chunks: list[str] = []

        self._in_heading = False
        self._heading_chunks: list[str] = []

        self._current_heading = "Header & Abstract"
        self._current_chunks: list[str] = []

    def _flush_section(self) -> None:
        raw = "".join(self._current_chunks)
        lines = [normalize_whitespace(line) for line in raw.splitlines()]
        body = "\n".join(line for line in lines if line)
        body = _MULTI_NEWLINES_RE.sub("\n\n", body).strip()
        if body:
            self.sections.append((self._current_heading, body))
        self._current_chunks = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if self._skip_depth > 0 or tag in _SKIP_HTML_TAGS:
            self._skip_depth += 1
            return
        if tag == "title":
            self._in_title = True
            self._title_chunks = []
            return
        if tag in _HEADING_TAGS:
            self._flush_section()
            self._in_heading = True
            self._heading_chunks = []
            return
        if tag in _BLOCK_TAGS or tag == "br":
            self._current_chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if self._skip_depth > 0:
            self._skip_depth -= 1
            return
        if tag == "title":
            self._in_title = False
            if not self.doc_title:
                self.doc_title = normalize_whitespace("".join(self._title_chunks))
            return
        if tag in _HEADING_TAGS and self._in_heading:
            self._in_heading = False
            heading = normalize_whitespace("".join(self._heading_chunks))
            if heading:
                if tag == "h1" and not self.doc_title:
                    self.doc_title = heading
                self._current_heading = heading
            self._heading_chunks = []
            return
        if tag in _BLOCK_TAGS:
            self._current_chunks.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth > 0:
            return
        if self._in_title:
            self._title_chunks.append(data)
            return
        if self._in_heading:
            self._heading_chunks.append(data)
            return
        self._current_chunks.append(data)

    def close(self) -> None:
        super().close()
        self._flush_section()


def parse_html_into_sections(
    html_text: str,
    *,
    paper_id: str,
    source_url: str,
    fallback_title: str = "",
    source_format: str = "arxiv_html",
) -> ParsedFulltext:
    """将 arXiv HTML 或学术 HTML 页面解析为按章节索引的 ParsedFulltext。"""
    parser = _AcademicHTMLSectionParser()
    parser.feed(html_text)
    parser.close()

    sections: list[PaperSection] = []
    full_chunks: list[str] = []
    for idx, (heading, body) in enumerate(parser.sections, start=1):
        sections.append(PaperSection(index=idx, heading=heading, content=body))
        full_chunks.append(body)

    full_text = "\n\n".join(full_chunks)
    title = fallback_title or parser.doc_title or paper_id
    return ParsedFulltext(
        paper_id=paper_id,
        title=title,
        source_url=source_url,
        source_format=source_format,
        total_chars=len(full_text),
        page_count=None,
        code_urls=extract_code_urls(full_text[:15_000]),
        sections=tuple(sections),
        pages=(),
    )


def _is_likely_pdf_heading(line: str) -> bool:
    stripped = normalize_whitespace(line)
    if not stripped or len(stripped) < 3 or len(stripped) > 95:
        return False
    # 排除句子结尾标点或纯数字页码（允许 "1. Introduction" 等短编号标题）
    if stripped.endswith((".", ",", ";", "?", "!")) and (
        len(stripped) > 40 or not re.match(r"^\d+\.?\s+[A-Z]", stripped)
    ):
        return False
    if stripped.isdigit():
        return False
    return bool(_PDF_HEADING_RE.match(stripped))


def parse_pdf_pages_into_sections(
    pages: list[ParsedPage],
    *,
    paper_id: str,
    source_url: str,
    fallback_title: str = "",
) -> ParsedFulltext:
    """将 PyMuPDF 按页提取结果识别为带页码区间的学术章节结构。"""
    sections: list[PaperSection] = []
    current_heading = "Header & Abstract"
    current_lines: list[str] = []
    start_page = pages[0].page_number if pages else 1
    end_page = start_page

    def flush() -> None:
        body = _MULTI_NEWLINES_RE.sub("\n\n", "\n".join(current_lines)).strip()
        if body:
            sections.append(
                PaperSection(
                    index=len(sections) + 1,
                    heading=current_heading,
                    content=body,
                    start_page=start_page,
                    end_page=end_page,
                )
            )

    for page in pages:
        for raw_line in page.text.splitlines():
            line = raw_line.strip()
            if not line:
                current_lines.append("")
                continue
            if _is_likely_pdf_heading(line):
                flush()
                current_heading = normalize_whitespace(line)
                current_lines = []
                start_page = page.page_number
                end_page = page.page_number
            else:
                current_lines.append(line)
                end_page = page.page_number
    flush()

    # 若未能识别出多章节（例如短文或特殊排版），按每 2 页合并为结构块方便定位
    if len(sections) <= 1 and len(pages) > 2:
        sections = []
        for i in range(0, len(pages), 2):
            group = pages[i : i + 2]
            p_start = group[0].page_number
            p_end = group[-1].page_number
            label = f"Page {p_start}" if p_start == p_end else f"Pages {p_start}-{p_end}"
            body = "\n\n".join(p.text for p in group).strip()
            sections.append(
                PaperSection(
                    index=len(sections) + 1,
                    heading=label,
                    content=body,
                    start_page=p_start,
                    end_page=p_end,
                )
            )

    page_tuples = tuple((p.page_number, p.text) for p in pages)
    first_pages_text = "\n".join(p.text for p in pages[:3])
    total_chars = sum(len(p.text) for p in pages)
    inferred_title = fallback_title
    if not inferred_title and pages:
        first_line = next((ln.strip() for ln in pages[0].text.splitlines() if ln.strip()), "")
        inferred_title = first_line[:160] or paper_id

    return ParsedFulltext(
        paper_id=paper_id,
        title=inferred_title,
        source_url=source_url,
        source_format="pdf",
        total_chars=total_chars,
        page_count=len(pages),
        code_urls=extract_code_urls(first_pages_text),
        sections=tuple(sections),
        pages=page_tuples,
    )


def format_fulltext_outline(doc: ParsedFulltext) -> str:
    """格式化论文全文目录与结构概览，便于 Agent 低成本决定精读哪些章节。"""
    lines = [
        f"### 📄 论文全文结构概览：{doc.title}",
        f"- **Paper ID**：`{doc.paper_id}` | **解析格式**：`{doc.source_format}` | **全文约**：{doc.total_chars:,} 字符"
        + (f"（共 {doc.page_count} 页）" if doc.page_count else ""),
        f"- **来源 URL**：`{doc.source_url}`",
    ]
    if doc.code_urls:
        lines.append(f"- **提取到的代码仓库**：{', '.join(f'`{u}`' for u in doc.code_urls[:4])}")

    lines.append("")
    lines.append("#### 章节目录（Sections）")
    if not doc.sections:
        lines.append("（未能提取到有效章节文本）")
    else:
        for sec in doc.sections:
            page_info = ""
            if sec.start_page is not None:
                page_info = (
                    f" · p.{sec.start_page}"
                    if sec.end_page in (None, sec.start_page)
                    else f" · pp.{sec.start_page}-{sec.end_page}"
                )
            lines.append(
                f"{sec.index}. **{sec.heading}**（约 {len(sec.content):,} 字符{page_info}）"
            )

    # 附带首节预览（通常含 Abstract），减少一次额外调用
    if doc.sections:
        first_preview = doc.sections[0].content[:1200]
        if len(doc.sections[0].content) > 1200:
            first_preview += "..."
        lines.extend(["", f"#### 首节预览（{doc.sections[0].heading}）", first_preview])

    lines.extend(
        [
            "",
            (
                "> 提示：请用 `read_paper_fulltext(paper_id=..., mode='section', section_query='Method')` "
                "读取特定章节，或 `mode='query'` 按关键词精确定位实验细节/公式/局限性。"
            ),
        ]
    )
    return "\n".join(lines)


def extract_fulltext_section(
    doc: ParsedFulltext,
    section_query: str,
    *,
    max_chars: int = DEFAULT_FULLTEXT_MAX_CHARS,
) -> str:
    """按章节名/编号模糊匹配并提取正文内容。"""
    needle = normalize_whitespace(section_query).lower()
    if not needle:
        return format_fulltext_outline(doc)

    # 支持直接传章节序号（如 "3"）
    matched: list[PaperSection] = []
    if needle.isdigit():
        idx = int(needle)
        matched = [sec for sec in doc.sections if sec.index == idx]

    if not matched:
        tokens = [t for t in _WORD_TOKEN_RE.findall(needle) if len(t) >= 2]
        scored: list[tuple[int, PaperSection]] = []
        for sec in doc.sections:
            hl = sec.heading.lower()
            score = 0
            if needle == hl:
                score += 100
            elif needle in hl:
                score += 50
            for tok in tokens:
                if tok in hl:
                    score += 15
            if score > 0:
                scored.append((score, sec))
        scored.sort(key=lambda pair: (-pair[0], pair[1].index))
        matched = [sec for _, sec in scored[:4]]

    if not matched:
        available = "、".join(f"`{s.index}. {s.heading}`" for s in doc.sections[:15])
        return (
            f"在论文 `{doc.paper_id}` 中未找到匹配 `{section_query}` 的章节。\n"
            f"可用章节：{available or '无'}\n"
            "建议改用 `mode='query'` 在全文段落中检索关键词。"
        )

    header = [
        f"### 📖 论文章节精读：{doc.title}",
        f"- **Paper ID**：`{doc.paper_id}` | **匹配章节**：{', '.join(s.heading for s in matched)}",
        f"- **来源 URL**：`{doc.source_url}`",
        "",
    ]
    body_blocks: list[str] = []
    remaining = max_chars
    truncated = False
    for sec in matched:
        page_tag = ""
        if sec.start_page is not None:
            page_tag = (
                f"（第 {sec.start_page} 页）"
                if sec.end_page in (None, sec.start_page)
                else f"（第 {sec.start_page}-{sec.end_page} 页）"
            )
        block = f"#### [{sec.index}] {sec.heading}{page_tag}\n\n{sec.content}"
        if len(block) > remaining:
            body_blocks.append(block[:remaining])
            truncated = True
            break
        body_blocks.append(block)
        remaining -= len(block) + 2

    out = "\n".join(header) + "\n\n".join(body_blocks)
    if truncated:
        out += f"\n\n...[已按 max_chars={max_chars} 截断，可缩小 section_query 或换用 mode='query' 精准定位]"
    return out


def search_in_fulltext(
    doc: ParsedFulltext,
    query: str,
    *,
    max_chars: int = DEFAULT_FULLTEXT_MAX_CHARS,
) -> str:
    """在论文全文中按关键词密度与短语匹配召回最相关段落（轻量论文内检索）。"""
    clean_q = normalize_whitespace(query)
    if not clean_q:
        return format_fulltext_outline(doc)
    lowered_q = clean_q.lower()
    tokens = list(dict.fromkeys(_WORD_TOKEN_RE.findall(lowered_q)))
    if not tokens:
        tokens = [lowered_q]

    passages: list[tuple[float, PaperSection, str]] = []
    for sec in doc.sections:
        # 跳过纯 References 避免参考文献标题污染实验/方法检索
        is_ref_section = sec.heading.strip().lower().startswith("reference")
        raw_paras = [p.strip() for p in re.split(r"\n{2,}", sec.content) if len(p.strip()) >= 30]
        if not raw_paras and sec.content.strip():
            raw_paras = [sec.content.strip()]
        for para in raw_paras:
            pl = para.lower()
            score = 0.0
            if lowered_q in pl:
                score += 12.0
            matched_tokens = 0
            for tok in tokens:
                cnt = pl.count(tok)
                if cnt > 0:
                    matched_tokens += 1
                    score += min(cnt, 4) * 2.0
            if matched_tokens == 0 and score == 0.0:
                continue
            score += (matched_tokens / len(tokens)) * 6.0
            if is_ref_section:
                score *= 0.35
            passages.append((score, sec, para))

    passages.sort(key=lambda item: -item[0])
    if not passages:
        return (
            f"在论文 `{doc.paper_id}` 全文中未检索到包含 `{clean_q}` 的段落。"
            "请换用更通用的英文术语，或先用 `mode='outline'` 查看章节目录。"
        )

    lines = [
        f"### 🔎 论文全文片段定位：`{clean_q}`",
        f"- **论文**：{doc.title}（`{doc.paper_id}`）",
        f"- **命中段落数**：{len(passages)}（展示最相关片段）",
        "",
    ]
    used_chars = 0
    for shown, (_, sec, para) in enumerate(passages[:12]):
        page_tag = f" · p.{sec.start_page}" if sec.start_page is not None else ""
        snippet = para[:2400] + ("..." if len(para) > 2400 else "")
        block = f"**[片段 {shown + 1} · {sec.heading}{page_tag}]**\n{snippet}\n"
        if used_chars + len(block) > max_chars and shown > 0:
            break
        lines.append(block)
        used_chars += len(block)

    return "\n".join(lines).strip()


def _parse_page_range(page_range: str, total_pages: int) -> list[int]:
    pages: list[int] = []
    for part in page_range.split(","):
        token = part.strip()
        if not token:
            continue
        if "-" in token:
            left, right = token.split("-", 1)
            if left.strip().isdigit() and right.strip().isdigit():
                start = max(1, int(left.strip()))
                end = min(total_pages, int(right.strip()))
                if start <= end:
                    pages.extend(range(start, end + 1))
        elif token.isdigit():
            num = int(token)
            if 1 <= num <= total_pages:
                pages.append(num)
    return list(dict.fromkeys(pages))


def extract_fulltext_pages(
    doc: ParsedFulltext,
    page_range: str,
    *,
    max_chars: int = DEFAULT_FULLTEXT_MAX_CHARS,
) -> str:
    """按页码范围提取 PDF 全文内容。"""
    if not doc.pages:
        return (
            f"论文 `{doc.paper_id}` 当前由 `{doc.source_format}`（无固定页码）解析，"
            "不支持 `mode='pages'`；请改用 `mode='outline'`、`mode='section'` 或 `mode='query'`。"
        )
    target_nums = _parse_page_range(page_range or "1-2", len(doc.pages))
    if not target_nums:
        return f"无效的 page_range='{page_range}'，该 PDF 共 {len(doc.pages)} 页（示例：'1-3' 或 '2,5'）。"

    page_map = dict(doc.pages)
    lines = [
        f"### 📄 论文按页阅读：{doc.title}",
        f"- **Paper ID**：`{doc.paper_id}` | **页码**：{', '.join(str(n) for n in target_nums)} / 共 {len(doc.pages)} 页",
        "",
    ]
    used = 0
    truncated = False
    for num in target_nums:
        text = page_map.get(num, "")
        block = f"#### 第 {num} 页\n\n{text}\n"
        if used + len(block) > max_chars:
            lines.append(block[: max(0, max_chars - used)])
            truncated = True
            break
        lines.append(block)
        used += len(block)
    out = "\n".join(lines).strip()
    if truncated:
        out += f"\n\n...[已按 max_chars={max_chars} 截断]"
    return out


class PaperFulltextReader:
    """下载、解析并缓存开放获取 CS 论文全文（优先 arXiv HTML，回退 PyMuPDF PDF）。"""

    def __init__(
        self,
        *,
        timeout: float = DEFAULT_FULLTEXT_TIMEOUT,
        transport: httpx.AsyncBaseTransport | None = None,
        cache: TTLCache[ParsedFulltext] | None = None,
    ) -> None:
        self._timeout = timeout
        self._transport = transport
        self._cache = cache or TTLCache[ParsedFulltext](max_size=24, ttl_seconds=1800.0)

    async def _download_bytes(
        self,
        url: str,
        *,
        max_bytes: int,
        accept: str,
    ) -> tuple[str, str, bytes]:
        safe_url = validate_public_http_url(url)
        headers = build_research_headers(extra={"Accept": accept})
        async with httpx.AsyncClient(
            timeout=self._timeout,
            follow_redirects=True,
            transport=self._transport,
        ) as client:
            response = await client.get(safe_url, headers=headers)
            response.raise_for_status()
            final_url = str(response.url)
            validate_public_http_url(final_url)

            content_len = response.headers.get("content-length")
            if content_len and content_len.isdigit() and int(content_len) > max_bytes:
                raise ValueError(f"论文文件体积超过上限（>{max_bytes // (1024 * 1024)} MB）。")
            raw_bytes = response.content
            if len(raw_bytes) > max_bytes:
                raise ValueError(f"论文文件体积超过上限（>{max_bytes // (1024 * 1024)} MB）。")
            content_type = response.headers.get("content-type", "")
            return final_url, content_type, raw_bytes

    async def load(
        self,
        *,
        paper_id: str,
        title: str = "",
        html_url: str = "",
        pdf_url: str = "",
        prefer_pdf: bool = False,
    ) -> ParsedFulltext:
        """加载并解析论文全文，命中内存缓存时直接返回。"""
        cache_key = f"{paper_id}|{'pdf' if prefer_pdf else 'auto'}|{html_url}|{pdf_url}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        errors: list[str] = []

        # 1. 优先尝试 arXiv HTML（除非显式需要按 PDF 页码读取）
        if html_url and not prefer_pdf:
            try:
                final_url, ctype, raw_bytes = await self._download_bytes(
                    html_url,
                    max_bytes=MAX_PAPER_HTML_BYTES,
                    accept="text/html,application/xhtml+xml;q=0.9,*/*;q=0.5",
                )
                if "html" in ctype.lower() or raw_bytes.lstrip().startswith((b"<!", b"<html", b"<HTML")):
                    html_text = raw_bytes.decode("utf-8", errors="replace")
                    parsed = parse_html_into_sections(
                        html_text,
                        paper_id=paper_id,
                        source_url=final_url,
                        fallback_title=title,
                        source_format="arxiv_html" if "arxiv.org/html" in final_url else "html",
                    )
                    if parsed.total_chars >= 400:
                        self._cache.set(cache_key, parsed)
                        return parsed
                    errors.append("HTML 页面正文过短，已回退 PDF")
            except Exception as exc:  # noqa: BLE001 - HTML 失败自动回退 PDF
                errors.append(f"HTML 抓取失败（{exc}）")

        # 2. 回退下载并用 PyMuPDF 解析 PDF（或非 PDF 公开页面）
        if pdf_url:
            try:
                final_url, ctype, raw_bytes = await self._download_bytes(
                    pdf_url,
                    max_bytes=MAX_PAPER_PDF_BYTES,
                    accept="application/pdf,text/html;q=0.8,*/*;q=0.5",
                )
                is_pdf = (
                    "pdf" in ctype.lower()
                    or final_url.lower().endswith(".pdf")
                    or raw_bytes[:5] == b"%PDF-"
                )
                if is_pdf:
                    pages = await parse_document_bytes(
                        f"{paper_id}.pdf",
                        raw_bytes,
                        ocr_page=None,
                    )
                    parsed = parse_pdf_pages_into_sections(
                        pages,
                        paper_id=paper_id,
                        source_url=final_url,
                        fallback_title=title,
                    )
                    self._cache.set(cache_key, parsed)
                    return parsed

                # 部分出版商链接重定向到了 HTML 全文页
                if "html" in ctype.lower() or raw_bytes.lstrip().startswith((b"<!", b"<html", b"<HTML")):
                    parsed = parse_html_into_sections(
                        raw_bytes.decode("utf-8", errors="replace"),
                        paper_id=paper_id,
                        source_url=final_url,
                        fallback_title=title,
                        source_format="html",
                    )
                    if parsed.total_chars >= 200:
                        self._cache.set(cache_key, parsed)
                        return parsed
                errors.append(f"链接返回非 PDF/HTML 内容（{ctype or 'unknown'}）")
            except Exception as exc:  # noqa: BLE001
                errors.append(f"PDF 解析失败（{exc}）")

        detail = "；".join(errors) if errors else "未找到公开可访问的 HTML 或 PDF 全文链接"
        raise ValueError(f"无法读取论文 `{paper_id}` 全文：{detail}")
