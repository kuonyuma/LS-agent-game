from collections.abc import AsyncGenerator
from typing import Any, Literal, cast

from google.genai import types
from openai import APIConnectionError, APIStatusError, AsyncOpenAI, AuthenticationError

from agent_service.adapters.openai_compat.translator import (
    contents_to_openai_messages,
    parse_tool_arguments,
    tools_to_openai,
)
from agent_service.config.settings import ProviderConfig, resolve_api_key
from agent_service.errors import AgentRuntimeError, ConfigurationError
from agent_service.kernel.model.port import StreamEvent, StreamResult

ReasoningEffort = Literal["off", "low", "medium", "high", "xhigh", "max"]


def _normalize_openai_effort(level: str) -> Literal["low", "high", "max"]:
    cleaned = level.strip().lower()
    if cleaned == "low":
        return "low"
    if cleaned in {"max", "xhigh"}:
        return "max"
    return "high"


class OpenAICompatibleModel:
    """基于官方 OpenAI SDK 适配 DeepSeek、Kimi 等 OpenAI 协议模型。"""

    def __init__(
        self,
        provider: ProviderConfig,
        *,
        model_name: str | None = None,
        api_key: str | None = None,
        thinking_enabled: bool = True,
        reasoning_effort: ReasoningEffort = "high",
        client: AsyncOpenAI | None = None,
    ) -> None:
        self.provider = provider
        self.model_name = (model_name or provider.default_model).strip()
        self._api_key = api_key
        self.thinking_enabled = thinking_enabled
        self.reasoning_effort = reasoning_effort
        self._external_client = client is not None
        self._client: AsyncOpenAI | None = client

    def _ensure_client(self) -> AsyncOpenAI:
        if self._client is not None:
            return self._client
        key = resolve_api_key(self.provider, self._api_key)
        if not key:
            env_hint = (
                f"请配置 {self.provider.env_key} 环境变量"
                if self.provider.env_key
                else "请提供 API Key"
            )
            raise ConfigurationError(
                f"{env_hint}，或在设置中填入 {self.provider.label} 的 API Key"
            )
        self._client = AsyncOpenAI(
            api_key=key,
            base_url=self.provider.base_url or None,
        )
        return self._client

    def _build_extra_body(self) -> dict[str, Any]:
        extra: dict[str, Any] = {}
        thinking_off = (not self.thinking_enabled) or self.reasoning_effort == "off"
        if self.provider.thinking_mode == "toggleable":
            if not thinking_off:
                extra["thinking"] = {"type": "enabled"}
                extra["reasoning_effort"] = _normalize_openai_effort(
                    self.reasoning_effort
                )
            else:
                extra["thinking"] = {"type": "disabled"}
        elif self.provider.thinking_mode == "always_on":
            effort = (
                "low"
                if thinking_off
                else _normalize_openai_effort(self.reasoning_effort)
            )
            extra["reasoning_effort"] = effort
        return extra

    async def stream(
        self,
        *,
        contents: list[types.Content],
        system_prompt: str,
        max_tokens: int,
        tools: list[types.Tool] | None = None,
    ) -> AsyncGenerator[StreamEvent]:
        client = self._ensure_client()
        messages = contents_to_openai_messages(contents, system_prompt)
        openai_tools = tools_to_openai(tools)

        request_kwargs: dict[str, Any] = {
            "model": self.model_name,
            "messages": cast(Any, messages),
            "max_tokens": max_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if openai_tools:
            request_kwargs["tools"] = cast(Any, openai_tools)
        extra_body = self._build_extra_body()
        if extra_body:
            request_kwargs["extra_body"] = extra_body

        try:
            response = await client.chat.completions.create(**request_kwargs)
        except AuthenticationError as exc:
            raise ConfigurationError(
                f"{self.provider.label} API Key 鉴权失败，请检查密钥是否有效。"
            ) from exc
        except APIConnectionError as exc:
            raise AgentRuntimeError(
                f"无法连接 {self.provider.label} 接口（{self.provider.base_url}）：{exc}"
            ) from exc
        except APIStatusError as exc:
            raise AgentRuntimeError(
                f"{self.provider.label} 接口返回错误（HTTP {exc.status_code}）：{exc.message}"
            ) from exc

        yield StreamEvent(type="message_start")

        reasoning_parts: list[str] = []
        text_parts: list[str] = []
        tool_accumulators: dict[int, dict[str, Any]] = {}
        input_tokens = 0
        output_tokens = 0
        cached_tokens = 0
        has_cached_tokens = False
        stop_reason = "message_done"

        try:
            async for chunk in response:
                usage = getattr(chunk, "usage", None)
                if usage is not None:
                    prompt_tokens = getattr(usage, "prompt_tokens", None)
                    completion_tokens = getattr(usage, "completion_tokens", None)
                    if isinstance(prompt_tokens, int):
                        input_tokens = prompt_tokens
                    if isinstance(completion_tokens, int):
                        output_tokens = completion_tokens
                    cache_hit = getattr(usage, "prompt_cache_hit_tokens", None)
                    if not isinstance(cache_hit, int):
                        details = getattr(usage, "prompt_tokens_details", None)
                        cache_hit = getattr(details, "cached_tokens", None)
                    if isinstance(cache_hit, int):
                        cached_tokens = cache_hit
                        has_cached_tokens = True

                choices = getattr(chunk, "choices", None) or []
                if not choices:
                    continue
                choice = choices[0]
                finish = getattr(choice, "finish_reason", None)
                if finish:
                    stop_reason = str(finish)

                delta = getattr(choice, "delta", None)
                if delta is None:
                    continue

                reasoning_delta = getattr(delta, "reasoning_content", None)
                if isinstance(reasoning_delta, str) and reasoning_delta:
                    reasoning_parts.append(reasoning_delta)
                    yield StreamEvent(type="thinking", text=reasoning_delta)

                content_delta = getattr(delta, "content", None)
                if isinstance(content_delta, str) and content_delta:
                    text_parts.append(content_delta)
                    yield StreamEvent(type="text", text=content_delta)

                delta_tool_calls = getattr(delta, "tool_calls", None) or []
                for tc in delta_tool_calls:
                    index = int(getattr(tc, "index", 0) or 0)
                    acc = tool_accumulators.setdefault(
                        index,
                        {"id": "", "name": "", "arguments": [], "notified": False},
                    )
                    tc_id = getattr(tc, "id", None)
                    if isinstance(tc_id, str) and tc_id:
                        acc["id"] = tc_id
                    fn = getattr(tc, "function", None)
                    if fn is not None:
                        fn_name = getattr(fn, "name", None)
                        if isinstance(fn_name, str) and fn_name:
                            acc["name"] += fn_name
                        fn_args = getattr(fn, "arguments", None)
                        if isinstance(fn_args, str) and fn_args:
                            acc["arguments"].append(fn_args)
                    if acc["name"] and not acc["notified"]:
                        acc["notified"] = True
                        yield StreamEvent(
                            type="tool_use_start",
                            id=str(acc["id"]),
                            name=str(acc["name"]),
                        )
        except APIStatusError as exc:
            raise AgentRuntimeError(
                f"{self.provider.label} 流式响应中断（HTTP {exc.status_code}）：{exc.message}"
            ) from exc
        finally:
            close_fn = getattr(response, "close", None) or getattr(
                response, "aclose", None
            )
            if callable(close_fn):
                maybe_awaitable = close_fn()
                if hasattr(maybe_awaitable, "__await__"):
                    await cast(Any, maybe_awaitable)

        full_reasoning = "".join(reasoning_parts)
        full_text = "".join(text_parts)
        calls: list[types.FunctionCall] = []
        raw_parts: list[types.Part] = []

        if full_reasoning:
            raw_parts.append(types.Part(text=full_reasoning, thought=True))
        if full_text:
            raw_parts.append(types.Part.from_text(text=full_text))

        for index in sorted(tool_accumulators):
            acc = tool_accumulators[index]
            name = str(acc["name"]).strip()
            raw_args = "".join(acc["arguments"])
            parsed_args = parse_tool_arguments(raw_args, name or f"tool_{index}")
            fc = types.FunctionCall(
                id=str(acc["id"]) or None,
                name=name,
                args=parsed_args,
            )
            calls.append(fc)
            raw_parts.append(types.Part(function_call=fc))

        usage_payload: dict[str, Any] = {
            "input_len": input_tokens,
            "output_len": output_tokens,
        }
        if has_cached_tokens:
            usage_payload["cached_len"] = cached_tokens

        result = StreamResult(
            contents=full_text,
            stop_reason="tool_use" if calls else stop_reason,
            usage=usage_payload,
            function_calls=calls,
            raw_parts=raw_parts,
        )
        yield StreamEvent(type="message_done", result=result)

    async def close(self) -> None:
        if self._client is not None and not self._external_client:
            client = self._client
            self._client = None
            await client.close()
