"""基于 SQLite 单文件（原文/页码/FTS5）+ numpy 向量矩阵的本地混合知识库存储。"""

from __future__ import annotations

import hashlib
import re
import sqlite3
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import numpy as np

from agent_service.adapters.storage.embeddings import EmbeddingProvider
from agent_service.adapters.storage.pdf_parser import (
    PageOcrCallback,
    chunk_pages,
    parse_document_bytes,
)

_ASCII_WORD_RE = re.compile(r"[a-z0-9]+(?:[._-][a-z0-9]+)*")
_CJK_RUN_RE = re.compile(r"[\u4e00-\u9fff]+")
_RRF_K = 60.0


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def tokenize_for_fts(text: str) -> str:
    """为 SQLite FTS5 生成中英混合检索词元。

    - 英文、数字、课程号、条款编号保留完整词（如 cs302、3.7、2026）；
    - 连续中文生成 unigram + bigram，解决 FTS5 默认空格分词无法命中中文子词的问题。
    """

    lowered = text.lower()
    tokens: list[str] = []
    tokens.extend(_ASCII_WORD_RE.findall(lowered))

    for run in _CJK_RUN_RE.findall(lowered):
        chars = list(run)
        tokens.extend(chars)
        if len(chars) >= 2:
            tokens.extend(run[i : i + 2] for i in range(len(chars) - 1))

    return " ".join(tokens)


def _build_fts_match_query(raw_query: str) -> str:
    """将任意用户自然语言提问安全转换为 FTS5 MATCH 表达式，避免标点引发语法错误。"""

    lowered = raw_query.lower()
    terms: list[str] = []
    seen: set[str] = set()

    for word in _ASCII_WORD_RE.findall(lowered):
        if word not in seen:
            seen.add(word)
            terms.append(word)

    for run in _CJK_RUN_RE.findall(lowered):
        if len(run) >= 2:
            for i in range(len(run) - 1):
                bg = run[i : i + 2]
                if bg not in seen:
                    seen.add(bg)
                    terms.append(bg)
        else:
            if run not in seen:
                seen.add(run)
                terms.append(run)

    if not terms:
        return ""
    # 限制子句数量并加双引号转义；注意 SQLite FTS5 查询语法要求逻辑运算符 OR 必须大写，否则会被当成普通词 "or"
    limited = terms[:24]
    return " OR ".join(f'"{term}"' for term in limited)


def normalize_vector(vec: Sequence[float]) -> np.ndarray:
    arr = np.asarray(vec, dtype=np.float32)
    if arr.ndim != 1 or arr.size == 0:
        raise ValueError("向量必须是非空一维数值数组。")
    norm = float(np.linalg.norm(arr))
    if norm > 0.0:
        arr = arr / norm
    return arr


@dataclass(frozen=True)
class KnowledgeBaseSummary:
    kb_id: str
    name: str
    description: str
    embedding_provider: str
    embedding_model: str
    embedding_base_url: str
    embedding_dim: int
    document_count: int
    chunk_count: int
    created_at: str
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "kb_id": self.kb_id,
            "name": self.name,
            "description": self.description,
            "embedding_provider": self.embedding_provider,
            "embedding_model": self.embedding_model,
            "embedding_base_url": self.embedding_base_url,
            "embedding_dim": self.embedding_dim,
            "document_count": self.document_count,
            "chunk_count": self.chunk_count,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class KnowledgeDocumentInfo:
    doc_id: str
    kb_id: str
    filename: str
    file_hash: str
    page_count: int
    chunk_count: int
    ocr_pages: int
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "doc_id": self.doc_id,
            "kb_id": self.kb_id,
            "filename": self.filename,
            "file_hash": self.file_hash,
            "page_count": self.page_count,
            "chunk_count": self.chunk_count,
            "ocr_pages": self.ocr_pages,
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class KnowledgeHit:
    chunk_id: int
    kb_id: str
    kb_name: str
    doc_id: str
    filename: str
    page_number: int
    chunk_index: int
    content: str
    score: float
    vector_score: float | None = None
    fts_rank: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "kb_id": self.kb_id,
            "kb_name": self.kb_name,
            "doc_id": self.doc_id,
            "filename": self.filename,
            "page_number": self.page_number,
            "chunk_index": self.chunk_index,
            "content": self.content,
            "score": round(self.score, 6),
            "vector_score": round(self.vector_score, 4)
            if self.vector_score is not None
            else None,
            "fts_rank": self.fts_rank,
        }


@dataclass
class _MatrixCacheEntry:
    chunk_ids: list[int]
    matrix: np.ndarray  # shape: (N, D), float32, L2-normalized


class KnowledgeStore:
    """持久化于本地 SQLite 单文件的混合向量知识库，内存仅保留只读矩阵加速缓存。"""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path.resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._matrix_cache: dict[str, _MatrixCacheEntry] = {}
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("pragma foreign_keys = on")
        conn.execute("pragma journal_mode = wal")
        return conn

    def _init_schema(self) -> None:
        with self._lock, self._connect() as conn:
            conn.executescript(
                """
                create table if not exists knowledge_bases (
                    kb_id text primary key,
                    name text not null,
                    description text not null default '',
                    embedding_provider text not null default 'gemini',
                    embedding_model text not null default 'gemini-embedding-2',
                    embedding_base_url text not null default '',
                    embedding_dim integer not null default 0,
                    created_at text not null,
                    updated_at text not null
                );

                create table if not exists documents (
                    doc_id text primary key,
                    kb_id text not null references knowledge_bases(kb_id) on delete cascade,
                    filename text not null,
                    file_hash text not null,
                    page_count integer not null default 0,
                    chunk_count integer not null default 0,
                    ocr_pages integer not null default 0,
                    created_at text not null
                );

                create index if not exists idx_documents_kb_id
                    on documents (kb_id);

                create table if not exists chunks (
                    chunk_id integer primary key autoincrement,
                    kb_id text not null references knowledge_bases(kb_id) on delete cascade,
                    doc_id text not null references documents(doc_id) on delete cascade,
                    filename text not null,
                    page_number integer not null,
                    chunk_index integer not null,
                    content text not null,
                    embedding blob not null
                );

                create index if not exists idx_chunks_kb_id
                    on chunks (kb_id);

                create index if not exists idx_chunks_doc_id
                    on chunks (doc_id);

                create virtual table if not exists chunks_fts using fts5(
                    tokenized_content,
                    kb_id unindexed,
                    doc_id unindexed
                );
                """
            )

    def _invalidate_cache(self, kb_id: str) -> None:
        self._matrix_cache.pop(kb_id, None)

    def list_knowledge_bases(self) -> list[KnowledgeBaseSummary]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                select
                    kb.kb_id,
                    kb.name,
                    kb.description,
                    kb.embedding_provider,
                    kb.embedding_model,
                    kb.embedding_base_url,
                    kb.embedding_dim,
                    kb.created_at,
                    kb.updated_at,
                    (select count(*) from documents d where d.kb_id = kb.kb_id) as doc_count,
                    (select count(*) from chunks c where c.kb_id = kb.kb_id) as chunk_count
                from knowledge_bases kb
                order by kb.updated_at desc
                """
            ).fetchall()
        return [
            KnowledgeBaseSummary(
                kb_id=str(row["kb_id"]),
                name=str(row["name"]),
                description=str(row["description"]),
                embedding_provider=str(row["embedding_provider"]),
                embedding_model=str(row["embedding_model"]),
                embedding_base_url=str(row["embedding_base_url"]),
                embedding_dim=int(row["embedding_dim"]),
                document_count=int(row["doc_count"]),
                chunk_count=int(row["chunk_count"]),
                created_at=str(row["created_at"]),
                updated_at=str(row["updated_at"]),
            )
            for row in rows
        ]

    def get_knowledge_base(self, kb_id: str) -> KnowledgeBaseSummary | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                """
                select
                    kb.kb_id,
                    kb.name,
                    kb.description,
                    kb.embedding_provider,
                    kb.embedding_model,
                    kb.embedding_base_url,
                    kb.embedding_dim,
                    kb.created_at,
                    kb.updated_at,
                    (select count(*) from documents d where d.kb_id = kb.kb_id) as doc_count,
                    (select count(*) from chunks c where c.kb_id = kb.kb_id) as chunk_count
                from knowledge_bases kb
                where kb.kb_id = ?
                """,
                (kb_id,),
            ).fetchone()
        if row is None:
            return None
        return KnowledgeBaseSummary(
            kb_id=str(row["kb_id"]),
            name=str(row["name"]),
            description=str(row["description"]),
            embedding_provider=str(row["embedding_provider"]),
            embedding_model=str(row["embedding_model"]),
            embedding_base_url=str(row["embedding_base_url"]),
            embedding_dim=int(row["embedding_dim"]),
            document_count=int(row["doc_count"]),
            chunk_count=int(row["chunk_count"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    def create_knowledge_base(
        self,
        *,
        name: str,
        description: str = "",
        embedding_provider: str = "gemini",
        embedding_model: str = "gemini-embedding-2",
        embedding_base_url: str = "",
        embedding_dim: int = 1536,
    ) -> KnowledgeBaseSummary:
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("知识库名称不能为空。")
        kb_id = f"kb_{uuid4().hex[:10]}"
        now = _now_iso()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                insert into knowledge_bases (
                    kb_id,
                    name,
                    description,
                    embedding_provider,
                    embedding_model,
                    embedding_base_url,
                    embedding_dim,
                    created_at,
                    updated_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    kb_id,
                    clean_name,
                    description.strip(),
                    embedding_provider.strip().lower() or "gemini",
                    embedding_model.strip() or "gemini-embedding-2",
                    embedding_base_url.strip(),
                    max(0, int(embedding_dim)),
                    now,
                    now,
                ),
            )
        created = self.get_knowledge_base(kb_id)
        assert created is not None
        return created

    def delete_knowledge_base(self, kb_id: str) -> bool:
        with self._lock, self._connect() as conn:
            conn.execute("delete from chunks_fts where kb_id = ?", (kb_id,))
            cursor = conn.execute(
                "delete from knowledge_bases where kb_id = ?", (kb_id,)
            )
            deleted = cursor.rowcount > 0
        self._invalidate_cache(kb_id)
        return deleted

    def list_documents(self, kb_id: str) -> list[KnowledgeDocumentInfo]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                select
                    doc_id,
                    kb_id,
                    filename,
                    file_hash,
                    page_count,
                    chunk_count,
                    ocr_pages,
                    created_at
                from documents
                where kb_id = ?
                order by created_at desc
                """,
                (kb_id,),
            ).fetchall()
        return [
            KnowledgeDocumentInfo(
                doc_id=str(row["doc_id"]),
                kb_id=str(row["kb_id"]),
                filename=str(row["filename"]),
                file_hash=str(row["file_hash"]),
                page_count=int(row["page_count"]),
                chunk_count=int(row["chunk_count"]),
                ocr_pages=int(row["ocr_pages"]),
                created_at=str(row["created_at"]),
            )
            for row in rows
        ]

    def delete_document(self, kb_id: str, doc_id: str) -> bool:
        with self._lock, self._connect() as conn:
            conn.execute(
                "delete from chunks_fts where kb_id = ? and doc_id = ?",
                (kb_id, doc_id),
            )
            cursor = conn.execute(
                "delete from documents where kb_id = ? and doc_id = ?",
                (kb_id, doc_id),
            )
            deleted = cursor.rowcount > 0
            if deleted:
                conn.execute(
                    "update knowledge_bases set updated_at = ? where kb_id = ?",
                    (_now_iso(), kb_id),
                )
        self._invalidate_cache(kb_id)
        return deleted

    async def ingest_document(
        self,
        *,
        kb_id: str,
        filename: str,
        raw_bytes: bytes,
        embedder: EmbeddingProvider,
        ocr_page: PageOcrCallback | None = None,
        chunk_size: int = 650,
        overlap: int = 120,
        replace_duplicate: bool = True,
    ) -> KnowledgeDocumentInfo:
        """一步到位：解析 PDF/文本 → 切片 → 向量化归一化 → 事务落盘 SQLite + FTS5。"""

        kb = self.get_knowledge_base(kb_id)
        if kb is None:
            raise ValueError(f"知识库 `{kb_id}` 不存在。")

        file_hash = hashlib.sha256(raw_bytes).hexdigest()
        pages = await parse_document_bytes(
            filename,
            raw_bytes,
            ocr_page=ocr_page,
        )
        chunks = chunk_pages(pages, chunk_size=chunk_size, overlap=overlap)
        if not chunks:
            raise ValueError(f"文档 `{filename}` 切片后无有效文本内容。")

        # 在持锁写库前完成异步 Embedding 调用，避免阻塞 SQLite 读写
        vectors_raw = await embedder.embed_documents([c.content for c in chunks])
        normalized_vectors = [normalize_vector(v) for v in vectors_raw]
        actual_dim = int(normalized_vectors[0].size)

        if kb.chunk_count > 0 and kb.embedding_dim > 0 and kb.embedding_dim != actual_dim:
            raise ValueError(
                f"向量维度不匹配：知识库 `{kb.name}` 现有向量维度为 {kb.embedding_dim}，"
                f"当前 Embedding 模型返回维度为 {actual_dim}。请使用同一 Embedding 模型或新建知识库。"
            )

        doc_id = f"doc_{uuid4().hex[:10]}"
        now = _now_iso()
        ocr_pages = sum(1 for p in pages if p.used_ocr)

        with self._lock, self._connect() as conn:
            existing = conn.execute(
                """
                select doc_id from documents
                where kb_id = ? and (file_hash = ? or filename = ?)
                """,
                (kb_id, file_hash, filename),
            ).fetchall()
            if existing:
                if not replace_duplicate:
                    raise ValueError(f"文件 `{filename}` 已存在于该知识库中。")
                for row in existing:
                    old_id = str(row["doc_id"])
                    conn.execute(
                        "delete from chunks_fts where kb_id = ? and doc_id = ?",
                        (kb_id, old_id),
                    )
                    conn.execute(
                        "delete from documents where kb_id = ? and doc_id = ?",
                        (kb_id, old_id),
                    )

            conn.execute(
                """
                insert into documents (
                    doc_id,
                    kb_id,
                    filename,
                    file_hash,
                    page_count,
                    chunk_count,
                    ocr_pages,
                    created_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    doc_id,
                    kb_id,
                    filename,
                    file_hash,
                    len(pages),
                    len(chunks),
                    ocr_pages,
                    now,
                ),
            )

            for chunk, vec in zip(chunks, normalized_vectors, strict=True):
                cursor = conn.execute(
                    """
                    insert into chunks (
                        kb_id,
                        doc_id,
                        filename,
                        page_number,
                        chunk_index,
                        content,
                        embedding
                    ) values (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        kb_id,
                        doc_id,
                        filename,
                        chunk.page_number,
                        chunk.chunk_index,
                        chunk.content,
                        vec.tobytes(),
                    ),
                )
                chunk_id = int(cursor.lastrowid or 0)
                conn.execute(
                    """
                    insert into chunks_fts (
                        rowid,
                        tokenized_content,
                        kb_id,
                        doc_id
                    ) values (?, ?, ?, ?)
                    """,
                    (
                        chunk_id,
                        tokenize_for_fts(f"{filename} {chunk.content}"),
                        kb_id,
                        doc_id,
                    ),
                )

            conn.execute(
                """
                update knowledge_bases
                set embedding_dim = ?, updated_at = ?
                where kb_id = ?
                """,
                (actual_dim, now, kb_id),
            )

        self._invalidate_cache(kb_id)
        return KnowledgeDocumentInfo(
            doc_id=doc_id,
            kb_id=kb_id,
            filename=filename,
            file_hash=file_hash,
            page_count=len(pages),
            chunk_count=len(chunks),
            ocr_pages=ocr_pages,
            created_at=now,
        )

    def _load_matrix_cache(self, kb_id: str) -> _MatrixCacheEntry | None:
        cached = self._matrix_cache.get(kb_id)
        if cached is not None:
            return cached

        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                select chunk_id, embedding
                from chunks
                where kb_id = ?
                order by chunk_id asc
                """,
                (kb_id,),
            ).fetchall()

        if not rows:
            return None

        ids: list[int] = []
        vectors: list[np.ndarray] = []
        for row in rows:
            ids.append(int(row["chunk_id"]))
            vectors.append(np.frombuffer(row["embedding"], dtype=np.float32).copy())

        matrix = np.vstack(vectors)
        entry = _MatrixCacheEntry(chunk_ids=ids, matrix=matrix)
        self._matrix_cache[kb_id] = entry
        return entry

    def _search_fts(
        self,
        kb_ids: Sequence[str],
        query_text: str,
        limit: int,
    ) -> list[int]:
        match_expr = _build_fts_match_query(query_text)
        if not match_expr or not kb_ids:
            return []

        placeholders = ", ".join("?" for _ in kb_ids)
        sql = f"""
            select rowid
            from chunks_fts
            where chunks_fts match ?
              and kb_id in ({placeholders})
            order by bm25(chunks_fts) asc
            limit ?
        """
        with self._lock, self._connect() as conn:
            try:
                rows = conn.execute(
                    sql,
                    (match_expr, *kb_ids, limit),
                ).fetchall()
            except sqlite3.OperationalError:
                return []
        return [int(row["rowid"]) for row in rows]

    async def search(
        self,
        *,
        kb_ids: Sequence[str],
        query_text: str,
        embedder_map: Mapping[str, EmbeddingProvider] | None = None,
        default_embedder: EmbeddingProvider | None = None,
        top_k: int = 6,
    ) -> list[KnowledgeHit]:
        """在指定知识库集合上执行“向量语义 + FTS5 关键词”双路检索并用 RRF 融合排序。"""

        cleaned_query = query_text.strip()
        unique_kb_ids = [kid for kid in dict.fromkeys(kb_ids) if kid]
        if not cleaned_query or not unique_kb_ids:
            return []

        clamped_k = max(1, min(top_k, 20))
        candidate_k = max(20, clamped_k * 4)

        # 1. 按知识库计算向量语义相似度（不同知识库可各自使用对应 EmbeddingProvider）
        vec_scores_by_chunk: dict[int, float] = {}
        query_vec_cache: dict[tuple[str, str, int], np.ndarray] = {}

        for kb_id in unique_kb_ids:
            entry = self._load_matrix_cache(kb_id)
            if entry is None:
                continue
            embedder = (embedder_map or {}).get(kb_id) or default_embedder
            if embedder is None:
                continue
            vec_key = (
                embedder.provider_id,
                embedder.model_name,
                int(entry.matrix.shape[1]),
            )
            q_vec = query_vec_cache.get(vec_key)
            if q_vec is None:
                q_vec = normalize_vector(await embedder.embed_query(cleaned_query))
                query_vec_cache[vec_key] = q_vec

            if q_vec.size != entry.matrix.shape[1]:
                continue

            sims = entry.matrix @ q_vec
            take = min(candidate_k, sims.size)
            if take == sims.size:
                top_idx = np.argsort(sims)[::-1]
            else:
                part = np.argpartition(sims, -take)[-take:]
                top_idx = part[np.argsort(sims[part])[::-1]]
            for idx in top_idx:
                cid = entry.chunk_ids[int(idx)]
                vec_scores_by_chunk[cid] = float(sims[int(idx)])

        vec_ranked_ids = [
            cid
            for cid, _ in sorted(
                vec_scores_by_chunk.items(),
                key=lambda item: item[1],
                reverse=True,
            )[:candidate_k]
        ]

        # 2. SQLite FTS5 全文关键词检索
        fts_ranked_ids = self._search_fts(unique_kb_ids, cleaned_query, candidate_k)

        # 3. RRF 倒数排名融合
        rrf_scores: dict[int, float] = {}
        fts_rank_map: dict[int, int] = {}

        for rank, cid in enumerate(vec_ranked_ids, start=1):
            rrf_scores[cid] = rrf_scores.get(cid, 0.0) + 1.0 / (_RRF_K + rank)
        for rank, cid in enumerate(fts_ranked_ids, start=1):
            fts_rank_map[cid] = rank
            rrf_scores[cid] = rrf_scores.get(cid, 0.0) + 1.0 / (_RRF_K + rank)

        if not rrf_scores:
            return []

        top_chunk_ids = [
            cid
            for cid, _ in sorted(
                rrf_scores.items(),
                key=lambda item: (item[1], vec_scores_by_chunk.get(item[0], -1.0)),
                reverse=True,
            )[:clamped_k]
        ]

        placeholders = ", ".join("?" for _ in top_chunk_ids)
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                f"""
                select
                    c.chunk_id,
                    c.kb_id,
                    kb.name as kb_name,
                    c.doc_id,
                    c.filename,
                    c.page_number,
                    c.chunk_index,
                    c.content
                from chunks c
                join knowledge_bases kb on kb.kb_id = c.kb_id
                where c.chunk_id in ({placeholders})
                """,
                tuple(top_chunk_ids),
            ).fetchall()

        row_map = {int(row["chunk_id"]): row for row in rows}
        hits: list[KnowledgeHit] = []
        for cid in top_chunk_ids:
            row = row_map.get(cid)
            if row is None:
                continue
            hits.append(
                KnowledgeHit(
                    chunk_id=cid,
                    kb_id=str(row["kb_id"]),
                    kb_name=str(row["kb_name"]),
                    doc_id=str(row["doc_id"]),
                    filename=str(row["filename"]),
                    page_number=int(row["page_number"]),
                    chunk_index=int(row["chunk_index"]),
                    content=str(row["content"]),
                    score=rrf_scores[cid],
                    vector_score=vec_scores_by_chunk.get(cid),
                    fts_rank=fts_rank_map.get(cid),
                )
            )
        return hits
