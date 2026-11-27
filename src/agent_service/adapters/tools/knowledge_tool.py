"""面向 Agent 的本地知识库混合检索只读工具。"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, ClassVar

from agent_service.adapters.storage.embeddings import EmbeddingProvider
from agent_service.adapters.storage.knowledge_store import KnowledgeStore
from agent_service.kernel.dispatch.base import Tool, ToolResult


class SearchKnowledgeBaseTool(Tool):
    """在用户选定的本地向量知识库中执行混合检索并返回带出处页码的原文片段。"""

    name = "search_knowledge_base"
    description = (
        "在用户已挂载的本地向量知识库（如学校手册、课程资料、制度 PDF 等）中执行"
        "语义向量 + 全文关键词混合检索，返回最相关的原文片段、来源文件名与精确页码。"
        "当用户提问涉及已挂载知识库中的规定、流程、数据或课程信息时，应先调用此工具查证，"
        "若首次检索不够精准可改写关键词再次检索。"
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "OBJECT",
        "properties": {
            "query": {
                "type": "STRING",
                "description": "检索问题或关键词（可包含课程编号、条款名称、专有名词或自然语言问题）",
            },
            "top_k": {
                "type": "INTEGER",
                "description": "返回的片段数量（1~15，默认 6）",
            },
            "kb_id": {
                "type": "STRING",
                "description": "可选：限定只检索某一个已挂载知识库的 ID；留空则检索全部已挂载知识库",
            },
        },
        "required": ["query"],
    }
    read_only = True

    def __init__(
        self,
        *,
        store: KnowledgeStore,
        kb_ids: Sequence[str],
        embedder_map: dict[str, EmbeddingProvider],
    ) -> None:
        self._store = store
        self._kb_ids = tuple(dict.fromkeys(kid for kid in kb_ids if kid))
        self._embedder_map = dict(embedder_map)

    async def run(self, parameter: dict[str, Any]) -> ToolResult:
        raw_query = parameter.get("query", "")
        query_text = str(raw_query).strip() if raw_query is not None else ""
        if not query_text:
            return ToolResult(content="检索失败：query 不能为空。", is_error=True)

        raw_top_k = parameter.get("top_k", 6)
        try:
            top_k = int(raw_top_k) if raw_top_k is not None else 6
        except (TypeError, ValueError):
            top_k = 6
        top_k = max(1, min(top_k, 15))

        raw_kb_id = parameter.get("kb_id", "")
        requested_kb = str(raw_kb_id).strip() if raw_kb_id is not None else ""
        if requested_kb:
            if requested_kb not in self._kb_ids:
                return ToolResult(
                    content=(
                        f"知识库 `{requested_kb}` 未在本次对话中挂载。"
                        f"当前可用知识库 ID：{', '.join(self._kb_ids)}"
                    ),
                    is_error=True,
                )
            target_kb_ids: tuple[str, ...] = (requested_kb,)
        else:
            target_kb_ids = self._kb_ids

        if not target_kb_ids:
            return ToolResult(
                content="当前对话未挂载任何知识库。",
                is_error=True,
            )

        try:
            hits = await self._store.search(
                kb_ids=target_kb_ids,
                query_text=query_text,
                embedder_map=self._embedder_map,
                top_k=top_k,
            )
        except Exception as exc:  # noqa: BLE001 - 将底层异常转为可读工具错误
            return ToolResult(content=f"知识库检索失败：{exc}", is_error=True)

        if not hits:
            return ToolResult(
                content=(
                    f"在已挂载的知识库中未检索到与“{query_text}”直接相关的片段。"
                    "你可以尝试换用更正式的术语、关键词或更宽泛的表述重新检索；"
                    "若仍无结果，请向用户如实说明知识库中未查到相关内容。"
                ),
                is_error=False,
            )

        blocks: list[str] = [f"共命中 {len(hits)} 条知识库原文片段（按混合相关性排序）："]
        for idx, hit in enumerate(hits, start=1):
            signals: list[str] = []
            if hit.vector_score is not None:
                signals.append(f"语义相似度 {hit.vector_score:.3f}")
            if hit.fts_rank is not None:
                signals.append(f"关键词命中第 {hit.fts_rank} 名")
            signal_text = " | ".join(signals) if signals else "混合匹配"
            blocks.append(
                f"---\n"
                f"[片段 {idx}] 来源：《{hit.filename}》第 {hit.page_number} 页"
                f"（知识库：{hit.kb_name} | {signal_text}）\n"
                f"{hit.content}"
            )
        return ToolResult(content="\n\n".join(blocks), is_error=False)
