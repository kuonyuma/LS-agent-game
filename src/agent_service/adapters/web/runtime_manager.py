import asyncio
import base64
import binascii
import time
from collections.abc import AsyncGenerator, Callable
from contextlib import aclosing
from dataclasses import dataclass, field, replace
from html import escape
from importlib.resources import files
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from google.genai import types

from agent_service.adapters.dashscope.image_client import DashScopeImageClient
from agent_service.adapters.openai_compat.model import ReasoningEffort
from agent_service.adapters.openai_responses.auth import ChatGPTAuthManager
from agent_service.adapters.security.coordinator import RunCoordinator
from agent_service.adapters.security.reviewer import TextReviewer
from agent_service.adapters.security.workspace import (
    WorkspacePathError,
    WorkspacePathPolicy,
)
from agent_service.adapters.storage.embeddings import (
    EmbeddingProvider,
    create_embedding_provider,
)
from agent_service.adapters.storage.image_store import ImageStore
from agent_service.adapters.storage.knowledge_store import (
    KnowledgeBaseSummary,
    KnowledgeStore,
)
from agent_service.adapters.storage.session_store import (
    SessionMode,
    SessionRecord,
    SessionStore,
    deserialize_contents,
    serialize_contents,
)
from agent_service.adapters.tools.generate_image import GenerateImageTool
from agent_service.adapters.tools.generate_pptx import (
    GeneratePptxTool,
    PresentationStore,
    PresentonClient,
)
from agent_service.adapters.tools.index import create_tool_registry
from agent_service.adapters.tools.knowledge_tool import SearchKnowledgeBaseTool
from agent_service.adapters.tools.search_images import SearchImagesTool
from agent_service.adapters.web.media_proxy import extract_verified_media_urls
from agent_service.adapters.web.office_attachments import (
    MAX_OFFICE_BASE64_CHARS,
    OFFICE_MIME_TYPES,
    parse_office_attachment,
)
from agent_service.adapters.web.schemas import RunAttachment, RunTurnRequest
from agent_service.adapters.web.workspace_ops import validate_workspace_path
from agent_service.bootstrap import (
    create_model_gateway,
    create_runner,
    create_text_reviewer,
    get_provider_config,
)
from agent_service.config.settings import (
    ProviderConfig,
    resolve_model_max_tokens,
    resolve_model_thinking_level,
    settings,
)
from agent_service.errors import AgentServiceError
from agent_service.kernel.context.history import runtime_notice
from agent_service.kernel.dispatch.approval import ToolApprovalRequest
from agent_service.kernel.dispatch.base import Tool
from agent_service.kernel.model.port import ModelGateway
from agent_service.kernel.runtime.control import RunControl
from agent_service.kernel.runtime.events import LoopEvent
from agent_service.kernel.strategy.plan import ExecutionPlan

ModelFactory = Callable[..., ModelGateway]
ReviewerFactory = Callable[..., TextReviewer]
EmbeddingFactory = Callable[..., EmbeddingProvider]

_MAX_TEXT_ATTACHMENT_CHARS = 120_000
_MAX_TURN_OFFICE_BASE64_CHARS = 3 * MAX_OFFICE_BASE64_CHARS
_SHARED_TOOLS = frozenset({
    "update_plan",
    "reflect",
    "subagent",
    "web_search",
    "fetch_web_page",
    "get_current_time",
    "date_calc",
    "calculate",
    "get_weather",
    "get_exchange_rate",
    "search_places",
    "plan_route",
    "search_images",
    "search_papers",
    "inspect_paper",
    "read_paper_fulltext",
    "search_knowledge_base",
    "generate_pptx",
    "generate_image",
})


def create_web_tool_registry(
    workspace: Path | None,
    *,
    deepseek_api_key: str | None = None,
    serper_api_key: str | None = None,
    tavily_api_key: str | None = None,
    knowledge_tool: Tool | None = None,
    presentation_tool: Tool | None = None,
    image_tool: Tool | None = None,
    default_timezone: str = "UTC",
) -> dict[str, Tool]:
    registry = create_tool_registry(
        WorkspacePathPolicy(workspace) if workspace is not None else None,
        deepseek_api_key=deepseek_api_key,
        serper_api_key=serper_api_key,
        tavily_api_key=tavily_api_key,
        knowledge_tool=knowledge_tool,
        presentation_tool=presentation_tool,
        image_tool=image_tool,
        default_timezone=default_timezone,
        allow_deepseek_fetch=workspace is not None,
    )
    if workspace is None:
        # 共享工作台仅声明显式允许的工具。子代理继承父级工具的范围。
        return {name: tool for name, tool in registry.items() if name in _SHARED_TOOLS}
    return registry


def serialize_plan(plan: ExecutionPlan | None) -> dict[str, Any] | None:
    if plan is None or not plan.has_state():
        return None
    return {
        "goal": plan.goal,
        "phase": plan.phase,
        "revision": plan.revision,
        "replan_reason": plan.replan_reason,
        "pending_reflection_reason": plan.pending_reflection_reason,
        "unverified_edits": list(plan.unverified_edits),
        "steps": [
            {
                "id": step.id,
                "title": step.title,
                "status": step.status,
                "notes": step.notes,
            }
            for step in plan.steps
        ],
        "reflections": [
            {
                "id": ref.id,
                "observation": ref.observation,
                "diagnosis": ref.diagnosis,
                "strategy": ref.strategy,
                "next_action": ref.next_action,
                "trigger": ref.trigger,
                "step_id": ref.step_id,
            }
            for ref in plan.reflections
        ],
    }


def _model_supports_vision(provider_cfg: ProviderConfig, model_name: str) -> bool:
    option = next((m for m in provider_cfg.models if m.id == model_name), None)
    return option.supports_vision if option is not None else True


async def _build_attachment_parts(
    attachments: list[RunAttachment],
    *,
    provider_cfg: ProviderConfig,
    model_name: str,
) -> list[types.Part]:
    """将附件转为本轮用户消息的多模态或文本部分。"""

    if not attachments:
        return []

    binary_parts: list[types.Part] = []
    text_blocks: list[str] = []
    attached_names: list[str] = []
    supports_vision = _model_supports_vision(provider_cfg, model_name)
    remaining_text = _MAX_TEXT_ATTACHMENT_CHARS
    office_base64_chars = 0

    for att in attachments:
        mime = (att.mime_type or "").strip().lower()
        extension = Path(att.name).suffix.lower()
        office_mime = OFFICE_MIME_TYPES.get(extension)
        is_image = office_mime is None and (att.kind == "image" or mime.startswith("image/"))

        if is_image:
            if not supports_vision:
                raise ValueError(
                    f"当前模型 `{model_name}` 不支持图片视觉输入，请切换支持视觉的模型后再上传图片。"
                )
            if not att.data_base64:
                continue
            try:
                raw_bytes = base64.b64decode(att.data_base64)
            except (ValueError, binascii.Error):
                continue
            binary_parts.append(
                types.Part.from_bytes(data=raw_bytes, mime_type=mime or "image/png")
            )
            attached_names.append(att.name)
            continue

        # 文本 / 代码文件优先使用 text_content；若仅传 base64 则尝试 UTF-8 解码
        text_body: str | None = att.text_content
        if office_mime is not None:
            office_base64_chars += len(att.data_base64)
            if office_base64_chars > _MAX_TURN_OFFICE_BASE64_CHARS:
                raise ValueError("本轮 Office 附件总大小超过 30 MB，请拆分后再发送。")
            mime = office_mime
            try:
                text_body = await asyncio.to_thread(
                    parse_office_attachment, att.data_base64, extension, remaining_text
                )
            except ValueError as exc:
                raise ValueError(f"附件 `{att.name}`：{exc}") from exc
        elif text_body is None and att.data_base64:
            try:
                raw_bytes = base64.b64decode(att.data_base64)
            except (ValueError, binascii.Error):
                raw_bytes = b""
            if mime == "application/pdf" and provider_cfg.protocol == "gemini" and raw_bytes:
                binary_parts.append(
                    types.Part.from_bytes(data=raw_bytes, mime_type="application/pdf")
                )
                attached_names.append(att.name)
                continue
            if raw_bytes:
                try:
                    text_body = raw_bytes.decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise ValueError(
                        f"附件 `{att.name}` 不是受支持的文本或图片格式（{mime or 'binary'}）。"
                    ) from exc

        if text_body is not None:
            truncated = text_body[:remaining_text]
            remaining_text -= len(truncated)
            suffix = (
                "\n[文件内容已按本轮附件文本长度上限截断]"
                if len(text_body) > len(truncated)
                else ""
            )
            text_blocks.append(
                f"<attached-file name=\"{escape(att.name)}\" mime=\"{escape(mime or 'text/plain')}\">\n"
                f"{truncated}{suffix}\n"
                "</attached-file>"
            )
            attached_names.append(att.name)

    if not attached_names:
        return []
    header = f"[本条消息随附 {len(attached_names)} 个聊天附件：{', '.join(attached_names)}]"
    return [
        types.Part.from_text(text="\n\n".join([header, *text_blocks])),
        *binary_parts,
    ]


def _build_knowledge_notice(kbs: list[KnowledgeBaseSummary]) -> str:
    lines = [
        "## 已挂载的本地知识库（仅本轮生效）",
        "用户已选择让你参考以下本地向量知识库回答问题：",
    ]
    for kb in kbs:
        desc = f" — {kb.description}" if kb.description else ""
        lines.append(
            f"- **{kb.name}**（`kb_id={kb.kb_id}`，{kb.document_count} 份文档 / {kb.chunk_count} 条切片）{desc}"
        )
    lines.append(
        "请优先调用 `search_knowledge_base` 查证原文后再作答；"
        "回答时必须明确标注出处（如《文件名》第 X 页）；"
        "如果知识库中未检索到相关条款或信息，请如实告知用户，严禁编造。"
    )
    return runtime_notice("\n".join(lines))


@dataclass
class PendingApproval:
    request: ToolApprovalRequest
    future: asyncio.Future[bool]


@dataclass
class ActiveRun:
    run_id: str
    session_id: str
    control: RunControl
    event_queue: asyncio.Queue[dict[str, Any] | None]
    login_id: str | None = None
    done_event: asyncio.Event = field(default_factory=asyncio.Event)
    producer_task: asyncio.Task[None] | None = None
    stopping: bool = False
    pending_approvals: dict[str, PendingApproval] = field(default_factory=dict)
    verified_media_urls: set[str] = field(default_factory=set)

    def cancel_all(self) -> None:
        self.stopping = True
        self.control.stopped = True
        for pending in list(self.pending_approvals.values()):
            if not pending.future.done():
                pending.future.set_result(False)
        self.pending_approvals.clear()
        if self.producer_task is not None and not self.producer_task.done():
            self.producer_task.cancel()
        self.event_queue.put_nowait(None)


class WebRuntimeManager:
    """管理 Web 会话的并发运行、动态工作区、可选知识库挂载、SSE 事件与人工审批。"""

    def __init__(
        self,
        *,
        store: SessionStore,
        workspace: Path | None,
        knowledge_store: KnowledgeStore | None = None,
        presentation_store: PresentationStore | None = None,
        presenton_client: PresentonClient | None = None,
        presentation_message: str = "PPT 生成工具尚未配置。",
        image_client: DashScopeImageClient | None = None,
        image_store: ImageStore | None = None,
        chatgpt_auth: ChatGPTAuthManager | None = None,
        model_factory: ModelFactory = create_model_gateway,
        reviewer_factory: ReviewerFactory = create_text_reviewer,
        embedding_factory: EmbeddingFactory = create_embedding_provider,
    ) -> None:
        self.store = store
        self.workspace = workspace
        self.knowledge_store = knowledge_store
        self.presentation_store = presentation_store
        self.presenton_client = presenton_client
        self.image_client = image_client
        self.image_store = image_store
        self.chatgpt_auth = chatgpt_auth
        self.presentation_skill = (
            files("agent_service.config").joinpath("presentation_skill.md").read_text(encoding="utf-8")
            if presenton_client is not None and presentation_store is not None
            else (
                "用户要求使用工作台生成、修改或导出 PPT 成品时，告知 PPT 功能暂未开启，请联系工作台管理员。"
                f"管理员排查时可参考后端状态：{presentation_message} "
                "请勿为替代这项内置功能而调用 shell、探测或安装本机依赖。"
                "普通代码任务和关于 PPT 的问答照常处理。"
            )
        )
        self.rich_output_skill = (
            files("agent_service.config")
            .joinpath("rich_output_skill.md")
            .read_text(encoding="utf-8")
        )
        self._model_factory = model_factory
        self._reviewer_factory = reviewer_factory
        self._embedding_factory = embedding_factory
        self._active_runs: dict[str, ActiveRun] = {}

    def has_active_run(self, session_id: str) -> bool:
        active = self._active_runs.get(session_id)
        return active is not None and not active.stopping

    def is_verified_media_url(
        self,
        session_id: str,
        url: str,
        record: SessionRecord | None = None,
    ) -> bool:
        """校验图片 URL 是否曾在本会话的受信任工具输出中出现，阻止开放代理与臆造链接。"""
        target = url.strip()
        if not target:
            return False
        active = self._active_runs.get(session_id)
        if active is not None and target in active.verified_media_urls:
            return True
        if record is None:
            return False
        for message in record.ui_messages:
            if not isinstance(message, dict):
                continue
            saved_urls = message.get("verified_media_urls")
            if isinstance(saved_urls, list) and target in saved_urls:
                return True
            tool_calls = message.get("tool_calls")
            if isinstance(tool_calls, list):
                for trace in tool_calls:
                    if not isinstance(trace, dict) or trace.get("is_error"):
                        continue
                    t_name = str(trace.get("name") or "")
                    t_result = str(trace.get("result") or "")
                    if target in extract_verified_media_urls(t_name, t_result):
                        return True
        return False

    def cancel_login_runs(self, login_id: str) -> None:
        for active in self._active_runs.values():
            if active.login_id == login_id and not active.stopping:
                active.cancel_all()

    async def stop_run(self, session_id: str, *, wait_timeout: float = 5.0) -> bool:
        active = self._active_runs.get(session_id)
        if active is None:
            return False
        active.cancel_all()
        try:
            await asyncio.wait_for(active.done_event.wait(), timeout=wait_timeout)
        except TimeoutError:
            # 超时兜底清理占位，避免会话永久锁死
            if self._active_runs.get(session_id) is active:
                self._active_runs.pop(session_id, None)
        return True

    def resolve_approval(
        self,
        session_id: str,
        approval_id: str,
        *,
        approved: bool,
        request_hash: str,
    ) -> tuple[bool, str]:
        active = self._active_runs.get(session_id)
        if active is None:
            return False, "当前会话没有正在运行的任务。"
        pending = active.pending_approvals.get(approval_id)
        if pending is None:
            return False, f"未找到待审批调用 `{approval_id}`，可能已超时或已完成。"
        if pending.request.request_hash != request_hash:
            return False, "审批请求指纹（request_hash）不匹配，已拒绝本次操作。"
        if not pending.future.done():
            pending.future.set_result(approved)
        active.pending_approvals.pop(approval_id, None)
        return True, "ok"

    def _resolve_target_workspace(
        self,
        record: SessionRecord,
        req: RunTurnRequest,
    ) -> Path | None:
        if self.workspace is None:
            return None
        raw = (
            req.workspace.strip()
            if isinstance(req.workspace, str) and req.workspace.strip()
            else record.workspace.strip()
        )
        if not raw:
            return self.workspace
        return validate_workspace_path(raw)

    def _resolve_active_knowledge_bases(
        self,
        record: SessionRecord,
        req: RunTurnRequest,
    ) -> list[KnowledgeBaseSummary]:
        if self.knowledge_store is None:
            return []
        requested = (
            req.knowledge_base_ids
            if req.knowledge_base_ids is not None
            else record.knowledge_base_ids
        )
        resolved: list[KnowledgeBaseSummary] = []
        seen: set[str] = set()
        for raw_id in requested:
            kid = raw_id.strip()
            if not kid or kid in seen:
                continue
            seen.add(kid)
            kb = self.knowledge_store.get_knowledge_base(kid)
            if kb is not None:
                resolved.append(kb)
        return resolved

    async def stream_run(
        self,
        session_id: str,
        req: RunTurnRequest,
        *,
        login_id: str | None = None,
        recheck_login: Callable[[], None] | None = None,
    ) -> AsyncGenerator[dict[str, Any]]:
        existing = self._active_runs.get(session_id)
        if existing is not None:
            if existing.stopping:
                try:
                    await asyncio.wait_for(existing.done_event.wait(), timeout=5.0)
                except TimeoutError:
                    if self._active_runs.get(session_id) is existing:
                        self._active_runs.pop(session_id, None)
            if session_id in self._active_runs:
                yield {
                    "type": "run.failed",
                    "error": "当前会话已有任务正在运行，请等待完成或先点击停止。",
                }
                return

        # 等待上一轮结束后再确认登录和会话。确认之后直到占用会话前均不让出执行。
        if recheck_login is not None:
            recheck_login()
        record = self.store.get(session_id)
        if record is None:
            yield {"type": "run.failed", "error": "会话不存在"}
            return

        provider_id = (
            (req.provider or record.provider or settings.model_config.provider)
            .strip()
            .lower()
        )
        mode: SessionMode = "multi"

        try:
            provider_cfg = get_provider_config(provider_id)
            if (
                provider_cfg.protocol == "openai_responses"
                and self.chatgpt_auth is not None
                and self.chatgpt_auth.is_connected()
            ):
                cached = self.chatgpt_auth.get_cached_models()
                if cached:
                    provider_cfg = replace(
                        provider_cfg,
                        default_model=cached[0].id,
                        models=tuple(cached),
                    )
            target_workspace = self._resolve_target_workspace(record, req)
        except (AgentServiceError, WorkspacePathError) as exc:
            yield {"type": "run.failed", "error": str(exc)}
            return

        active_kbs = self._resolve_active_knowledge_bases(record, req)
        active_kb_ids = [kb.kb_id for kb in active_kbs]

        model_name = (
            req.model_name.strip()
            if isinstance(req.model_name, str) and req.model_name.strip()
            else (
                record.model_name
                if record.provider == provider_id and record.model_name
                else provider_cfg.default_model
            )
        )
        resolved_level = resolve_model_thinking_level(
            provider_cfg,
            model_name,
            req.reasoning_effort,
            thinking_enabled=req.thinking_enabled,
        )
        reasoning_effort = cast(ReasoningEffort, resolved_level)
        effective_thinking_enabled = resolved_level != "off"

        run_id = f"run_{uuid4().hex[:10]}"
        control = RunControl()
        event_queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        active_run = ActiveRun(
            run_id=run_id,
            session_id=record.session_id,
            control=control,
            event_queue=event_queue,
            login_id=login_id,
        )
        self._active_runs[record.session_id] = active_run

        start_ts = time.perf_counter()

        model_gateway: ModelGateway | None = None
        reviewer: TextReviewer | None = None
        embedders_to_close: list[EmbeddingProvider] = []
        producer_task: asyncio.Task[None] | None = None

        user_msg_id = f"msg_{uuid4().hex[:10]}"
        assistant_msg_id = f"msg_{uuid4().hex[:10]}"

        thinking_chunks: list[str] = []
        text_chunks: list[str] = []
        tool_traces: dict[str, dict[str, Any]] = {}
        tool_order: list[str] = []
        subagent_traces: list[dict[str, Any]] = []
        latest_plan: dict[str, Any] | None = None
        safety_blocked = False
        safety_category = ""
        final_error = ""
        final_reason = "completed"
        final_turns = 0
        final_usage: dict[str, int] = {}
        final_safety_usage: dict[str, int] = {}
        final_history: list[types.Content] | None = None
        artifacts: dict[str, list[dict[str, Any]]] = {}
        deferred_completion: dict[str, Any] | None = None
        search_images_tool_ref: list[SearchImagesTool] = []
        presentation_tool = (
            GeneratePptxTool(self.presenton_client, self.presentation_store, record.session_id)
            if self.presenton_client is not None and self.presentation_store is not None
            else None
        )
        selected_ratio = req.image_options.aspect_ratio if req.image_options is not None else None
        image_tool = (
            GenerateImageTool(
                self.image_client, self.image_store, record.session_id, selected_ratio=selected_ratio
            )
            if self.image_client is not None and self.image_store is not None
            else None
        )

        async def web_permission_check(request: ToolApprovalRequest) -> bool:
            loop = asyncio.get_running_loop()
            future: asyncio.Future[bool] = loop.create_future()
            active_run.pending_approvals[request.call_id] = PendingApproval(
                request=request,
                future=future,
            )
            await event_queue.put(
                {
                    "type": "tool.approval_required",
                    "run_id": run_id,
                    "approval_id": request.call_id,
                    "tool_call_id": request.call_id,
                    "tool_name": request.tool_name,
                    "tool_arguments": request.arguments,
                    "request_hash": request.request_hash,
                }
            )
            try:
                return await asyncio.wait_for(future, timeout=300.0)
            except TimeoutError:
                return False
            finally:
                active_run.pending_approvals.pop(request.call_id, None)

        def handle_loop_event(ev: LoopEvent) -> dict[str, Any]:
            nonlocal latest_plan, safety_blocked, safety_category
            nonlocal final_error, final_reason, final_turns, final_usage
            nonlocal final_safety_usage, final_history

            plan_dict = serialize_plan(ev.plan)
            if plan_dict is not None:
                latest_plan = plan_dict

            payload: dict[str, Any] = {
                "type": ev.type,
                "run_id": run_id,
                "turn": ev.turn,
                "phase": ev.phase,
            }
            if plan_dict is not None:
                payload["plan"] = plan_dict

            if ev.type == "thinking.delta":
                thinking_chunks.append(ev.text)
                payload["text"] = ev.text
            elif ev.type == "text.delta":
                text_chunks.append(ev.text)
                payload["text"] = ev.text
            elif ev.type == "tool.requested":
                trace = {
                    "call_id": ev.tool_call_id,
                    "name": ev.tool_name,
                    "arguments": ev.tool_arguments,
                    "requires_approval": ev.requires_approval,
                    "request_hash": ev.approval_request_hash,
                    "status": "awaiting_approval" if ev.requires_approval else "running",
                    "result": "",
                    "is_error": False,
                }
                tool_traces[ev.tool_call_id] = trace
                if ev.tool_call_id not in tool_order:
                    tool_order.append(ev.tool_call_id)
                if ev.tool_name == "subagent":
                    args = ev.tool_arguments
                    subagent_traces.append(
                        {
                            "call_id": ev.tool_call_id,
                            "action": str(args.get("action") or "spawn"),
                            "role": str(args.get("role") or "explorer"),
                            "agent_id": str(args.get("agent_id") or ""),
                            "description": str(
                                args.get("description") or args.get("task") or ""
                            )[:80],
                            "status": "running",
                        }
                    )
                payload.update(
                    {
                        "tool_call_id": ev.tool_call_id,
                        "tool_name": ev.tool_name,
                        "tool_arguments": ev.tool_arguments,
                        "requires_approval": ev.requires_approval,
                        "request_hash": ev.approval_request_hash,
                    }
                )
            elif ev.type == "tool.completed":
                res = ev.tool_result
                content = res.content if res is not None else ""
                is_err = res.is_error if res is not None else False
                if ev.tool_call_id in tool_traces:
                    tool_traces[ev.tool_call_id]["status"] = (
                        "error" if is_err else "completed"
                    )
                    tool_traces[ev.tool_call_id]["result"] = content
                    tool_traces[ev.tool_call_id]["is_error"] = is_err
                if ev.tool_name == "subagent":
                    for item in reversed(subagent_traces):
                        if item["call_id"] == ev.tool_call_id:
                            item["status"] = "failed" if is_err else "completed"
                            item["summary"] = content[:240]
                            break
                if not is_err:
                    newly_verified = extract_verified_media_urls(ev.tool_name, content)
                    if ev.tool_name == "search_images" and search_images_tool_ref:
                        newly_verified |= search_images_tool_ref[0].verified_urls
                    if newly_verified:
                        active_run.verified_media_urls.update(newly_verified)
                payload.update(
                    {
                        "tool_call_id": ev.tool_call_id,
                        "tool_name": ev.tool_name,
                        "tool_arguments": ev.tool_arguments,
                        "result": content,
                        "is_error": is_err,
                        **(
                            {"verified_media_urls": sorted(active_run.verified_media_urls)}
                            if active_run.verified_media_urls
                            else {}
                        ),
                    }
                )
            elif ev.type == "safety.blocked":
                safety_blocked = True
                safety_category = ev.safety_category
                text_chunks.clear()
                text_chunks.append(ev.text)
                payload.update(
                    {
                        "text": ev.text,
                        "safety_code": ev.safety_code,
                        "safety_category": ev.safety_category,
                    }
                )
            elif ev.type in {"run.completed", "run.failed"}:
                result = ev.result
                if result is not None:
                    final_reason = result.reason
                    final_turns = result.turns
                    final_usage = dict(result.usage)
                    final_safety_usage = dict(result.safety_usage)
                    final_history = list(result.history)
                    if result.error is not None:
                        final_error = str(result.error)
                    if result.plan is not None:
                        latest_plan = serialize_plan(result.plan)
                payload.update(
                    {
                        "reason": final_reason,
                        "turns": final_turns,
                        "usage": final_usage,
                        "safety_usage": final_safety_usage,
                        "error": final_error,
                        "plan": latest_plan,
                        "duration_ms": int((time.perf_counter() - start_ts) * 1000),
                        **(
                            {"verified_media_urls": sorted(active_run.verified_media_urls)}
                            if active_run.verified_media_urls and not safety_blocked
                            else {}
                        ),
                    }
                )
            return payload

        async def produce() -> None:
            nonlocal model_gateway, reviewer, final_error, final_reason
            try:
                model_kwargs: dict[str, Any] = {
                    "provider": provider_id,
                    "model_name": model_name,
                    "api_key": req.api_key,
                    "thinking_enabled": effective_thinking_enabled,
                    "reasoning_effort": reasoning_effort,
                }
                reviewer_kwargs: dict[str, Any] = {
                    "provider": provider_id,
                    "model_name": model_name,
                    "api_key": req.api_key,
                    "enabled": req.safety_enabled,
                }
                if provider_cfg.protocol == "openai_responses":
                    model_kwargs["chatgpt_auth"] = self.chatgpt_auth
                    reviewer_kwargs["chatgpt_auth"] = self.chatgpt_auth
                model_gateway = self._model_factory(**model_kwargs)
                reviewer = self._reviewer_factory(**reviewer_kwargs)
                deepseek_tool_key = (
                    req.search_api_key
                    or (req.api_key if provider_id == "deepseek" else None)
                )

                # 按用户勾选动态构造知识库只读检索工具；不勾选则完全不挂载
                knowledge_tool: SearchKnowledgeBaseTool | None = None
                effective_query = req.query
                if active_kbs and self.knowledge_store is not None:
                    embedder_by_spec: dict[
                        tuple[str, str, str, int], EmbeddingProvider
                    ] = {}
                    embedder_map: dict[str, EmbeddingProvider] = {}
                    embed_key = req.embedding_api_key or (
                        req.api_key if provider_id == "gemini" else None
                    )
                    for kb in active_kbs:
                        spec = (
                            kb.embedding_provider,
                            kb.embedding_model,
                            kb.embedding_base_url,
                            kb.embedding_dim,
                        )
                        emb = embedder_by_spec.get(spec)
                        if emb is None:
                            emb = self._embedding_factory(
                                provider=kb.embedding_provider,
                                model_name=kb.embedding_model,
                                dimensions=kb.embedding_dim or None,
                                base_url=kb.embedding_base_url or None,
                                api_key=embed_key,
                            )
                            embedder_by_spec[spec] = emb
                            embedders_to_close.append(emb)
                        embedder_map[kb.kb_id] = emb

                    knowledge_tool = SearchKnowledgeBaseTool(
                        store=self.knowledge_store,
                        kb_ids=active_kb_ids,
                        embedder_map=embedder_map,
                    )
                    effective_query = (
                        f"{_build_knowledge_notice(active_kbs)}\n\n{req.query}"
                    )

                registry = create_web_tool_registry(
                    target_workspace,
                    deepseek_api_key=deepseek_tool_key,
                    serper_api_key=req.serper_api_key,
                    tavily_api_key=req.tavily_api_key,
                    knowledge_tool=knowledge_tool,
                    presentation_tool=presentation_tool,
                    image_tool=image_tool,
                    default_timezone=req.timezone or "UTC",
                )
                registered_img_search = registry.get("search_images")
                if isinstance(registered_img_search, SearchImagesTool):
                    search_images_tool_ref.append(registered_img_search)
                # 将子代理、规划、反思及工作区工具（及已勾选的知识库工具）开放给 Manager
                allowed_tools = frozenset(registry.keys())
                effective_max_tokens = resolve_model_max_tokens(
                    provider_cfg,
                    model_name,
                    req.max_tokens,
                    thinking_enabled=effective_thinking_enabled,
                )
                base_prompt = req.system_prompt_override
                if base_prompt is None:
                    base_prompt = settings.system_prompt
                run_prompt = (
                    base_prompt
                    + "\n\n"
                    + self.rich_output_skill
                    + "\n\n"
                    + self.presentation_skill
                )
                if image_tool is not None:
                    run_prompt += (
                        "\n\n用户明确要求画图、生成海报或插图成品时，调用 generate_image，"
                        "保留用户要求出现在图里的原文。工具直接执行，成功后引导用户查看聊天中的图片。"
                        "普通图片理解、提示词撰写与讨论照常处理。工具仅支持文生图。"
                        "若生图失败，本轮应说明实际结果。"
                    )
                    if req.image_options is not None:
                        run_prompt += "\n用户本轮在输入框选中了生成图片，请将有效的图片描述交给 generate_image。"
                    if selected_ratio is not None:
                        run_prompt += f"用户在界面选定了 {selected_ratio}，生成时以此宽高比为准。"
                runner = create_runner(
                    model=model_gateway,
                    tool_registry=registry,
                    allowed_tool_names=allowed_tools,
                    system_prompt=run_prompt,
                    max_tokens=effective_max_tokens,
                    plan_and_execute=req.plan_and_execute,
                    workspace=target_workspace,
                )
                coordinator = RunCoordinator(runner, reviewer, settings.safety)
                prior_history = deserialize_contents(record.history)
                attachment_parts = await _build_attachment_parts(
                    req.attachments,
                    provider_cfg=provider_cfg,
                    model_name=model_name,
                )

                await event_queue.put(
                    {
                        "type": "run.started",
                        "run_id": run_id,
                        "session_id": record.session_id,
                        "provider": provider_id,
                        "model_name": model_name,
                        "workspace": str(target_workspace or ""),
                        "knowledge_base_ids": active_kb_ids,
                        "reasoning_effort": reasoning_effort,
                        "mode": mode,
                        "safety_enabled": req.safety_enabled,
                        "user_message_id": user_msg_id,
                        "assistant_message_id": assistant_msg_id,
                    }
                )

                async with aclosing(
                    coordinator.run_turn(
                        history=prior_history,
                        user_query=effective_query,
                        permission_check=web_permission_check,
                        run_control=control,
                        user_context_parts=attachment_parts,
                    )
                ) as stream:
                    async for loop_event in stream:
                        await event_queue.put(handle_loop_event(loop_event))
            except Exception as exc:  # noqa: BLE001 - 将未知错误转换为终态事件
                final_reason = "error"
                final_error = str(exc)
                await event_queue.put(
                    {
                        "type": "run.failed",
                        "run_id": run_id,
                        "reason": "error",
                        "error": final_error,
                        "duration_ms": int((time.perf_counter() - start_ts) * 1000),
                    }
                )
            finally:
                await event_queue.put(None)

        producer_task = asyncio.create_task(produce(), name=f"web-run-{run_id}")
        active_run.producer_task = producer_task
        try:
            while True:
                try:
                    item = await asyncio.wait_for(event_queue.get(), timeout=15.0)
                except TimeoutError:
                    yield {"type": "ping", "run_id": run_id}
                    continue
                if item is None:
                    break
                has_presentations = presentation_tool is not None and bool(presentation_tool.artifacts)
                has_images = image_tool is not None and bool(image_tool.artifacts)
                if (
                    not safety_blocked
                    and (
                        (item["type"] == "run.completed" and (has_presentations or has_images))
                        or (item["type"] == "run.failed" and has_images)
                    )
                ):
                    # 成品卡出现前，先保证下载所需的会话记录已保存。
                    deferred_completion = item
                    continue
                yield item
        finally:
            try:
                if deferred_completion is not None and not active_run.stopping:
                    if (
                        deferred_completion["type"] == "run.completed"
                        and presentation_tool is not None
                        and presentation_tool.artifacts
                    ):
                        artifacts["presentations"] = [
                            artifact.to_message() for artifact in presentation_tool.artifacts
                        ]
                    if image_tool is not None and image_tool.artifacts:
                        artifacts["images"] = [
                            artifact.to_message(record.session_id) for artifact in image_tool.artifacts
                        ]
                active_run.cancel_all()
                if producer_task is not None and not producer_task.done():
                    producer_task.cancel()
                if producer_task is not None:
                    await asyncio.gather(producer_task, return_exceptions=True)

                if model_gateway is not None:
                    await model_gateway.close()
                for emb in embedders_to_close:
                    await emb.close()
                close_reviewer = getattr(reviewer, "close", None)
                if callable(close_reviewer):
                    maybe = close_reviewer()
                    if hasattr(maybe, "__await__"):
                        await cast(Any, maybe)

                if active_run.stopping and not final_error and final_history is None:
                    final_reason = "error"
                    final_error = "运行已由用户手动中止。"

                # 持久化会话状态与完整前端展示记录
                record.provider = provider_id
                record.model_name = model_name
                record.mode = mode
                if target_workspace is not None:
                    record.workspace = str(target_workspace)
                record.knowledge_base_ids = active_kb_ids
                if record.title == "新会话" and req.query.strip():
                    record.title = req.query.strip()[:28]
                if final_history is not None:
                    record.history = serialize_contents(final_history)
                for key in ("input_len", "output_len", "cached_len"):
                    val = final_usage.get(key, 0)
                    if isinstance(val, int):
                        record.usage[key] = record.usage.get(key, 0) + val

                record.ui_messages.append(
                    {
                        "id": user_msg_id,
                        "role": "user",
                        "text": req.query,
                        "attachments": [
                            {
                                "name": a.name,
                                "mime_type": a.mime_type,
                                "kind": a.kind,
                            }
                            for a in req.attachments
                        ],
                    }
                )
                record.ui_messages.append(
                    {
                        "id": assistant_msg_id,
                        "role": "assistant",
                        "provider": provider_id,
                        "model_name": model_name,
                        "mode": mode,
                        "thinking": "".join(thinking_chunks),
                        "text": "".join(text_chunks),
                        "tool_calls": [tool_traces[cid] for cid in tool_order],
                        **(
                            {"verified_media_urls": sorted(active_run.verified_media_urls)}
                            if active_run.verified_media_urls and not safety_blocked
                            else {}
                        ),
                        **artifacts,
                        "subagents": subagent_traces,
                        "plan": latest_plan,
                        "usage": final_usage,
                        "safety_usage": final_safety_usage,
                        "safety_blocked": safety_blocked,
                        "safety_category": safety_category,
                        "reason": final_reason,
                        "error": final_error,
                        "turns": final_turns,
                        "duration_ms": int((time.perf_counter() - start_ts) * 1000),
                    }
                )
                self.store.save(record)
            finally:
                if self._active_runs.get(record.session_id) is active_run:
                    self._active_runs.pop(record.session_id, None)
                active_run.done_event.set()
        if deferred_completion is not None:
            yield {**deferred_completion, **artifacts}
