"""内核 DTO（google.genai.types）与 OpenAI Chat Completions 协议之间的双向翻译。"""

import base64
import json
from typing import Any

from google.genai import types

from agent_service.errors import InvalidToolCallError

_TYPE_MAP: dict[str, str] = {
    "OBJECT": "object",
    "STRING": "string",
    "INTEGER": "integer",
    "NUMBER": "number",
    "BOOLEAN": "boolean",
    "ARRAY": "array",
}


def _normalize_schema_type(raw_type: Any) -> str:
    if raw_type is None:
        return "object"
    value = raw_type.value if hasattr(raw_type, "value") else str(raw_type)
    upper = str(value).upper()
    if upper.startswith("TYPE."):
        upper = upper.split(".", 1)[1]
    return _TYPE_MAP.get(upper, str(value).lower())


def schema_to_json_schema(schema: types.Schema | None) -> dict[str, Any]:
    """将 Gemini types.Schema 递归转换为标准 OpenAI JSON Schema。"""

    if schema is None:
        return {"type": "object", "properties": {}}

    result: dict[str, Any] = {"type": _normalize_schema_type(schema.type)}
    if schema.description:
        result["description"] = schema.description
    if schema.enum:
        result["enum"] = list(schema.enum)
    if schema.properties is not None:
        result["properties"] = {
            key: schema_to_json_schema(value)
            for key, value in schema.properties.items()
        }
    elif result["type"] == "object":
        result["properties"] = {}
    if schema.required:
        result["required"] = list(schema.required)
    if schema.items is not None:
        result["items"] = schema_to_json_schema(schema.items)
    return result


def tools_to_openai(tools: list[types.Tool] | None) -> list[dict[str, Any]]:
    """将内核工具声明转换为 OpenAI Chat Completions `tools` 列表。"""

    if not tools:
        return []
    converted: list[dict[str, Any]] = []
    for group in tools:
        for decl in group.function_declarations or []:
            if not decl.name:
                continue
            parameters = schema_to_json_schema(decl.parameters)
            if parameters.get("type") != "object":
                parameters = {"type": "object", "properties": {}}
            converted.append(
                {
                    "type": "function",
                    "function": {
                        "name": decl.name,
                        "description": decl.description or "",
                        "parameters": parameters,
                    },
                }
            )
    return converted


def _encode_inline_image(blob: types.Blob) -> dict[str, Any] | None:
    mime = (blob.mime_type or "").strip()
    data = blob.data
    if not mime.startswith("image/") or not data:
        return None
    b64 = base64.b64encode(data).decode("ascii")
    return {
        "type": "image_url",
        "image_url": {"url": f"data:{mime};base64,{b64}"},
    }


def contents_to_openai_messages(
    contents: list[types.Content],
    system_prompt: str,
) -> list[dict[str, Any]]:
    """将内核历史转换为 OpenAI 协议消息列表。

    关键约束：
    1. 保留 `thought=True` 文本到 assistant 的 `reasoning_content`（DeepSeek V4 与 Kimi K3.1 Pro 工具调用强要求）。
    2. 同一条 user Content 同时包含 `function_response` 与追加文本（如 `<runtime-notice>`）时，
       必须先发出所有 `role="tool"` 消息，再单独发出 `role="user"` 消息。
    """

    messages: list[dict[str, Any]] = []
    if system_prompt.strip():
        messages.append({"role": "system", "content": system_prompt})

    synthetic_call_seq = 0

    for item in contents:
        parts = list(item.parts or [])
        if not parts:
            continue

        if item.role == "model":
            reasoning_chunks: list[str] = []
            text_chunks: list[str] = []
            tool_calls: list[dict[str, Any]] = []

            for part in parts:
                if part.text:
                    if getattr(part, "thought", False):
                        reasoning_chunks.append(part.text)
                    else:
                        text_chunks.append(part.text)
                if part.function_call is not None:
                    fc = part.function_call
                    synthetic_call_seq += 1
                    call_id = fc.id or f"call_{synthetic_call_seq}"
                    tool_calls.append(
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {
                                "name": fc.name or "",
                                "arguments": json.dumps(
                                    fc.args or {}, ensure_ascii=False
                                ),
                            },
                        }
                    )

            if not reasoning_chunks and not text_chunks and not tool_calls:
                continue

            assistant_msg: dict[str, Any] = {
                "role": "assistant",
                "content": "".join(text_chunks),
            }
            if reasoning_chunks:
                assistant_msg["reasoning_content"] = "".join(reasoning_chunks)
            if tool_calls:
                assistant_msg["tool_calls"] = tool_calls
            messages.append(assistant_msg)
            continue

        tool_messages: list[dict[str, Any]] = []
        text_blocks: list[str] = []
        image_blocks: list[dict[str, Any]] = []

        for part in parts:
            if part.function_response is not None:
                fr = part.function_response
                payload = json.dumps(fr.response or {}, ensure_ascii=False)
                tool_messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": fr.id or "",
                        "content": payload,
                    }
                )
                continue
            if part.inline_data is not None:
                image_block = _encode_inline_image(part.inline_data)
                if image_block is not None:
                    image_blocks.append(image_block)
                    continue
            if part.text:
                text_blocks.append(part.text)

        messages.extend(tool_messages)

        if image_blocks:
            content_array: list[dict[str, Any]] = [*image_blocks]
            if text_blocks:
                content_array.append({"type": "text", "text": "\n".join(text_blocks)})
            messages.append({"role": "user", "content": content_array})
        elif text_blocks:
            messages.append({"role": "user", "content": "\n".join(text_blocks)})

    return messages


def parse_tool_arguments(raw_arguments: str, tool_name: str) -> dict[str, Any]:
    """解析流式拼接完成后的工具 JSON 参数。"""

    cleaned = raw_arguments.strip()
    if not cleaned:
        return {}
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise InvalidToolCallError(
            f"模型返回的工具 `{tool_name}` 参数不是合法 JSON：{exc.msg}"
        ) from exc
    if isinstance(parsed, dict):
        return parsed
    raise InvalidToolCallError(
        f"模型返回的工具 `{tool_name}` 参数必须是 JSON 对象，实际为 {type(parsed).__name__}"
    )
