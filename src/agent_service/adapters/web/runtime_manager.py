import asyncio
import base64
import binascii
import time
from collections.abc import AsyncGenerator, Callable
from contextlib import aclosing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from google.genai import types

from agent_service.adapters.openai_compat.model import ReasoningEffort
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
from agent_service.adapters.tools.index import create_tool_registry
from agent_service.adapters.tools.knowledge_tool import SearchKnowledgeBaseTool
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
from agent_service.kernel.model.port import ModelGateway
from agent_service.kernel.runtime.control import RunControl
from agent_service.kernel.runtime.events import LoopEvent
from agent_service.kernel.strategy.plan import ExecutionPlan

ModelFactory = Callable[..., ModelGateway]
ReviewerFactory = Callable[..., TextReviewer]
EmbeddingFactory = Callable[..., EmbeddingProvider]

_MAX_TEXT_ATTACHMENT_CHARS = 120_000


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


def _build_user_history_prefix(
    history: list[types.Content],
    attachments: list[RunAttachment],
    *,
    provider_cfg: ProviderConfig,
    model_name: str,
) -> tuple[list[types.Content], str]:
    """将用户附带的图片、PDF 或文本代码文件转为多模态或结构化上下文。"""

    if not attachments:
        return history, ""

    binary_parts: list[types.Part] = []
    text_blocks: list[str] = []
    attached_names: list[str] = []
    supports_vision = _model_supports_vision(provider_cfg, model_name)

    for att in attachments:
        mime = (att.mime_type or "").strip().lower()
        is_image = att.kind == "image" or mime.startswith("image/")

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
        if text_body is None and att.data_base64:
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
            truncated = text_body[:_MAX_TEXT_ATTACHMENT_CHARS]
            suffix = (
                "\n[文件内容已按长度上限截断]"
                if len(text_body) > _MAX_TEXT_ATTACHMENT_CHARS
                else ""
            )
            text_blocks.append(
                f"<attached-file name=\"{att.name}\" mime=\"{mime or 'text/plain'}\">\n"
                f"{truncated}{suffix}\n"
                "</attached-file>"
            )
            attached_names.append(att.name)

    if not binary_parts and not text_blocks:
        return history, ""

    user_parts: list[types.Part] = [*binary_parts]
    summary_header = f"[用户附加了 {len(attached_names)} 个附件：{', '.join(attached_names)}]"
    if text_blocks:
        user_parts.append(
            types.Part.from_text(text=summary_header + "\n\n" + "\n\n".join(text_blocks))
        )
    else:
        user_parts.append(types.Part.from_text(text=summary_header))

    merged_history = [
        *history,
        types.Content(role="user", parts=user_parts),
        types.Content(
            role="model",
            parts=[types.Part.from_text(text="已收到你提供的附件，请说明你的问题或任务。")],
        ),
    ]
    return merged_history, f"（附 {len(attached_names)} 个附件）"


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
    done_event: asyncio.Event = field(default_factory=asyncio.Event)
    producer_task: asyncio.Task[None] | None = None
    stopping: bool = False
    pending_approvals: dict[str, PendingApproval] = field(default_factory=dict)

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
        workspace: Path,
        knowledge_store: KnowledgeStore | None = None,
        model_factory: ModelFactory = create_model_gateway,
        reviewer_factory: ReviewerFactory = create_text_reviewer,
        embedding_factory: EmbeddingFactory = create_embedding_provider,
    ) -> None:
        self.store = store
        self.workspace = workspace
        self.knowledge_store = knowledge_store
        self._model_factory = model_factory
        self._reviewer_factory = reviewer_factory
        self._embedding_factory = embedding_factory
        self._active_runs: dict[str, ActiveRun] = {}

    def has_active_run(self, session_id: str) -> bool:
        active = self._active_runs.get(session_id)
        return active is not None and not active.stopping

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
    ) -> Path:
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
        record: SessionRecord,
        req: RunTurnRequest,
    ) -> AsyncGenerator[dict[str, Any]]:
        existing = self._active_runs.get(record.session_id)
        if existing is not None:
            if existing.stopping:
                try:
                    await asyncio.wait_for(existing.done_event.wait(), timeout=5.0)
                except TimeoutError:
                    if self._active_runs.get(record.session_id) is existing:
                        self._active_runs.pop(record.session_id, None)
            if record.session_id in self._active_runs:
                yield {
                    "type": "run.failed",
                    "error": "当前会话已有任务正在运行，请等待完成或先点击停止。",
                }
                return

        provider_id = (
            (req.provider or record.provider or settings.model_config.provider)
            .strip()
            .lower()
        )
        mode: SessionMode = "multi"

        try:
            provider_cfg = get_provider_config(provider_id)
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
            except (TimeoutError, asyncio.CancelledError):
                active_run.pending_approvals.pop(request.call_id, None)
                return False

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
                payload.update(
                    {
                        "tool_call_id": ev.tool_call_id,
                        "tool_name": ev.tool_name,
                        "tool_arguments": ev.tool_arguments,
                        "result": content,
                        "is_error": is_err,
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
                    }
                )
            return payload

        async def produce() -> None:
            nonlocal model_gateway, reviewer, final_error, final_reason
            try:
                model_gateway = self._model_factory(
                    provider=provider_id,
                    model_name=model_name,
                    api_key=req.api_key,
                    thinking_enabled=effective_thinking_enabled,
                    reasoning_effort=reasoning_effort,
                )
                reviewer = self._reviewer_factory(
                    provider=provider_id,
                    model_name=model_name,
                    api_key=req.api_key,
                    enabled=req.safety_enabled,
                )
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

                workspace_policy = WorkspacePathPolicy(target_workspace)
                registry = create_tool_registry(
                    workspace_policy,
                    deepseek_api_key=deepseek_tool_key,
                    knowledge_tool=knowledge_tool,
                )
                # 将子代理、规划、反思及工作区工具（及已勾选的知识库工具）开放给 Manager
                allowed_tools = frozenset(registry.keys())
                effective_max_tokens = resolve_model_max_tokens(
                    provider_cfg,
                    model_name,
                    req.max_tokens,
                    thinking_enabled=effective_thinking_enabled,
                )
                runner = create_runner(
                    model=model_gateway,
                    tool_registry=registry,
                    allowed_tool_names=allowed_tools,
                    system_prompt=req.system_prompt_override,
                    max_tokens=effective_max_tokens,
                    plan_and_execute=req.plan_and_execute,
                    workspace=target_workspace,
                )
                coordinator = RunCoordinator(runner, reviewer, settings.safety)
                prior_history = deserialize_contents(record.history)
                prior_history, _ = _build_user_history_prefix(
                    prior_history,
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
                        "workspace": str(target_workspace),
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
                yield item
        finally:
            try:
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
