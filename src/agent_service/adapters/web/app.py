import asyncio
import html
import json
import os
import time
from collections.abc import AsyncGenerator
from contextlib import aclosing
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import (
    APIRouter,
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
)
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from agent_service.adapters.dashscope.image_client import (
    DashScopeImageClient,
    DashScopeImageConfig,
)
from agent_service.adapters.openai_responses.auth import (
    CHATGPT_USAGE_SETTINGS_URL,
    ChatGPTAuthManager,
)
from agent_service.adapters.security.workspace import WorkspacePathError
from agent_service.adapters.storage.account_store import AccountStore, BrowserLogin
from agent_service.adapters.storage.embeddings import (
    DEFAULT_OLLAMA_BASE_URL,
    create_embedding_provider,
)
from agent_service.adapters.storage.image_store import ImageStore
from agent_service.adapters.storage.knowledge_store import KnowledgeStore
from agent_service.adapters.storage.pdf_parser import create_gemini_page_ocr
from agent_service.adapters.storage.session_store import SessionRecord, SessionStore
from agent_service.adapters.tools.generate_image import GenerateImageTool
from agent_service.adapters.tools.generate_pptx import (
    PPTX_MIME,
    GeneratePptxTool,
    PresentationStore,
    PresentonClient,
    PresentonConfig,
)
from agent_service.adapters.web.auth import (
    AuthenticatedRoute,
    CurrentUser,
    create_auth_router,
    get_current_user,
)
from agent_service.adapters.web.media_proxy import ImageProxyService, MediaProxyError
from agent_service.adapters.web.runtime_manager import (
    EmbeddingFactory,
    ModelFactory,
    ReviewerFactory,
    WebRuntimeManager,
    create_web_tool_registry,
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


@dataclass(frozen=True)
class _UserResources:
    sessions: SessionStore
    knowledge: KnowledgeStore
    presentations: PresentationStore
    images: ImageStore
    runtime: WebRuntimeManager


class _PrivateAPICacheMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith("/api/"):
            await self.app(scope, receive, send)
            return

        async def send_private(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)["Cache-Control"] = "no-store"
            await send(message)

        await self.app(scope, receive, send_private)


def _format_sse(event_payload: dict[str, Any]) -> str:
    event_type = str(event_payload.get("type", "message"))
    data = json.dumps(event_payload, ensure_ascii=False)
    return f"event: {event_type}\ndata: {data}\n\n"


def _resolve_chatgpt_callback_uri(request: Request) -> str:
    url = request.base_url
    host = (url.hostname or "127.0.0.1").lower()
    if host not in {"127.0.0.1", "localhost", "::1"}:
        host = "127.0.0.1"
    port = url.port or 8000
    return f"http://{host}:{port}/api/auth/chatgpt/callback"


def _render_oauth_callback_html(*, ok: bool, message: str) -> str:
    title = "ChatGPT 登录成功" if ok else "ChatGPT 登录未完成"
    safe_msg = html.escape(message)
    payload_json = json.dumps(
        {"type": "chatgpt-oauth-complete", "ok": ok, "message": message},
        ensure_ascii=False,
    ).replace("<", "\\u003c")
    accent = "#059669" if ok else "#dc2626"
    auto_close = "setTimeout(function () { window.close(); }, 900);" if ok else ""
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>{title}</title>
  <style>
    body {{ font-family: system-ui, -apple-system, sans-serif; display: flex; min-height: 100vh; margin: 0; align-items: center; justify-content: center; background: #fafafa; color: #171717; }}
    .card {{ max-width: 420px; padding: 28px 32px; border-radius: 20px; background: #fff; border: 1px solid #e5e5e5; box-shadow: 0 12px 36px rgba(0,0,0,0.08); text-align: center; }}
    h1 {{ margin: 0 0 10px; font-size: 18px; color: {accent}; }}
    p {{ margin: 0 0 18px; font-size: 13.5px; line-height: 1.6; color: #525252; }}
    button {{ border: 0; border-radius: 999px; background: #171717; color: #fff; padding: 8px 18px; font-size: 13px; cursor: pointer; }}
  </style>
</head>
<body>
  <div class="card">
    <h1>{title}</h1>
    <p>{safe_msg}</p>
    <button type="button" onclick="window.close()">关闭窗口</button>
  </div>
  <script>
    (function () {{
      try {{
        if (window.opener && !window.opener.closed) {{
          window.opener.postMessage({payload_json}, "*");
        }}
      }} catch (_e) {{}}
      {auto_close}
    }})();
  </script>
</body>
</html>"""


def create_web_app(
    *,
    workspace: Path | None = None,
    data_dir: Path | None = None,
    knowledge_db_path: Path | None = None,
    static_dir: Path | None = None,
    chatgpt_auth: ChatGPTAuthManager | None = None,
    model_factory: ModelFactory = create_model_gateway,
    reviewer_factory: ReviewerFactory = create_text_reviewer,
    embedding_factory: EmbeddingFactory = create_embedding_provider,
    presenton_client: PresentonClient | None = None,
    image_client: DashScopeImageClient | None = None,
    image_proxy: ImageProxyService | None = None,
    local_mode: bool = False,
    allow_browser_setup: bool = False,
) -> FastAPI:
    """构建通用 Agent Web 工作台 FastAPI 应用。"""

    resolved_image_proxy = image_proxy or ImageProxyService()

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
    accounts = None if local_mode else AccountStore(resolved_data_dir.parent)
    resolved_chatgpt_auth = (
        chatgpt_auth or ChatGPTAuthManager(resolved_data_dir.parent / "auth" / "chatgpt.json")
        if local_mode
        else None
    )

    presentation_missing: tuple[str, ...] = ()
    if presenton_client is None:
        presenton_config, presentation_missing = PresentonConfig.from_environment()
        if presenton_config is not None:
            presenton_client = PresentonClient(presenton_config)
    presentation_message = (
        f"PPT 生成未启用，当前后端进程缺少 {', '.join(presentation_missing)}。"
        if presentation_missing
        else "PPT 生成工具已挂载（Presenton）。"
    )
    if image_client is None:
        image_config = DashScopeImageConfig.from_environment(settings.image_generation_model)
        if image_config is not None:
            image_client = DashScopeImageClient(image_config)
    user_resources: dict[str, _UserResources] = {}

    async def get_resources(user: CurrentUser) -> _UserResources:
        account = user.account
        resources = user_resources.get(account.id)
        if resources is not None:
            return resources
        if account.role == "admin":
            # 首个管理员继承升级前本机工作台的会话、知识库和成品。
            session_dir = resolved_data_dir
            kb_path = resolved_kb_db
        else:
            user_dir = resolved_data_dir.parent / "users" / account.id
            session_dir = user_dir / "sessions"
            kb_path = user_dir / "knowledge.db"
        sessions = SessionStore(session_dir)
        knowledge = KnowledgeStore(kb_path)
        presentations = PresentationStore(session_dir / "presentations")
        images = ImageStore(session_dir / "images")
        runtime = WebRuntimeManager(
            store=sessions,
            workspace=resolved_workspace if local_mode else None,
            knowledge_store=knowledge,
            chatgpt_auth=resolved_chatgpt_auth,
            model_factory=model_factory,
            reviewer_factory=reviewer_factory,
            embedding_factory=embedding_factory,
            presentation_store=presentations,
            presenton_client=presenton_client,
            presentation_message=presentation_message,
            image_client=image_client,
            image_store=images,
        )
        resources = _UserResources(sessions, knowledge, presentations, images, runtime)
        user_resources[account.id] = resources
        return resources

    def cancel_login_runs(login: BrowserLogin) -> None:
        resources = user_resources.get(login.account.id)
        if resources is not None:
            resources.runtime.cancel_login_runs(login.login_id)

    async def require_session(
        session_id: str, resources: Annotated[_UserResources, Depends(get_resources)]
    ) -> SessionRecord:
        try:
            record = resources.sessions.get(session_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if record is None:
            raise HTTPException(status_code=404, detail="会话不存在")
        return record

    def check_knowledge_bases(knowledge: KnowledgeStore, ids: list[str] | None) -> None:
        if local_mode or not ids:
            return
        for kb_id in {item.strip() for item in ids if item.strip()}:
            if knowledge.get_knowledge_base(kb_id) is None:
                raise HTTPException(status_code=404, detail="知识库不存在")

    def require_local_mode() -> None:
        if not local_mode:
            raise HTTPException(status_code=403, detail="此操作仅在本机模式下可用。")

    def select_workspace(raw_path: str | None) -> str:
        if raw_path and raw_path.strip():
            require_local_mode()
            try:
                return str(validate_workspace_path(raw_path))
            except WorkspacePathError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        return str(resolved_workspace) if local_mode else ""

    def session_payload(record: SessionRecord, *, detail: bool) -> dict[str, Any]:
        data = record.to_detail() if detail else record.to_summary()
        data["workspace"] = (record.workspace or str(resolved_workspace)) if local_mode else ""
        return data

    def local_chatgpt_auth() -> ChatGPTAuthManager:
        if resolved_chatgpt_auth is None:
            raise HTTPException(status_code=403, detail="ChatGPT 账号授权仅在本机模式下可用。")
        return resolved_chatgpt_auth

    app = FastAPI(
        title="Agent Service Studio",
        version="2.3.0",
        description="基于自研微内核的多模型通用智能体工作台（支持本地混合向量知识库）",
    )
    app.state.accounts = accounts
    app.include_router(create_auth_router(
        accounts, on_login_end=cancel_login_runs, allow_browser_setup=allow_browser_setup
    ))
    api = APIRouter(prefix="/api", route_class=AuthenticatedRoute)
    app.state.presentation_generation = {
        "enabled": presenton_client is not None,
        "missing_env": list(presentation_missing),
        "message": presentation_message,
    }

    if local_mode:
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

    app.add_middleware(_PrivateAPICacheMiddleware)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        if request.url.path.rstrip("/") in {"/api/auth/login", "/api/auth/setup", "/api/auth/users"}:
            return JSONResponse({"detail": "账号或密码格式不正确。"}, status_code=422)
        return await request_validation_exception_handler(request, exc)

    @app.get("/health", include_in_schema=False)
    async def service_health(request: Request) -> dict[str, Any]:
        if local_mode:
            get_current_user(request)
            return await health()
        return {"status": "ok"}

    @api.get("/health")
    async def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "workspace": str(resolved_workspace) if local_mode else "",
            "workspace_name": workspace_display_name(resolved_workspace) if local_mode else "共享工作台",
            "default_provider": settings.model_config.provider,
            "default_model": settings.model_config.name,
            "knowledge_db": str(resolved_kb_db) if local_mode else "",
            "presentation_generation": app.state.presentation_generation,
            "image_search": {
                "serper_configured": bool(os.getenv("SERPER_API_KEY", "").strip()),
                "tavily_configured": bool(os.getenv("TAVILY_API_KEY", "").strip()),
                "fallback": "wikimedia",
            },
        }

    @api.post("/auth/chatgpt/start")
    async def start_chatgpt_oauth(request: Request) -> dict[str, Any]:
        auth = local_chatgpt_auth()
        try:
            return auth.start_login(
                _resolve_chatgpt_callback_uri(request)
            )
        except ConfigurationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @api.get("/auth/chatgpt/callback", response_class=HTMLResponse)
    async def chatgpt_oauth_callback(
        code: str = Query(default=""),
        state: str = Query(default=""),
        client_id: str | None = Query(default=None),
        scope: str | None = Query(default=None),
        error: str | None = Query(default=None),
        error_description: str | None = Query(default=None),
    ) -> HTMLResponse:
        auth = local_chatgpt_auth()
        if error:
            detail = error_description or error
            return HTMLResponse(
                _render_oauth_callback_html(
                    ok=False,
                    message=f"授权未完成：{detail}",
                ),
                status_code=400,
            )
        if not code.strip() or not state.strip():
            return HTMLResponse(
                _render_oauth_callback_html(
                    ok=False,
                    message="回调缺少 code 或 state 参数，请回到工作台重新发起登录。",
                ),
                status_code=400,
            )
        try:
            status = await auth.complete_callback(
                code=code,
                state=state,
                callback_client_id=client_id,
                scope=scope,
            )
        except AgentServiceError as exc:
            return HTMLResponse(
                _render_oauth_callback_html(ok=False, message=str(exc)),
                status_code=400,
            )
        account = status.account_email or status.account_name or "ChatGPT 账号"
        return HTMLResponse(
            _render_oauth_callback_html(
                ok=True,
                message=f"已成功授权 {account}，可以关闭此窗口返回智启 Agent 工作台。",
            )
        )

    @api.get("/auth/chatgpt/status")
    async def chatgpt_oauth_status() -> dict[str, Any]:
        return local_chatgpt_auth().get_status().to_dict()

    @api.post("/auth/chatgpt/logout")
    async def chatgpt_oauth_logout() -> dict[str, Any]:
        auth = local_chatgpt_auth()
        auth.logout()
        return {
            "logged_out": True,
            **auth.get_status().to_dict(),
        }

    @api.get("/providers")
    async def list_providers(
        refresh_models: bool = Query(default=False),
    ) -> dict[str, Any]:
        items = []
        auth = resolved_chatgpt_auth
        for pid, cfg in settings.providers.items():
            active_models = list(cfg.models)
            default_model = cfg.default_model
            oauth_connected = False
            oauth_account = ""
            oauth_models_error = ""

            if cfg.auth_mode == "chatgpt_oauth" and auth is not None:
                chatgpt_status = auth.get_status()
                oauth_connected = chatgpt_status.connected
                oauth_account = (
                    chatgpt_status.account_email or chatgpt_status.account_name
                )
                if oauth_connected:
                    try:
                        dynamic_models = await auth.fetch_models(
                            force_refresh=refresh_models
                        )
                        if dynamic_models:
                            active_models = dynamic_models
                            default_model = dynamic_models[0].id
                    except AgentServiceError as exc:
                        oauth_models_error = str(exc)
                        chatgpt_status = auth.get_status()
                        oauth_connected = chatgpt_status.connected

            items.append(
                {
                    "id": pid,
                    "label": cfg.label,
                    "protocol": cfg.protocol,
                    "auth_mode": cfg.auth_mode if local_mode else "api_key",
                    "base_url": cfg.base_url,
                    "env_key": cfg.env_key,
                    "env_configured": bool(resolve_api_key(cfg)),
                    "oauth_connected": oauth_connected,
                    "oauth_account": oauth_account,
                    "oauth_usage_url": (
                        CHATGPT_USAGE_SETTINGS_URL
                        if cfg.auth_mode == "chatgpt_oauth" and local_mode
                        else ""
                    ),
                    "oauth_models_error": oauth_models_error,
                    "default_model": default_model,
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
                        for m in active_models
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

    @api.get("/knowledge-bases")
    async def list_knowledge_bases(resources: Annotated[_UserResources, Depends(get_resources)]) -> dict[str, Any]:
        kbs = resources.knowledge.list_knowledge_bases()
        return {
            "knowledge_bases": [kb.to_dict() for kb in kbs],
            "default_embedding": {
                "provider": settings.embedding.provider,
                "model": settings.embedding.model,
                "dimensions": settings.embedding.dimensions,
                "base_url": settings.embedding.base_url if local_mode else "",
                "ollama_default_url": DEFAULT_OLLAMA_BASE_URL if local_mode else "",
            },
        }

    @api.post("/knowledge-bases")
    async def create_knowledge_base(
        req: CreateKnowledgeBaseRequest,
        resources: Annotated[_UserResources, Depends(get_resources)],
    ) -> dict[str, Any]:
        provider = req.embedding_provider
        if not local_mode:
            if req.embedding_base_url.strip() or (
                "embedding_provider" in req.model_fields_set and provider != settings.embedding.provider
            ):
                raise HTTPException(status_code=403, detail="共享模式使用服务端配置的向量模型和地址。")
            provider = settings.embedding.provider
            model = settings.embedding.model
            base_url = settings.embedding.base_url
            dim = settings.embedding.dimensions
        elif provider == "gemini":
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
            created = resources.knowledge.create_knowledge_base(
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

    @api.delete("/knowledge-bases/{kb_id}")
    async def delete_knowledge_base(
        kb_id: str, resources: Annotated[_UserResources, Depends(get_resources)]
    ) -> dict[str, Any]:
        deleted = resources.knowledge.delete_knowledge_base(kb_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="知识库不存在")
        return {"deleted": True, "kb_id": kb_id}

    @api.get("/knowledge-bases/{kb_id}/documents")
    async def list_knowledge_documents(
        kb_id: str, resources: Annotated[_UserResources, Depends(get_resources)]
    ) -> dict[str, Any]:
        knowledge_store = resources.knowledge
        kb = knowledge_store.get_knowledge_base(kb_id)
        if kb is None:
            raise HTTPException(status_code=404, detail="知识库不存在")
        docs = knowledge_store.list_documents(kb_id)
        return {
            "knowledge_base": kb.to_dict(),
            "documents": [d.to_dict() for d in docs],
        }

    @api.post("/knowledge-bases/{kb_id}/documents")
    async def upload_knowledge_documents(
        kb_id: str,
        files: Annotated[list[UploadFile], File()],
        resources: Annotated[_UserResources, Depends(get_resources)],
        api_key: Annotated[str, Form()] = "",
        enable_ocr: Annotated[bool, Form()] = False,
    ) -> dict[str, Any]:
        knowledge_store = resources.knowledge
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

    @api.delete("/knowledge-bases/{kb_id}/documents/{doc_id}")
    async def delete_knowledge_document(
        kb_id: str, doc_id: str, resources: Annotated[_UserResources, Depends(get_resources)]
    ) -> dict[str, Any]:
        knowledge_store = resources.knowledge
        deleted = knowledge_store.delete_document(kb_id, doc_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="文档不存在")
        updated_kb = knowledge_store.get_knowledge_base(kb_id)
        return {
            "deleted": True,
            "doc_id": doc_id,
            "knowledge_base": updated_kb.to_dict() if updated_kb else None,
        }

    @api.post("/knowledge-bases/{kb_id}/search")
    async def search_knowledge_base(
        kb_id: str,
        req: KnowledgeSearchRequest,
        resources: Annotated[_UserResources, Depends(get_resources)],
    ) -> dict[str, Any]:
        knowledge_store = resources.knowledge
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

    @api.get("/workspace/browse")
    async def browse_workspace(
        path: str = Query(
            default="",
            description="要浏览的目录绝对路径，留空返回根目录/盘符列表",
        ),
    ) -> dict[str, Any]:
        require_local_mode()
        try:
            return list_workspace_entries(path)
        except WorkspacePathError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @api.post("/workspace/validate")
    async def validate_workspace(req: ValidateWorkspaceRequest) -> dict[str, Any]:
        require_local_mode()
        try:
            validated = validate_workspace_path(req.path)
        except WorkspacePathError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "path": str(validated),
            "name": workspace_display_name(validated),
        }

    @api.post("/workspace/pick")
    async def pick_workspace(req: PickWorkspaceRequest) -> dict[str, Any]:
        require_local_mode()
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

    @api.get("/tools")
    async def list_tools(
        workspace: str | None = Query(default=None),
    ) -> dict[str, Any]:
        target_ws = select_workspace(workspace)
        registry = create_web_tool_registry(Path(target_ws) if target_ws else None)
        tools_payload = [
            {
                "name": tool.name,
                "description": tool.description,
                "read_only": tool.read_only,
                "requires_approval": tool.requires_approval(),
            }
            for tool in registry.values()
        ]
        if presenton_client is not None:
            tools_payload.append(
                {
                    "name": GeneratePptxTool.name,
                    "description": GeneratePptxTool.description,
                    "read_only": GeneratePptxTool.read_only,
                    "requires_approval": GeneratePptxTool.requires_approval(),
                }
            )
        if image_client is not None:
            tools_payload.append(
                {
                    "name": GenerateImageTool.name,
                    "description": GenerateImageTool.description,
                    "read_only": GenerateImageTool.read_only,
                    "requires_approval": GenerateImageTool.requires_approval(),
                }
            )
        # 展示按需挂载的知识库检索工具契约
        tools_payload.append(
            {
                "name": "search_knowledge_base",
                "description": "当用户勾选本地知识库时自动开放：在选定知识库中执行向量 + 全文混合检索，返回带页码的原文片段。",
                "read_only": True,
                "requires_approval": False,
            }
        )
        return {
            "workspace": target_ws,
            "tools": tools_payload,
        }

    @api.get("/sessions")
    async def list_sessions(resources: Annotated[_UserResources, Depends(get_resources)]) -> dict[str, Any]:
        records = resources.sessions.list_sessions()
        return {
            "sessions": [
                {
                    **session_payload(rec, detail=False),
                    "running": resources.runtime.has_active_run(rec.session_id),
                }
                for rec in records
            ]
        }

    @api.post("/sessions")
    async def create_session(
        req: CreateSessionRequest, resources: Annotated[_UserResources, Depends(get_resources)]
    ) -> dict[str, Any]:
        try:
            provider_cfg = get_provider_config(req.provider)
        except ConfigurationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        target_ws = select_workspace(req.workspace)
        check_knowledge_bases(resources.knowledge, req.knowledge_base_ids)
        model_name = req.model_name.strip() or provider_cfg.default_model
        record = resources.sessions.create(
            title=req.title,
            mode=req.mode,
            provider=provider_cfg.id,
            model_name=model_name,
            workspace=target_ws,
            knowledge_base_ids=list(req.knowledge_base_ids),
        )
        return record.to_detail()

    @api.get("/sessions/{session_id}")
    async def get_session(
        record: Annotated[SessionRecord, Depends(require_session)],
        resources: Annotated[_UserResources, Depends(get_resources)],
    ) -> dict[str, Any]:
        detail = session_payload(record, detail=True)
        detail["running"] = resources.runtime.has_active_run(record.session_id)
        return detail

    @api.get("/sessions/{session_id}/presentations/{artifact_id}/{asset}")
    async def get_presentation_file(
        artifact_id: str,
        asset: Literal["pptx", "pdf", "png"],
        record: Annotated[SessionRecord, Depends(require_session)],
        resources: Annotated[_UserResources, Depends(get_resources)],
    ) -> FileResponse:
        artifact = next(
            (
                item
                for message in record.ui_messages
                for item in message.get("presentations", [])
                if item.get("id") == artifact_id
            ),
            None,
        )
        if artifact is None:
            raise HTTPException(status_code=404, detail="演示文稿成品不存在。")
        try:
            path = resources.presentations.resolve(record.session_id, artifact_id, asset)
        except WorkspacePathError as exc:
            raise HTTPException(status_code=404, detail="演示文稿文件不存在。") from exc
        media_types = {"pptx": PPTX_MIME, "pdf": "application/pdf", "png": "image/png"}
        return FileResponse(
            path,
            media_type=media_types[asset],
            filename=artifact["filename"] if asset == "pptx" else None,
            headers={"X-Content-Type-Options": "nosniff"},
        )

    @api.get("/sessions/{session_id}/images/{artifact_id}")
    async def get_image_file(
        artifact_id: str,
        record: Annotated[SessionRecord, Depends(require_session)],
        resources: Annotated[_UserResources, Depends(get_resources)],
        download: bool = False,
    ) -> FileResponse:
        artifact = next(
            (
                item
                for message in record.ui_messages
                for item in message.get("images", [])
                if item.get("id") == artifact_id
            ),
            None,
        )
        if artifact is None:
            raise HTTPException(status_code=404, detail="图片成品不存在。")
        try:
            path = resources.images.resolve(record.session_id, artifact_id)
        except WorkspacePathError as exc:
            raise HTTPException(status_code=404, detail="图片文件不存在。") from exc
        return FileResponse(
            path,
            media_type="image/png",
            filename=artifact["filename"] if download else None,
            headers={"X-Content-Type-Options": "nosniff"},
        )

    @api.get("/sessions/{session_id}/media-proxy")
    async def proxy_verified_session_image(
        record: Annotated[SessionRecord, Depends(require_session)],
        resources: Annotated[_UserResources, Depends(get_resources)],
        url: str = Query(..., min_length=8, max_length=2048),
    ) -> Response:
        if not resources.runtime.is_verified_media_url(record.session_id, url, record):
            raise HTTPException(
                status_code=403,
                detail="该图片地址未在当前会话工具检索结果中核验，已拒绝代理。",
            )
        try:
            proxied = await resolved_image_proxy.fetch(url)
        except MediaProxyError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
        headers = {"X-Content-Type-Options": "nosniff"}
        if proxied.media_type == "image/svg+xml":
            headers["Content-Security-Policy"] = "default-src 'none'; sandbox"
        return Response(
            content=proxied.content,
            media_type=proxied.media_type,
            headers=headers,
        )

    @api.patch("/sessions/{session_id}")
    async def update_session(
        req: UpdateSessionRequest,
        record: Annotated[SessionRecord, Depends(require_session)],
        resources: Annotated[_UserResources, Depends(get_resources)],
    ) -> dict[str, Any]:
        if req.title is not None and req.title.strip():
            record.title = req.title.strip()
        if req.mode is not None:
            record.mode = req.mode
        if req.workspace is not None and req.workspace.strip():
            record.workspace = select_workspace(req.workspace)
        if req.knowledge_base_ids is not None:
            check_knowledge_bases(resources.knowledge, req.knowledge_base_ids)
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
        resources.sessions.save(record)
        return session_payload(record, detail=True)

    @api.delete("/sessions/{session_id}")
    async def delete_session(
        record: Annotated[SessionRecord, Depends(require_session)],
        resources: Annotated[_UserResources, Depends(get_resources)],
    ) -> dict[str, Any]:
        if not resources.sessions.delete(record.session_id):
            raise HTTPException(status_code=404, detail="会话不存在")
        await resources.runtime.stop_run(record.session_id)
        return {"deleted": True, "session_id": record.session_id}

    @api.post("/sessions/{session_id}/runs")
    async def run_session_turn(
        req: RunTurnRequest,
        request: Request,
        user: CurrentUser,
        record: Annotated[SessionRecord, Depends(require_session)],
        resources: Annotated[_UserResources, Depends(get_resources)],
    ) -> StreamingResponse:
        if not local_mode:
            select_workspace(req.workspace)
        if req.image_options is not None and image_client is None:
            raise HTTPException(status_code=409, detail="生图功能尚未配置，请联系工作台管理员。")
        check_knowledge_bases(resources.knowledge, req.knowledge_base_ids)

        def recheck_login() -> None:
            get_current_user(request)

        async def sse_generator() -> AsyncGenerator[str]:
            login = user.login
            expiry = None
            if login is not None:
                expiry = asyncio.get_running_loop().call_later(
                    max(0, login.expires_at - time.time()),
                    resources.runtime.cancel_login_runs,
                    login.login_id,
                )
            try:
                async with aclosing(
                    resources.runtime.stream_run(
                        record.session_id,
                        req,
                        login_id=login.login_id if login else None,
                        recheck_login=recheck_login if login else None,
                    )
                ) as stream:
                    async for event in stream:
                        yield _format_sse(event)
            except HTTPException as exc:
                yield _format_sse({"type": "run.failed", "error": exc.detail})
            finally:
                if expiry is not None:
                    expiry.cancel()

        return StreamingResponse(
            sse_generator(),
            media_type="text/event-stream",
            headers={
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @api.post("/sessions/{session_id}/approvals/{approval_id}")
    async def decide_approval(
        approval_id: str,
        req: ApprovalDecisionRequest,
        record: Annotated[SessionRecord, Depends(require_session)],
        resources: Annotated[_UserResources, Depends(get_resources)],
    ) -> dict[str, Any]:
        ok, message = resources.runtime.resolve_approval(
            record.session_id,
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

    @api.post("/sessions/{session_id}/stop", response_model=StopRunResponse)
    async def stop_session_run(
        record: Annotated[SessionRecord, Depends(require_session)],
        resources: Annotated[_UserResources, Depends(get_resources)],
    ) -> StopRunResponse:
        stopped = await resources.runtime.stop_run(record.session_id)
        return StopRunResponse(stopped=stopped, status="stopped" if stopped else "idle")

    app.include_router(api)
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
