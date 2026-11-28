"""基于 OpenAI Responses API（/v1/responses）的流式模型适配器，支持 ChatGPT 登录与 API Key 兜底。"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any, cast

from google.genai import types
from openai import APIConnectionError, APIStatusError, AsyncOpenAI, AuthenticationError

from agent_service.adapters.openai_compat.model import ReasoningEffort
from agent_service.adapters.openai_compat.translator import parse_tool_arguments
from agent_service.adapters.openai_responses.auth import (
    CHATGPT_USAGE_SETTINGS_URL,
    ChatGPTAuthManager,
)
from agent_service.adapters.openai_responses.translator import (
    contents_to_responses_input,
    encode_openai_reasoning_signature,
    tools_to_responses,
)
from agent_service.config.settings import ProviderConfig, resolve_api_key
from agent_service.errors import AgentRuntimeError, ConfigurationError
from agent_service.kernel.model.port import StreamEvent, StreamResult


def _normalize_responses_effort(level: str) -> str:
    cleaned = level.strip().lower()
    if cleaned in {"low", "medium", "high", "xhigh"}:
        return cleaned
    if cleaned == "max":
        return "xhigh"
    return "high"


def _extract_error_code(exc: APIStatusError) -> str:
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            code = err.get("code") or err.get("type")
            if code:
                return str(code)
        code = body.get("code")
        if code:
            return str(code)
    code_attr = getattr(exc, "code", None)
    return str(code_attr or "")


class OpenAIResponsesModel:
    """适配 OpenAI Responses API，优先走 ChatGPT OAuth 套餐额度，支持 OPENAI_API_KEY 兜底。"""

    def __init__(
        self,
        provider: ProviderConfig,
        *,
        model_name: str | None = None,
        api_key: str | None = None,
        auth_manager: ChatGPTAuthManager | None = None,
        thinking_enabled: bool = True,
        reasoning_effort: ReasoningEffort = "high",
        client: AsyncOpenAI | None = None,
    ) -> None:
        self.provider = provider
        self.model_name = (model_name or provider.default_model).strip()
        self._api_key = api_key.strip() if isinstance(api_key, str) and api_key.strip() else None
        self._auth_manager = auth_manager
        self.thinking_enabled = thinking_enabled
        self.reasoning_effort = reasoning_effort
        self._external_client = client is not None
        self._client: AsyncOpenAI | None = client
        self._active_token: str = ""

    async def _resolve_credentials(self) -> tuple[str, bool]:
        """解析凭据并返回 (bearer_token, via_chatgpt_oauth)。"""

        if self._api_key:
            return self._api_key, False
        if self._auth_manager is not None and self._auth_manager.is_connected():
            token = await self._auth_manager.get_valid_access_token()
            return token, True
        env_key = resolve_api_key(self.provider)
        if env_key:
            return env_key, False
        if self._auth_manager is None:
            raise ConfigurationError("未配置 OPENAI_API_KEY。请在设置中填入 API Key。")
        raise ConfigurationError(
            "尚未登录 ChatGPT，也未配置 OPENAI_API_KEY。请在设置中点击「使用 ChatGPT 登录」或填入 API Key。"
        )

    async def _ensure_client(self) -> tuple[AsyncOpenAI, bool]:
        if self._external_client and self._client is not None:
            via_oauth = bool(
                self._api_key is None
                and self._auth_manager is not None
                and self._auth_manager.is_connected()
            )
            return self._client, via_oauth

        token, via_oauth = await self._resolve_credentials()
        if self._client is not None and self._active_token == token:
            return self._client, via_oauth
        if self._client is not None:
            await self._client.close()
        self._client = AsyncOpenAI(
            api_key=token,
            base_url=self.provider.base_url or None,
        )
        self._active_token = token
        return self._client, via_oauth

    def _format_subscription_error(self, code: str, message: str, status_code: int) -> Exception:
        normalized = code.strip().lower()
        if normalized == "subscription_sharing_usage_limit_exceeded":
            return AgentRuntimeError(
                "已达到当前 ChatGPT 套餐额度或应用每周上限，请前往 "
                f"{CHATGPT_USAGE_SETTINGS_URL} 查看或调整额度后再试。"
            )
        if normalized == "subscription_sharing_user_not_eligible":
            return ConfigurationError(
                "当前 ChatGPT 账号暂不支持在第三方应用中使用套餐额度（需 Plus / Pro / Business 等合格套餐）。"
            )
        if normalized == "subscription_sharing_invalid_user":
            if self._auth_manager is not None:
                self._auth_manager.invalidate_tokens()
            return ConfigurationError(
                "ChatGPT 授权态已失效，请在设置中重新点击「使用 ChatGPT 登录」。"
            )
        return AgentRuntimeError(
            f"{self.provider.label} 接口返回错误（HTTP {status_code}）：{message}"
        )

    async def stream(
        self,
        *,
        contents: list[types.Content],
        system_prompt: str,
        max_tokens: int,
        tools: list[types.Tool] | None = None,
    ) -> AsyncGenerator[StreamEvent]:
        client, via_oauth = await self._ensure_client()
        input_items = contents_to_responses_input(contents)
        resp_tools = tools_to_responses(tools)

        request_kwargs: dict[str, Any] = {
            "model": self.model_name,
            "input": cast(Any, input_items),
            "store": False,
            "stream": True,
        }
        if system_prompt.strip():
            request_kwargs["instructions"] = system_prompt
        if resp_tools:
            request_kwargs["tools"] = cast(Any, resp_tools)

        thinking_off = (not self.thinking_enabled) or self.reasoning_effort == "off"
        if not thinking_off:
            request_kwargs["reasoning"] = {
                "effort": _normalize_responses_effort(self.reasoning_effort),
                "summary": "auto",
            }
            request_kwargs["include"] = ["reasoning.encrypted_content"]

        # ChatGPT 套餐直连模式限制自定义 max_output_tokens；仅 API Key 兜底模式传入
        if not via_oauth and max_tokens > 0:
            request_kwargs["max_output_tokens"] = max_tokens

        try:
            response = await client.responses.create(**request_kwargs)
        except AuthenticationError as exc:
            code = _extract_error_code(exc)
            if via_oauth and self._auth_manager is not None:
                self._auth_manager.invalidate_tokens()
                raise ConfigurationError(
                    "ChatGPT 登录态已失效或被撤销，请在设置中重新点击「使用 ChatGPT 登录」。"
                ) from exc
            if code:
                raise self._format_subscription_error(code, exc.message, exc.status_code) from exc
            raise ConfigurationError(
                f"{self.provider.label} API Key 鉴权失败，请检查密钥是否有效。"
            ) from exc
        except APIConnectionError as exc:
            raise AgentRuntimeError(
                f"无法连接 {self.provider.label} 接口（{self.provider.base_url}）：{exc}"
            ) from exc
        except APIStatusError as exc:
            code = _extract_error_code(exc)
            raise self._format_subscription_error(
                code, exc.message, exc.status_code
            ) from exc

        yield StreamEvent(type="message_start")

        reasoning_parts: list[str] = []
        completed_reasoning_items: list[tuple[str, str, str]] = []
        text_parts: list[str] = []
        tool_by_item_id: dict[str, dict[str, Any]] = {}
        tool_order: list[str] = []
        input_tokens = 0
        output_tokens = 0
        cached_tokens = 0
        has_cached_tokens = False
        stop_reason = "message_done"

        try:
            async for event in response:
                ev_type = str(getattr(event, "type", "") or "")

                if ev_type in {
                    "response.reasoning_summary_text.delta",
                    "response.reasoning_text.delta",
                }:
                    delta = getattr(event, "delta", None)
                    if isinstance(delta, str) and delta:
                        reasoning_parts.append(delta)
                        yield StreamEvent(type="thinking", text=delta)
                    continue

                if ev_type == "response.output_text.delta":
                    delta = getattr(event, "delta", None)
                    if isinstance(delta, str) and delta:
                        text_parts.append(delta)
                        yield StreamEvent(type="text", text=delta)
                    continue

                if ev_type == "response.output_item.added":
                    item = getattr(event, "item", None)
                    if item is not None and getattr(item, "type", None) == "function_call":
                        item_id = str(
                            getattr(item, "id", None)
                            or getattr(item, "call_id", None)
                            or f"item_{len(tool_order)}"
                        )
                        call_id = str(getattr(item, "call_id", None) or item_id)
                        name = str(getattr(item, "name", None) or "")
                        entry = tool_by_item_id.setdefault(
                            item_id,
                            {
                                "call_id": call_id,
                                "name": name,
                                "arguments": [],
                                "final_arguments": None,
                                "notified": False,
                            },
                        )
                        if item_id not in tool_order:
                            tool_order.append(item_id)
                        if name and not entry["notified"]:
                            entry["notified"] = True
                            yield StreamEvent(
                                type="tool_use_start",
                                id=call_id,
                                name=name,
                            )
                    continue

                if ev_type == "response.function_call_arguments.delta":
                    item_id = str(getattr(event, "item_id", "") or "")
                    delta = getattr(event, "delta", None)
                    if item_id and isinstance(delta, str) and delta:
                        entry = tool_by_item_id.setdefault(
                            item_id,
                            {
                                "call_id": item_id,
                                "name": "",
                                "arguments": [],
                                "final_arguments": None,
                                "notified": False,
                            },
                        )
                        if item_id not in tool_order:
                            tool_order.append(item_id)
                        entry["arguments"].append(delta)
                    continue

                if ev_type == "response.function_call_arguments.done":
                    item_id = str(getattr(event, "item_id", "") or "")
                    final_args = getattr(event, "arguments", None)
                    if item_id and isinstance(final_args, str):
                        entry = tool_by_item_id.setdefault(
                            item_id,
                            {
                                "call_id": item_id,
                                "name": "",
                                "arguments": [],
                                "final_arguments": None,
                                "notified": False,
                            },
                        )
                        if item_id not in tool_order:
                            tool_order.append(item_id)
                        entry["final_arguments"] = final_args
                    continue

                if ev_type == "response.output_item.done":
                    item = getattr(event, "item", None)
                    if item is None:
                        continue
                    item_type = getattr(item, "type", None)
                    if item_type == "function_call":
                        item_id = str(
                            getattr(item, "id", None)
                            or getattr(item, "call_id", None)
                            or ""
                        )
                        call_id = str(getattr(item, "call_id", None) or item_id)
                        name = str(getattr(item, "name", None) or "")
                        args_str = getattr(item, "arguments", None)
                        if item_id:
                            entry = tool_by_item_id.setdefault(
                                item_id,
                                {
                                    "call_id": call_id,
                                    "name": name,
                                    "arguments": [],
                                    "final_arguments": None,
                                    "notified": False,
                                },
                            )
                            if item_id not in tool_order:
                                tool_order.append(item_id)
                            if call_id:
                                entry["call_id"] = call_id
                            if name:
                                entry["name"] = name
                            if isinstance(args_str, str) and args_str:
                                entry["final_arguments"] = args_str
                    elif item_type == "reasoning":
                        encrypted = str(getattr(item, "encrypted_content", "") or "").strip()
                        r_id = str(getattr(item, "id", "") or "").strip()
                        summary_texts: list[str] = []
                        for s_part in getattr(item, "summary", None) or []:
                            s_text = getattr(s_part, "text", None)
                            if isinstance(s_text, str) and s_text:
                                summary_texts.append(s_text)
                        if encrypted:
                            completed_reasoning_items.append(
                                (r_id, "".join(summary_texts), encrypted)
                            )
                    continue

                if ev_type in {"response.completed", "response.incomplete"}:
                    resp_obj = getattr(event, "response", None)
                    if resp_obj is not None:
                        status = str(getattr(resp_obj, "status", "") or "")
                        stop_reason = "length" if status == "incomplete" else "stop"
                        usage = getattr(resp_obj, "usage", None)
                        if usage is not None:
                            in_tok = getattr(usage, "input_tokens", None)
                            out_tok = getattr(usage, "output_tokens", None)
                            if isinstance(in_tok, int):
                                input_tokens = in_tok
                            if isinstance(out_tok, int):
                                output_tokens = out_tok
                            in_details = getattr(usage, "input_tokens_details", None)
                            c_tok = getattr(in_details, "cached_tokens", None)
                            if isinstance(c_tok, int):
                                cached_tokens = c_tok
                                has_cached_tokens = True
                    continue

                if ev_type == "response.failed":
                    resp_obj = getattr(event, "response", None)
                    err = getattr(resp_obj, "error", None)
                    code = str(getattr(err, "code", "") or "")
                    msg = str(getattr(err, "message", "") or "模型响应失败")
                    raise self._format_subscription_error(code, msg, 429 if "limit" in code else 500)

                if ev_type == "error":
                    code = str(getattr(event, "code", "") or "")
                    msg = str(getattr(event, "message", "") or "流式事件返回错误")
                    raise self._format_subscription_error(code, msg, 429 if "limit" in code else 500)
        except APIStatusError as exc:
            code = _extract_error_code(exc)
            raise self._format_subscription_error(
                code, exc.message, exc.status_code
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

        if completed_reasoning_items:
            for r_id, summary_text, encrypted in completed_reasoning_items:
                raw_parts.append(
                    types.Part(
                        text=summary_text or full_reasoning or None,
                        thought=True,
                        thought_signature=encode_openai_reasoning_signature(
                            item_id=r_id,
                            encrypted_content=encrypted,
                        ),
                    )
                )
        elif full_reasoning:
            raw_parts.append(types.Part(text=full_reasoning, thought=True))

        if full_text:
            raw_parts.append(types.Part.from_text(text=full_text))

        for item_id in tool_order:
            entry = tool_by_item_id[item_id]
            name = str(entry["name"]).strip()
            raw_args = (
                entry["final_arguments"]
                if isinstance(entry["final_arguments"], str)
                else "".join(entry["arguments"])
            )
            parsed_args = parse_tool_arguments(raw_args, name or item_id)
            fc = types.FunctionCall(
                id=str(entry["call_id"]) or None,
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
            self._active_token = ""
            await client.close()
