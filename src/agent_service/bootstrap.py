"""唯一组装入口：选择外部实现，向内核注入模型、工具表和按次绑定工厂。"""

from collections.abc import AsyncGenerator, Mapping
from pathlib import Path
from typing import Any, cast

from google.genai import types

from agent_service.adapters.cli.app import App
from agent_service.adapters.collaboration.bindings import ToolBindingFactory
from agent_service.adapters.gemini.model import GeminiModel
from agent_service.adapters.openai_compat.model import (
    OpenAICompatibleModel,
    ReasoningEffort,
)
from agent_service.adapters.openai_compat.reviewer import OpenAICompatibleTextReviewer
from agent_service.adapters.openai_responses.auth import ChatGPTAuthManager
from agent_service.adapters.openai_responses.model import OpenAIResponsesModel
from agent_service.adapters.openai_responses.reviewer import (
    OpenAIResponsesTextReviewer,
)
from agent_service.adapters.security.coordinator import RunCoordinator
from agent_service.adapters.security.reviewer import (
    GeminiTextReviewer,
    PassThroughTextReviewer,
    TextReviewer,
)
from agent_service.adapters.security.workspace import WorkspacePathPolicy
from agent_service.adapters.tools.index import create_tool_registry
from agent_service.config.settings import (
    ProviderConfig,
    resolve_model_thinking_level,
    settings,
)
from agent_service.errors import ConfigurationError
from agent_service.kernel.dispatch.base import Tool
from agent_service.kernel.model.port import ModelGateway
from agent_service.kernel.runtime.events import LoopEvent
from agent_service.kernel.runtime.loop import query
from agent_service.kernel.runtime.runner import AgentRunner


def get_provider_config(provider_id: str | None = None) -> ProviderConfig:
    target = (provider_id or settings.model_config.provider).strip().lower()
    provider = settings.providers.get(target)
    if provider is None:
        supported = ", ".join(sorted(settings.providers))
        raise ConfigurationError(f"未知的模型厂商 `{target}`，当前支持：{supported}")
    return provider


def create_model_gateway(
    *,
    provider: str | None = None,
    model_name: str | None = None,
    api_key: str | None = None,
    chatgpt_auth: ChatGPTAuthManager | None = None,
    thinking_enabled: bool = True,
    reasoning_effort: ReasoningEffort = "high",
) -> ModelGateway:
    """按厂商配置构造模型适配器；内核只面向 ModelGateway 协议编程。"""

    provider_cfg = get_provider_config(provider)
    active_model = (
        model_name.strip()
        if isinstance(model_name, str) and model_name.strip()
        else provider_cfg.default_model
    )
    resolved_level = resolve_model_thinking_level(
        provider_cfg,
        active_model,
        reasoning_effort,
        thinking_enabled=thinking_enabled,
    )
    if provider_cfg.protocol == "gemini":
        return GeminiModel(
            model_name=active_model,
            api_key=api_key,
            thinking_level=resolved_level,
        )
    if provider_cfg.protocol == "openai_responses":
        return OpenAIResponsesModel(
            provider_cfg,
            model_name=active_model,
            api_key=api_key,
            auth_manager=chatgpt_auth,
            thinking_enabled=resolved_level != "off",
            reasoning_effort=cast(ReasoningEffort, resolved_level),
        )
    return OpenAICompatibleModel(
        provider_cfg,
        model_name=active_model,
        api_key=api_key,
        thinking_enabled=resolved_level != "off",
        reasoning_effort=cast(ReasoningEffort, resolved_level),
    )


def create_text_reviewer(
    *,
    provider: str | None = None,
    model_name: str | None = None,
    api_key: str | None = None,
    chatgpt_auth: ChatGPTAuthManager | None = None,
    enabled: bool = True,
) -> TextReviewer:
    """构造与当前模型厂商匹配的输出安全审核器。"""

    if not enabled:
        return PassThroughTextReviewer()
    provider_cfg = get_provider_config(provider)
    active_model = (
        model_name.strip()
        if isinstance(model_name, str) and model_name.strip()
        else provider_cfg.default_model
    )
    if provider_cfg.protocol == "gemini":
        return GeminiTextReviewer(model_name=active_model, api_key=api_key)
    if provider_cfg.protocol == "openai_responses":
        return OpenAIResponsesTextReviewer(
            provider_cfg,
            model_name=active_model,
            api_key=api_key,
            auth_manager=chatgpt_auth,
        )
    return OpenAICompatibleTextReviewer(
        provider_cfg,
        model_name=active_model,
        api_key=api_key,
    )


def create_runner(
    *,
    model: ModelGateway | None = None,
    provider: str | None = None,
    model_name: str | None = None,
    api_key: str | None = None,
    thinking_enabled: bool = True,
    reasoning_effort: ReasoningEffort = "high",
    tools: list[types.Tool] | None = None,
    tool_registry: Mapping[str, Tool] | None = None,
    allowed_tool_names: frozenset[str] | None = None,
    system_prompt: str | None = None,
    max_turns: int | None = None,
    max_tokens: int | None = None,
    plan_and_execute: bool = True,
    workspace: Path | None = None,
) -> AgentRunner:
    gateway = (
        model
        if model is not None
        else create_model_gateway(
            provider=provider,
            model_name=model_name,
            api_key=api_key,
            thinking_enabled=thinking_enabled,
            reasoning_effort=reasoning_effort,
        )
    )
    token_limit = (
        max_tokens if max_tokens is not None else settings.model_config.max_tokens
    )

    def child_query(**kwargs: Any) -> AsyncGenerator[LoopEvent]:
        return query(
            model=gateway, max_tokens=token_limit, binding_factory=bindings, **kwargs
        )

    bindings = ToolBindingFactory(child_query)
    registry = tool_registry
    if registry is None:
        registry = create_tool_registry(WorkspacePathPolicy(workspace or Path.cwd()))
    return AgentRunner(
        model=gateway,
        binding_factory=bindings,
        tool_registry=registry,
        tools=tools,
        allowed_tool_names=allowed_tool_names,
        system_prompt=system_prompt
        if system_prompt is not None
        else settings.system_prompt,
        max_turns=max_turns,
        max_tokens=token_limit,
        plan_and_execute=plan_and_execute,
    )


def create_app(runner: AgentRunner, reviewer: TextReviewer | None = None) -> App:
    return App(RunCoordinator(runner, reviewer, settings.safety))
