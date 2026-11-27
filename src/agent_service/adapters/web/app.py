import json
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from agent_service.adapters.security.workspace import (
    WorkspacePathError,
    WorkspacePathPolicy,
)
from agent_service.adapters.storage.embeddings import (
    DEFAULT_OLLAMA_BASE_URL,
    create_embedding_provider,
)
from agent_service.adapters.storage.knowledge_store import KnowledgeStore
from agent_service.adapters.storage.pdf_parser import create_gemini_page_ocr
from agent_service.adapters.storage.session_store import SessionStore
from agent_service.adapters.tools.index import create_tool_registry
from agent_service.adapters.web.runtime_manager import (
    EmbeddingFactory,
    ModelFactory,
    ReviewerFactory,
    WebRuntimeManager,
)
from agent_service.adapters.web.schemas import (
    ApprovalDecisionRequest,
    CreateKnowledgeBaseRequest,
    CreateSessionRequest,
    KnowledgeSearchRequest,
    PickWorkspaceRequest,
    RunTurnRequest,
    StopRunResponse,
    UpdateSessionRequest,
    ValidateWorkspaceRequest,
)
from agent_service.adapters.web.workspace_ops import (
    list_workspace_entries,
    pick_native_workspace_directory,
    validate_workspace_path,
    workspace_display_name,
)
from agent_service.bootstrap import (
    create_model_gateway,
    create_text_reviewer,
    get_provider_config,
)
from agent_service.config.settings import resolve_api_key, settings
from agent_service.errors import AgentServiceError, ConfigurationError

_MAX_KB_FILE_BYTES = 50 * 1024 * 1024  # 单份 PDF/文档上限 50 MB


def _format_sse(event_payload: dict[str, Any]) -> str:
    event_type = str(event_payload.get("type", "message"))
    data = json.dumps(event_payload, ensure_ascii=False)
    return f"event: {event_type}\ndata: {data}\n\n"


def create_web_app(
    *,
    workspace: Path | None = None,
    data_dir: Path | None = None,
    knowledge_db_path: Path | None = None,
    static_dir: Path | None = None,
    model_factory: ModelFactory = create_model_gateway,
    reviewer_factory: ReviewerFactory = create_text_reviewer,
    embedding_factory: EmbeddingFactory = create_embedding_provider,
) -> FastAPI:
    """构建通用 Agent Web 工作台 FastAPI 应用。"""

    resolved_workspace = (workspace or Path.cwd()).resolve()
    resolved_data_dir = (
        data_dir or (resolved_workspace / ".agent_data" / "sessions")
    ).resolve()
    resolved_kb_db = (
        knowledge_db_path or (resolved_data_dir.parent / "knowledge.db")
    ).resolve()
    resolved_static = (
        static_dir or (resolved_workspace / "frontend" / "dist")
    ).resolve()

    store = SessionStore(resolved_data_dir)
    knowledge_store = KnowledgeStore(resolved_kb_db)
    manager = WebRuntimeManager(
        store=store,
        workspace=resolved_workspace,
        knowledge_store=knowledge_store,
        model_factory=model_factory,
        reviewer_factory=reviewer_factory,
        embedding_factory=embedding_factory,
    )

    app = FastAPI(
        title="Agent Service Studio",
        version="2.3.0",
        description="基于自研微内核的多模型通用智能体工作台（支持本地混合向量知识库）",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:8000",
            "http://127.0.0.1:8000",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "workspace": str(resolved_workspace),
            "workspace_name": workspace_display_name(resolved_workspace),
            "default_provider": settings.model_config.provider,
            "default_model": settings.model_config.name,
            "knowledge_db": str(resolved_kb_db),
        }

    @app.get("/api/providers")
    async def list_providers() -> dict[str, Any]:
        items = []
        for pid, cfg in settings.providers.items():
            items.append(
                {
                    "id": pid,
                    "label": cfg.label,
                    "protocol": cfg.protocol,
                    "base_url": cfg.base_url,
                    "env_key": cfg.env_key,
                    "env_configured": bool(resolve_api_key(cfg)),
                    "default_model": cfg.default_model,
                    "thinking_mode": cfg.thinking_mode,
                    "json_mode": cfg.json_mode,
                    "models": [
                        {
                            "id": m.id,
                            "label": m.label,
                            "supports_vision": m.supports_vision,
                            "supports_thinking": m.supports_thinking,
                            "thinking_levels": list(m.thinking_levels),
                            "default_thinking_level": m.default_thinking_level,
                            "max_output_tokens": m.max_output_tokens,
                            "effort_visual": (
                                {
                                    "level": m.effort_visual.level,
                                    "effect": m.effort_visual.effect,
                                    "duration_ms": m.effort_visual.duration_ms,
                                }
                                if m.effort_visual is not None
                                else None
                            ),
                        }
                        for m in cfg.models
                    ],
                }
            )
        return {
            "default_provider": settings.model_config.provider,
            "providers": items,
            "embedding": {
                "provider": settings.embedding.provider,
                "model": settings.embedding.model,
                "dimensions": settings.embedding.dimensions,
                "base_url": settings.embedding.base_url,
                "ollama_default_url": DEFAULT_OLLAMA_BASE_URL,
            },
        }

    @app.get("/api/knowledge-bases")
    async def list_knowledge_bases() -> dict[str, Any]:
        kbs = knowledge_store.list_knowledge_bases()
        return {
            "knowledge_bases": [kb.to_dict() for kb in kbs],
            "default_embedding": {
                "provider": settings.embedding.provider,
                "model": settings.embedding.model,
                "dimensions": settings.embedding.dimensions,
                "base_url": settings.embedding.base_url,
                "ollama_default_url": DEFAULT_OLLAMA_BASE_URL,
            },
        }

    @app.post("/api/knowledge-bases")
    async def create_knowledge_base(
        req: CreateKnowledgeBaseRequest,
    ) -> dict[str, Any]:
        provider = req.embedding_provider
        if provider == "gemini":
            model = req.embedding_model.strip() or settings.embedding.model
            base_url = ""
            dim = req.embedding_dim or settings.embedding.dimensions
        elif provider == "ollama":
            model = req.embedding_model.strip() or "bge-m3"
            base_url = req.embedding_base_url.strip() or DEFAULT_OLLAMA_BASE_URL
            dim = max(0, req.embedding_dim)
        else:
            model = req.embedding_model.strip()
            base_url = req.embedding_base_url.strip()
            dim = max(0, req.embedding_dim)
            if not base_url or not model:
                raise HTTPException(
                    status_code=400,
                    detail="选择 OpenAI 兼容 Embedding 时必须填写 base_url 和模型名。",
                )
        try:
            created = knowledge_store.create_knowledge_base(
                name=req.name,
                description=req.description,
                embedding_provider=provider,
                embedding_model=model,
                embedding_base_url=base_url,
                embedding_dim=dim,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return created.to_dict()

    @app.delete("/api/knowledge-bases/{kb_id}")
    async def delete_knowledge_base(kb_id: str) -> dict[str, Any]:
        deleted = knowledge_store.delete_knowledge_base(kb_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="知识库不存在")
        return {"deleted": True, "kb_id": kb_id}

    @app.get("/api/knowledge-bases/{kb_id}/documents")
    async def list_knowledge_documents(kb_id: str) -> dict[str, Any]:
        kb = knowledge_store.get_knowledge_base(kb_id)
        if kb is None:
            raise HTTPException(status_code=404, detail="知识库不存在")
        docs = knowledge_store.list_documents(kb_id)
        return {
            "knowledge_base": kb.to_dict(),
            "documents": [d.to_dict() for d in docs],
        }

    @app.post("/api/knowledge-bases/{kb_id}/documents")
    async def upload_knowledge_documents(
        kb_id: str,
        files: Annotated[list[UploadFile], File()],
        api_key: Annotated[str, Form()] = "",
        enable_ocr: Annotated[bool, Form()] = False,
    ) -> dict[str, Any]:
        kb = knowledge_store.get_knowledge_base(kb_id)
        if kb is None:
            raise HTTPException(status_code=404, detail="知识库不存在")
        if not files:
            raise HTTPException(status_code=400, detail="请至少选择一个要导入的文件。")

        embedder = embedding_factory(
            provider=kb.embedding_provider,
            model_name=kb.embedding_model,
            dimensions=kb.embedding_dim or None,
            base_url=kb.embedding_base_url or None,
            api_key=api_key.strip() or None,
        )
        ocr_callback = (
            create_gemini_page_ocr(api_key=api_key.strip() or None)
            if enable_ocr
            else None
        )

        ingested: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        try:
            for upload in files:
                filename = Path(upload.filename or "document.pdf").name
                raw = await upload.read()
                if len(raw) > _MAX_KB_FILE_BYTES:
                    errors.append(
                        {"filename": filename, "error": "文件超过 50MB 大小上限。"}
                    )
                    continue
                try:
                    doc = await knowledge_store.ingest_document(
                        kb_id=kb_id,
                        filename=filename,
                        raw_bytes=raw,
                        embedder=embedder,
                        ocr_page=ocr_callback,
                    )
                    ingested.append(doc.to_dict())
                except (ValueError, AgentServiceError) as exc:
                    errors.append({"filename": filename, "error": str(exc)})
        finally:
            await embedder.close()

        if not ingested and errors:
            raise HTTPException(
                status_code=400,
                detail="；".join(f"{e['filename']}：{e['error']}" for e in errors),
            )

        updated_kb = knowledge_store.get_knowledge_base(kb_id)
        return {
            "knowledge_base": updated_kb.to_dict() if updated_kb else kb.to_dict(),
            "documents": ingested,
            "errors": errors,
        }

    @app.delete("/api/knowledge-bases/{kb_id}/documents/{doc_id}")
    async def delete_knowledge_document(kb_id: str, doc_id: str) -> dict[str, Any]:
        deleted = knowledge_store.delete_document(kb_id, doc_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="文档不存在")
        updated_kb = knowledge_store.get_knowledge_base(kb_id)
        return {
            "deleted": True,
            "doc_id": doc_id,
            "knowledge_base": updated_kb.to_dict() if updated_kb else None,
        }

    @app.post("/api/knowledge-bases/{kb_id}/search")
    async def search_knowledge_base(
        kb_id: str,
        req: KnowledgeSearchRequest,
    ) -> dict[str, Any]:
        kb = knowledge_store.get_knowledge_base(kb_id)
        if kb is None:
            raise HTTPException(status_code=404, detail="知识库不存在")
        embedder = embedding_factory(
            provider=kb.embedding_provider,
            model_name=kb.embedding_model,
            dimensions=kb.embedding_dim or None,
            base_url=kb.embedding_base_url or None,
            api_key=(req.api_key or "").strip() or None,
        )
        try:
            hits = await knowledge_store.search(
                kb_ids=[kb_id],
                query_text=req.query,
                embedder_map={kb_id: embedder},
                top_k=req.top_k,
            )
        except (ValueError, AgentServiceError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            await embedder.close()
        return {
            "kb_id": kb_id,
            "query": req.query,
            "hits": [hit.to_dict() for hit in hits],
        }

    @app.get("/api/workspace/browse")
    async def browse_workspace(
        path: str = Query(
            default="",
            description="要浏览的目录绝对路径，留空返回根目录/盘符列表",
        ),
    ) -> dict[str, Any]:
        try:
            return list_workspace_entries(path)
        except WorkspacePathError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/workspace/validate")
    async def validate_workspace(req: ValidateWorkspaceRequest) -> dict[str, Any]:
        try:
            validated = validate_workspace_path(req.path)
        except WorkspacePathError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "path": str(validated),
            "name": workspace_display_name(validated),
        }

    @app.post("/api/workspace/pick")
    async def pick_workspace(req: PickWorkspaceRequest) -> dict[str, Any]:
        try:
            picked = await pick_native_workspace_directory(
                req.initial_dir or str(resolved_workspace)
            )
        except (WorkspacePathError, RuntimeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if picked is None:
            return {"cancelled": True, "path": None, "name": ""}
        return {
            "cancelled": False,
            "path": str(picked),
            "name": workspace_display_name(picked),
        }

    @app.get("/api/tools")
    async def list_tools(
        workspace: str | None = Query(default=None),
    ) -> dict[str, Any]:
        target_ws = resolved_workspace
        if workspace and workspace.strip():
            try:
                target_ws = validate_workspace_path(workspace)
            except WorkspacePathError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        registry = create_tool_registry(WorkspacePathPolicy(target_ws))
        tools_payload = [
            {
                "name": tool.name,
                "description": tool.description,
                "read_only": tool.read_only,
            }
            for tool in registry.values()
        ]
        # 展示按需挂载的知识库检索工具契约
        tools_payload.append(
            {
                "name": "search_knowledge_base",
                "description": "当用户勾选本地知识库时自动开放：在选定知识库中执行向量 + 全文混合检索，返回带页码的原文片段。",
                "read_only": True,
            }
        )
        return {
            "workspace": str(target_ws),
            "tools": tools_payload,
        }

    @app.get("/api/sessions")
    async def list_sessions() -> dict[str, Any]:
        records = store.list_sessions()
        return {
            "sessions": [
                {
                    **rec.to_summary(),
                    "workspace": rec.workspace or str(resolved_workspace),
                    "running": manager.has_active_run(rec.session_id),
                }
                for rec in records
            ]
        }

    @app.post("/api/sessions")
    async def create_session(req: CreateSessionRequest) -> dict[str, Any]:
        try:
            provider_cfg = get_provider_config(req.provider)
        except ConfigurationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        target_ws = str(resolved_workspace)
        if req.workspace.strip():
            try:
                target_ws = str(validate_workspace_path(req.workspace))
            except WorkspacePathError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        model_name = req.model_name.strip() or provider_cfg.default_model
        record = store.create(
            title=req.title,
            mode=req.mode,
            provider=provider_cfg.id,
            model_name=model_name,
            workspace=target_ws,
            knowledge_base_ids=list(req.knowledge_base_ids),
        )
        return record.to_detail()

    @app.get("/api/sessions/{session_id}")
    async def get_session(session_id: str) -> dict[str, Any]:
        try:
            record = store.get(session_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if record is None:
            raise HTTPException(status_code=404, detail="会话不存在")
        detail = record.to_detail()
        detail["workspace"] = record.workspace or str(resolved_workspace)
        detail["running"] = manager.has_active_run(session_id)
        return detail

    @app.patch("/api/sessions/{session_id}")
    async def update_session(
        session_id: str, req: UpdateSessionRequest
    ) -> dict[str, Any]:
        try:
            record = store.get(session_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if record is None:
            raise HTTPException(status_code=404, detail="会话不存在")
        if req.title is not None and req.title.strip():
            record.title = req.title.strip()
        if req.mode is not None:
            record.mode = req.mode
        if req.workspace is not None and req.workspace.strip():
            try:
                record.workspace = str(validate_workspace_path(req.workspace))
            except WorkspacePathError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        if req.knowledge_base_ids is not None:
            record.knowledge_base_ids = [
                kid.strip() for kid in req.knowledge_base_ids if kid.strip()
            ]
        if req.provider is not None:
            try:
                provider_cfg = get_provider_config(req.provider)
            except ConfigurationError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            record.provider = provider_cfg.id
            if req.model_name is None:
                record.model_name = provider_cfg.default_model
        if req.model_name is not None and req.model_name.strip():
            record.model_name = req.model_name.strip()
        store.save(record)
        detail = record.to_detail()
        detail["workspace"] = record.workspace or str(resolved_workspace)
        return detail

    @app.delete("/api/sessions/{session_id}")
    async def delete_session(session_id: str) -> dict[str, Any]:
        await manager.stop_run(session_id)
        try:
            deleted = store.delete(session_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not deleted:
            raise HTTPException(status_code=404, detail="会话不存在")
        return {"deleted": True, "session_id": session_id}

    @app.post("/api/sessions/{session_id}/runs")
    async def run_session_turn(
        session_id: str, req: RunTurnRequest
    ) -> StreamingResponse:
        try:
            record = store.get(session_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if record is None:
            raise HTTPException(status_code=404, detail="会话不存在")

        async def sse_generator() -> AsyncGenerator[str]:
            async for event in manager.stream_run(record, req):
                yield _format_sse(event)

        return StreamingResponse(
            sse_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @app.post("/api/sessions/{session_id}/approvals/{approval_id}")
    async def decide_approval(
        session_id: str,
        approval_id: str,
        req: ApprovalDecisionRequest,
    ) -> dict[str, Any]:
        ok, message = manager.resolve_approval(
            session_id,
            approval_id,
            approved=req.approved,
            request_hash=req.request_hash,
        )
        if not ok:
            raise HTTPException(status_code=409, detail=message)
        return {
            "accepted": True,
            "approval_id": approval_id,
            "approved": req.approved,
        }

    @app.post("/api/sessions/{session_id}/stop", response_model=StopRunResponse)
    async def stop_session_run(session_id: str) -> StopRunResponse:
        stopped = await manager.stop_run(session_id)
        return StopRunResponse(stopped=stopped, status="stopped" if stopped else "idle")

    assets_dir = resolved_static / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

    @app.get("/", include_in_schema=False)
    async def serve_index() -> Any:
        index_html = resolved_static / "index.html"
        if index_html.exists():
            return FileResponse(str(index_html))
        return {
            "message": "前端尚未构建。请先在 frontend/ 执行 `npm install && npm run build`，或使用 Vite 开发服务器访问 http://localhost:5173。",
            "api_docs": "/docs",
        }

    return app
