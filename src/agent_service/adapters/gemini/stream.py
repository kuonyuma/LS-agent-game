import warnings
from collections.abc import AsyncGenerator
from typing import Any, cast

from google import genai
from google.genai import types

from agent_service.adapters.gemini.client import get_client
from agent_service.config.settings import settings
from agent_service.kernel.model.port import StreamEvent, StreamResult


def _build_thinking_config(thinking_level: str | None) -> types.ThinkingConfig | None:
    if not thinking_level:
        return None
    normalized = thinking_level.strip().lower()
    if not normalized or normalized == "off":
        return None
    upper = normalized.upper()
    enum_member = getattr(types.ThinkingLevel, upper, None)
    if enum_member is not None:
        return types.ThinkingConfig(
            thinking_level=enum_member,
            include_thoughts=True,
        )
    # 兼容新档位（如 Gemini 4 的 XHIGH），在当前 SDK 尚未补充枚举项时透传并静默告警
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        return types.ThinkingConfig(
            thinking_level=cast(Any, upper),
            include_thoughts=True,
        )


_FOREIGN_THOUGHT_SIG_PREFIX = b"openai_reasoning_v1:"


def _sanitize_contents_for_gemini(contents: Any) -> Any:
    """跨厂商切换回 Gemini 时剥离其他厂商（如 OpenAI）的思考签名，避免 SDK/服务端报 400。"""

    if not isinstance(contents, list):
        return contents
    sanitized: list[Any] = []
    changed = False
    for item in contents:
        if not isinstance(item, types.Content) or not item.parts:
            sanitized.append(item)
            continue
        new_parts: list[types.Part] = []
        item_changed = False
        for part in item.parts:
            sig = getattr(part, "thought_signature", None)
            if isinstance(sig, bytes) and sig.startswith(_FOREIGN_THOUGHT_SIG_PREFIX):
                item_changed = True
                changed = True
                if part.text:
                    new_parts.append(types.Part(text=part.text, thought=True))
                continue
            new_parts.append(part)
        if item_changed:
            sanitized.append(types.Content(role=item.role, parts=new_parts))
        else:
            sanitized.append(item)
    return sanitized if changed else contents


async def stream_message(
    contents: Any,
    system_prompt: str | None = "你是一位编程助手",
    max_tokens: int | None = None,
    tools: list[types.Tool] | None = None,
    *,
    model_name: str | None = None,
    client: genai.Client | None = None,
    thinking_level: str | None = None,
) -> AsyncGenerator[StreamEvent]:
    active_client = client if client is not None else get_client()
    active_model = model_name or settings.model_config.name

    thinking_config = _build_thinking_config(thinking_level)
    config = types.GenerateContentConfig(
        system_instruction=system_prompt,
        max_output_tokens=max_tokens,
        tools=tools,
        thinking_config=thinking_config,
    )
    response = await active_client.aio.models.generate_content_stream(
        model=active_model,
        contents=_sanitize_contents_for_gemini(contents),
        config=config,
    )

    yield StreamEvent(type="message_start")

    full_text: str = ""
    full_parts: list[types.Part] = []
    calls: list[types.FunctionCall] = []
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    has_cached_tokens: bool = False
    stop: str = "message_done"

    try:
        async for chunk in response:
            if chunk.usage_metadata:
                input_tokens = getattr(
                    chunk.usage_metadata, "prompt_token_count", input_tokens
                )
                output_tokens = getattr(
                    chunk.usage_metadata, "candidates_token_count", output_tokens
                )
                raw_cached = getattr(
                    chunk.usage_metadata, "cached_content_token_count", None
                )
                if isinstance(raw_cached, int):
                    cached_tokens = raw_cached
                    has_cached_tokens = True
            if chunk.candidates:
                candidate = chunk.candidates[0]
                if candidate.finish_reason:
                    stop = str(candidate.finish_reason)
                if candidate.content and candidate.content.parts:
                    parts = candidate.content.parts
                    for part in parts:
                        full_parts.append(part)
                        if part.text:
                            if getattr(part, "thought", False):
                                yield StreamEvent(type="thinking", text=part.text)
                            else:
                                full_text += part.text
                                yield StreamEvent(type="text", text=part.text)
                        if part.function_call:
                            fc = part.function_call
                            calls.append(fc)
                            yield StreamEvent(
                                type="tool_use_start",
                                id=fc.id or "",
                                name=fc.name or "",
                            )
    finally:
        close_stream = getattr(response, "aclose", None)
        if close_stream is not None:
            await close_stream()

    usage_payload: dict[str, Any] = {
        "input_len": input_tokens,
        "output_len": output_tokens,
    }
    if has_cached_tokens:
        usage_payload["cached_len"] = cached_tokens

    result = StreamResult(
        contents=full_text,
        stop_reason="tool_use" if calls else stop,
        usage=usage_payload,
        function_calls=calls,
        raw_parts=full_parts,
    )

    yield StreamEvent(type="message_done", result=result)
