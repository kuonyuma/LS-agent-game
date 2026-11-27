"""本地 SQLite + FTS5 + numpy 混合向量知识库与 PDF 解析单元测试。"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pymupdf
import pytest

from agent_service.adapters.storage.knowledge_store import (
    KnowledgeStore,
    tokenize_for_fts,
)
from agent_service.adapters.storage.pdf_parser import chunk_pages, parse_document_bytes


class DeterministicKeywordEmbedder:
    """基于固定主题维度的确定性测试 Embedding 提供器（不产生外网调用）。"""

    provider_id = "fake"
    model_name = "fake-embed-v1"

    def __init__(self, dim: int = 4) -> None:
        self._dim = dim
        self.dimensions: int = dim
        self.closed = False

    def _vectorize(self, text: str) -> list[float]:
        lowered = text.lower()
        base = [
            1.0 if any(k in lowered for k in ("缓考", "deferred", "请假", "病假")) else 0.05,
            1.0 if any(k in lowered for k in ("cs302", "学分", "credit", "选课")) else 0.05,
            1.0 if any(k in lowered for k in ("奖学金", "scholarship", "绩点", "gpa")) else 0.05,
            1.0 if any(k in lowered for k in ("宿舍", "dorm", "门禁", "宵禁")) else 0.05,
        ]
        if self._dim <= 4:
            return base[: self._dim]
        return base + [0.05] * (self._dim - 4)

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._vectorize(t) for t in texts]

    async def embed_query(self, text: str) -> list[float]:
        return self._vectorize(text)

    async def close(self) -> None:
        self.closed = True


def _build_pdf_bytes(page_texts: Sequence[str]) -> bytes:
    """在内存中构造真实的多页 PDF 字节流。"""

    doc = pymupdf.open()
    try:
        for text in page_texts:
            page = doc.new_page()
            page.insert_text((72, 72), text, fontsize=12)
        return bytes(doc.tobytes())
    finally:
        doc.close()


def _build_scanned_image_pdf_bytes() -> bytes:
    """构造一页仅包含位图、无文字层的扫描件 PDF。"""

    doc = pymupdf.open()
    try:
        page = doc.new_page(width=200, height=200)
        pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 64, 64), False)
        pix.set_rect(pix.irect, (240, 240, 240))
        page.insert_image(pymupdf.Rect(10, 10, 190, 190), pixmap=pix)
        return bytes(doc.tobytes())
    finally:
        doc.close()


@pytest.mark.asyncio
async def test_pdf_parser_preserves_page_numbers_and_overlap_chunking():
    pdf_bytes = _build_pdf_bytes(
        [
            "Chapter 1 Campus Rules\n\nCS302 requires 4 credits and attendance above 80 percent.",
            "Chapter 2 Exam Policy\n\nDeferred exam applications must be submitted 3 days before exam.",
        ]
    )
    pages = await parse_document_bytes("handbook.pdf", pdf_bytes)
    assert len(pages) == 2
    assert pages[0].page_number == 1
    assert "CS302" in pages[0].text
    assert pages[1].page_number == 2
    assert "Deferred exam" in pages[1].text

    chunks = chunk_pages(pages, chunk_size=120, overlap=20)
    assert any(c.page_number == 1 and "CS302" in c.content for c in chunks)
    assert any(c.page_number == 2 and "Deferred exam" in c.content for c in chunks)


@pytest.mark.asyncio
async def test_scanned_pdf_triggers_ocr_callback_and_errors_without_ocr():
    scanned_pdf = _build_scanned_image_pdf_bytes()

    with pytest.raises(ValueError, match="纯图片扫描件"):
        await parse_document_bytes("scan.pdf", scanned_pdf, ocr_page=None)

    called_pages: list[int] = []

    async def fake_ocr(png_bytes: bytes, page_number: int) -> str:
        assert png_bytes.startswith(b"\x89PNG")
        called_pages.append(page_number)
        return "第 1 页扫描件识别结果：学生缓考须附三甲医院诊断证明并在考前三天提交教务处。"

    pages = await parse_document_bytes("scan.pdf", scanned_pdf, ocr_page=fake_ocr)
    assert called_pages == [1]
    assert len(pages) == 1
    assert pages[0].used_ocr is True
    assert "缓考" in pages[0].text


def test_tokenize_for_fts_handles_cjk_bigrams_and_ascii_codes():
    tokens = tokenize_for_fts("CS302 缓考申请 2026.1")
    token_set = set(tokens.split())
    assert "cs302" in token_set
    assert "2026.1" in token_set
    assert "缓考" in token_set
    assert "申请" in token_set


@pytest.mark.asyncio
async def test_sqlite_persistence_hybrid_rrf_search_and_lifecycle(tmp_path: Path):
    db_path = tmp_path / "knowledge.db"
    embedder = DeterministicKeywordEmbedder(dim=4)
    store = KnowledgeStore(db_path)

    kb = store.create_knowledge_base(
        name="2026 学生手册",
        description="包含学籍、选课、缓考与奖学金规定",
        embedding_provider="gemini",
        embedding_model="gemini-embedding-2",
        embedding_dim=4,
    )

    # 1. 入库中文 Markdown（测中文 FTS5 + 向量双路召回）与真实多页 PDF（测页码保留）
    md_doc = await store.ingest_document(
        kb_id=kb.kb_id,
        filename="学籍管理规定.md",
        raw_bytes=(
            "## 缓考申请办法\n\n"
            "学生因突发疾病或不可抗力原因无法按时参加期末集中考试，必须在正式考试开始前三个工作日内登录教务系统提交缓考申请表，并附上二级甲等以上医院开具的正式诊断证明与辅导员签字意见。\n\n"
            "## 奖学金评定标准\n\n"
            "学校一等奖学金按学年综合素质测评与课程成绩评定，要求申请者该学年平均学分绩点 GPA 不低于 3.85，且所有必修课与限选课均无补考、重修或挂科记录。"
        ).encode(),
        embedder=embedder,
        chunk_size=120,
        overlap=20,
    )
    assert md_doc.chunk_count >= 2

    pdf_doc = await store.ingest_document(
        kb_id=kb.kb_id,
        filename="course_guide.pdf",
        raw_bytes=_build_pdf_bytes(
            [
                "Page 1 Overview: Dormitory curfew starts at 23:30 on weekdays.",
                "Page 2 Course Rules: CS302 Operating Systems carries 4.0 credits.",
            ]
        ),
        embedder=embedder,
    )
    assert pdf_doc.page_count == 2

    # 2. 模拟服务重启：用完全全新的 KnowledgeStore 实例直接从磁盘 SQLite 恢复，不丢失数据
    restarted_store = KnowledgeStore(db_path)
    kbs = restarted_store.list_knowledge_bases()
    assert len(kbs) == 1
    assert kbs[0].document_count == 2
    assert kbs[0].chunk_count == md_doc.chunk_count + pdf_doc.chunk_count

    # 3. 混合检索：中文词“缓考”命中 Markdown 切片
    zh_hits = await restarted_store.search(
        kb_ids=[kb.kb_id],
        query_text="期末生病怎么申请缓考？",
        embedder_map={kb.kb_id: embedder},
        top_k=3,
    )
    assert len(zh_hits) >= 1
    assert zh_hits[0].filename == "学籍管理规定.md"
    assert "缓考" in zh_hits[0].content
    assert zh_hits[0].vector_score is not None
    assert zh_hits[0].fts_rank == 1

    # 4. 混合检索：精确课程号 CS302 命中 PDF 第 2 页
    en_hits = await restarted_store.search(
        kb_ids=[kb.kb_id],
        query_text="How many credits is CS302?",
        embedder_map={kb.kb_id: embedder},
        top_k=3,
    )
    assert len(en_hits) >= 1
    assert en_hits[0].filename == "course_guide.pdf"
    assert en_hits[0].page_number == 2
    assert "CS302" in en_hits[0].content

    # 5. 同名文档重新上传会覆盖旧切片（不产生重复脏数据）
    replaced = await restarted_store.ingest_document(
        kb_id=kb.kb_id,
        filename="course_guide.pdf",
        raw_bytes=_build_pdf_bytes(
            ["Updated Course Rules: CS302 now carries 5.0 credits."]
        ),
        embedder=embedder,
    )
    assert replaced.page_count == 1
    assert len(restarted_store.list_documents(kb.kb_id)) == 2
    updated_hits = await restarted_store.search(
        kb_ids=[kb.kb_id],
        query_text="CS302 credits",
        embedder_map={kb.kb_id: embedder},
        top_k=2,
    )
    assert "5.0 credits" in updated_hits[0].content

    # 6. 维度不匹配保护（防止向已有库混入不同模型的向量）
    wrong_dim_embedder = DeterministicKeywordEmbedder(dim=8)
    with pytest.raises(ValueError, match="向量维度不匹配"):
        await restarted_store.ingest_document(
            kb_id=kb.kb_id,
            filename="mismatch.txt",
            raw_bytes="宿舍门禁时间是晚上十一点半。".encode(),
            embedder=wrong_dim_embedder,
        )

    # 7. 删除单个文档与级联删除整个知识库（含 FTS5 清理）
    assert restarted_store.delete_document(kb.kb_id, replaced.doc_id) is True
    assert len(restarted_store.list_documents(kb.kb_id)) == 1
    assert restarted_store.delete_knowledge_base(kb.kb_id) is True
    assert restarted_store.list_knowledge_bases() == []
    empty_hits = await restarted_store.search(
        kb_ids=[kb.kb_id],
        query_text="缓考",
        embedder_map={kb.kb_id: embedder},
    )
    assert empty_hits == []


@pytest.mark.asyncio
async def test_gemini_embedding_wraps_each_chunk_as_independent_content():
    """回归测试：gemini-embedding-2 批量向量化必须传入独立 types.Content，避免被 SDK 合并成 1 条。"""

    from types import SimpleNamespace
    from typing import Any, cast

    from google import genai
    from google.genai import _transformers as genai_transformers
    from google.genai import types

    from agent_service.adapters.storage.embeddings import GeminiEmbeddingProvider

    captured_contents: list[Any] = []

    class FakeAsyncModels:
        async def embed_content(
            self,
            *,
            model: str,
            contents: Any,
            config: Any = None,
        ) -> types.EmbedContentResponse:
            _ = config
            captured_contents.append(contents)
            # 复现真实 google-genai 对 gemini-embedding-2 的预处理路径
            normalized = (
                genai_transformers.t_contents(contents)
                if "gemini-embedding-2" in model
                else contents
            )
            return types.EmbedContentResponse(
                embeddings=[
                    types.ContentEmbedding(values=[0.1, 0.2, 0.3, 0.4])
                    for _ in normalized
                ]
            )

    fake_client = cast(
        genai.Client,
        SimpleNamespace(aio=SimpleNamespace(models=FakeAsyncModels())),
    )
    provider = GeminiEmbeddingProvider(
        model_name="gemini-embedding-2",
        dimensions=4,
        batch_size=3,
        client=fake_client,
    )

    vectors = await provider.embed_documents(["片段一", "片段二", "片段三", "片段四"])
    assert len(vectors) == 4
    assert len(captured_contents) == 2
    assert len(genai_transformers.t_contents(captured_contents[0])) == 3
    assert len(genai_transformers.t_contents(captured_contents[1])) == 1

