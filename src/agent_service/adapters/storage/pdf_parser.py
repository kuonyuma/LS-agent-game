"""PDF 与文本文档按页解析、扫描件视觉 OCR 兜底与段落感知滑动窗口切片。"""

from __future__ import annotations

import os
import re
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import pymupdf
from google.genai import types

from agent_service.adapters.gemini.client import close_genai_client, create_client
from agent_service.config.settings import resolve_api_key, settings

PageOcrCallback = Callable[[bytes, int], Awaitable[str]]

_SUPPORTED_TEXT_EXTENSIONS = {
    ".txt",
    ".md",
    ".markdown",
    ".csv",
    ".tsv",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".log",
    ".html",
    ".htm",
    ".xml",
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".sql",
}

_SENTENCE_BREAK_RE = re.compile(r"[。！？!?；;\n]")


@dataclass(frozen=True)
class ParsedPage:
    page_number: int
    text: str
    used_ocr: bool = False


@dataclass(frozen=True)
class DocumentChunk:
    page_number: int
    chunk_index: int
    content: str


def _clean_page_text(raw: str) -> str:
    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    # 合并多余连续空行，保留自然段落与表格行结构
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _meaningful_char_count(text: str) -> int:
    return len(re.sub(r"\s+", "", text))


def _decode_text_bytes(raw_bytes: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return raw_bytes.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("无法识别该文本文件的编码格式，请转换为 UTF-8 后重试。")


async def parse_document_bytes(
    filename: str,
    raw_bytes: bytes,
    *,
    ocr_page: PageOcrCallback | None = None,
    min_chars_for_native_pdf_page: int = 30,
    max_ocr_pages: int = 25,
) -> list[ParsedPage]:
    """将 PDF 或常见文本文件解析为带精确页码的页面列表；扫描页支持可选 OCR 兜底。"""

    if not raw_bytes:
        raise ValueError(f"上传的文件 `{filename}` 为空。")

    ext = Path(filename).suffix.lower()
    if ext == ".pdf":
        return await _parse_pdf_bytes(
            filename,
            raw_bytes,
            ocr_page=ocr_page,
            min_chars_for_native_pdf_page=min_chars_for_native_pdf_page,
            max_ocr_pages=max_ocr_pages,
        )

    if ext in _SUPPORTED_TEXT_EXTENSIONS or ext == "":
        decoded = _clean_page_text(_decode_text_bytes(raw_bytes))
        if not decoded:
            raise ValueError(f"文件 `{filename}` 不包含可索引的文本内容。")
        return [ParsedPage(page_number=1, text=decoded, used_ocr=False)]

    raise ValueError(
        f"暂不支持的文件格式 `{ext}`，当前支持 PDF（.pdf）以及 Markdown / TXT / CSV / 代码等文本文件。"
    )


async def _parse_pdf_bytes(
    filename: str,
    raw_bytes: bytes,
    *,
    ocr_page: PageOcrCallback | None,
    min_chars_for_native_pdf_page: int,
    max_ocr_pages: int,
) -> list[ParsedPage]:
    try:
        doc = pymupdf.open(stream=raw_bytes, filetype="pdf")
    except Exception as exc:
        raise ValueError(f"PDF 文件 `{filename}` 解析失败，可能已损坏或加密：{exc}") from exc

    pages: list[ParsedPage] = []
    ocr_used_count = 0
    scanned_skipped_pages: list[int] = []

    try:
        if doc.is_encrypted:
            raise ValueError(f"PDF 文件 `{filename}` 受密码加密保护，请解密后再上传。")

        for page_idx in range(len(doc)):
            page_number = page_idx + 1
            page = doc[page_idx]
            # sort=True 能更好地按阅读顺序保留中文多列与表格行
            extracted = _clean_page_text(str(page.get_text("text", sort=True) or ""))
            used_ocr = False

            if _meaningful_char_count(extracted) < min_chars_for_native_pdf_page:
                has_images = bool(page.get_images(full=False))
                if has_images and ocr_page is not None and ocr_used_count < max_ocr_pages:
                    pix = page.get_pixmap(dpi=180)
                    png_bytes = pix.tobytes("png")
                    ocr_text = _clean_page_text(await ocr_page(png_bytes, page_number))
                    if _meaningful_char_count(ocr_text) > _meaningful_char_count(extracted):
                        extracted = ocr_text
                        used_ocr = True
                        ocr_used_count += 1
                elif has_images and not extracted:
                    scanned_skipped_pages.append(page_number)

            if extracted:
                pages.append(
                    ParsedPage(
                        page_number=page_number,
                        text=extracted,
                        used_ocr=used_ocr,
                    )
                )
    finally:
        doc.close()

    if not pages:
        if scanned_skipped_pages:
            raise ValueError(
                f"PDF `{filename}` 看起来是纯图片扫描件（未提取到可选中文字）。"
                "请勾选“扫描件视觉 OCR 兜底”并配置 Gemini API Key 后重新上传。"
            )
        raise ValueError(f"PDF `{filename}` 未提取到有效文本内容。")

    return pages


def _split_long_paragraph(text: str, chunk_size: int, overlap: int) -> list[str]:
    """把超长段落按句末标点优先切分，避免截断单词、编号或中文句子。"""

    pieces: list[str] = []
    n = len(text)
    start = 0
    step_overlap = max(0, min(overlap, chunk_size // 2))

    while start < n:
        end = min(n, start + chunk_size)
        if end < n:
            # 在后半段尽量找句号/换行等自然边界
            search_from = start + int(chunk_size * 0.55)
            candidate_window = text[search_from:end]
            matches = list(_SENTENCE_BREAK_RE.finditer(candidate_window))
            if matches:
                end = search_from + matches[-1].end()
            else:
                space_pos = text.rfind(" ", search_from, end)
                if space_pos > search_from:
                    end = space_pos + 1

        segment = text[start:end].strip()
        if segment:
            pieces.append(segment)
        if end >= n:
            break
        next_start = max(start + 1, end - step_overlap)
        start = next_start
    return pieces


def chunk_pages(
    pages: Sequence[ParsedPage],
    *,
    chunk_size: int = 650,
    overlap: int = 120,
) -> list[DocumentChunk]:
    """按页优先合并自然段落，生成保留页码与相邻重叠的文本切片。"""

    if chunk_size < 100:
        raise ValueError("chunk_size 不能小于 100。")

    chunks: list[DocumentChunk] = []
    global_index = 0

    def flush_buffer(page_number: int, parts: list[str]) -> None:
        nonlocal global_index
        if not parts:
            return
        merged = "\n\n".join(parts).strip()
        if merged:
            chunks.append(
                DocumentChunk(
                    page_number=page_number,
                    chunk_index=global_index,
                    content=merged,
                )
            )
            global_index += 1

    for page in pages:
        paragraphs = [p.strip() for p in page.text.split("\n\n") if p.strip()]
        if not paragraphs:
            continue

        buffer_parts: list[str] = []
        buffer_len = 0

        for para in paragraphs:
            if len(para) > chunk_size:
                flush_buffer(page.page_number, buffer_parts)
                buffer_parts.clear()
                buffer_len = 0
                for piece in _split_long_paragraph(para, chunk_size, overlap):
                    chunks.append(
                        DocumentChunk(
                            page_number=page.page_number,
                            chunk_index=global_index,
                            content=piece,
                        )
                    )
                    global_index += 1
                continue

            added_len = len(para) + (2 if buffer_parts else 0)
            if buffer_parts and buffer_len + added_len > chunk_size:
                prev_merged = "\n\n".join(buffer_parts)
                flush_buffer(page.page_number, buffer_parts)
                tail_overlap = prev_merged[-overlap:].strip() if overlap > 0 else ""
                buffer_parts = [tail_overlap, para] if tail_overlap else [para]
                buffer_len = sum(len(item) for item in buffer_parts) + (
                    2 if len(buffer_parts) > 1 else 0
                )
            else:
                buffer_parts.append(para)
                buffer_len += added_len

        flush_buffer(page.page_number, buffer_parts)

    return chunks


def create_gemini_page_ocr(
    *,
    api_key: str | None = None,
    model_name: str | None = None,
) -> PageOcrCallback | None:
    """若有可用的 Gemini Key，构造用于扫描件 PDF 页面转 Markdown 的视觉 OCR 回调。"""

    gemini_cfg = settings.providers.get("gemini")
    key = (
        resolve_api_key(gemini_cfg, api_key)
        if gemini_cfg is not None
        else (api_key or os.getenv("GEMINI_API_KEY", "")).strip()
    )
    if not key:
        return None
    ocr_model = (
        model_name
        or (gemini_cfg.default_model if gemini_cfg is not None else "gemini-4-hydrogen")
    )

    async def _ocr_page(png_bytes: bytes, page_number: int) -> str:
        client = create_client(key)
        try:
            response = await client.aio.models.generate_content(
                model=ocr_model,
                contents=[
                    types.Part.from_bytes(data=png_bytes, mime_type="image/png"),
                    types.Part.from_text(
                        text=(
                            f"这是 PDF 文档第 {page_number} 页的扫描图像。"
                            "请精准提取其中全部文字与表格，表格用标准 Markdown 表格输出。"
                            "只输出文档原始内容，不要添加解释、评论或多余前缀。"
                        )
                    ),
                ],
                config=types.GenerateContentConfig(
                    max_output_tokens=8192,
                    thinking_config=types.ThinkingConfig(
                        thinking_level=types.ThinkingLevel.LOW,
                    ),
                ),
            )
            return str(response.text or "").strip()
        finally:
            await close_genai_client(client)

    return _ocr_page
