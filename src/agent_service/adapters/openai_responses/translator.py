"""内核 DTO（google.genai.types）与 OpenAI Responses API 协议之间的双向翻译。"""

from __future__ import annotations

import base64
import json
from typing import Any

from google.genai import types

from agent_service.adapters.openai_compat.translator import schema_to_json_schema

OPENAI_REASONING_SIGNATURE_PREFIX = b"openai_reasoning_v1:"


def encode_openai_reasoning_signature(
    *,
    item_id: str,
    encrypted_content: str,
) -> bytes:
    """将 Responses API 的无状态推理元数据编码进 Part.thought_signature。"""

    payload = json.dumps(
        {"id": item_id, "encrypted_content": encrypted_content},
        ensure_ascii=False,
    ).encode("utf-8")
    return OPENAI_REASONING_SIGNATURE_PREFIX + payload


def decode_openai_reasoning_signature(
    signature: bytes | None,
) -> dict[str, str] | None:
    if not isinstance(signature, bytes):
        return None
    if not signature.startswith(OPENAI_REASONING_SIGNATURE_PREFIX):
        return None
    raw = signature[len(OPENAI_REASONING_SIGNATURE_PREFIX) :]
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(parsed, dict):
        return None
    encrypted = str(parsed.get("encrypted_content") or "").strip()
    if not encrypted:
        return None
    return {
        "id": str(parsed.get("id") or "").strip(),
        "encrypted_content": encrypted,
    }


def tools_to_responses(tools: list[types.Tool] | None) -> list[dict[str, Any]]:
    """将内核工具声明转换为 Responses API 扁平 function 工具列表。"""

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
                    "name": decl.name,
                    "description": decl.description or "",
                    "parameters": parameters,
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
        "type": "input_image",
        "image_url": f"data:{mime};base64,{b64}",
    }


def contents_to_responses_input(
    contents: list[types.Content],
) -> list[dict[str, Any]]:
    """将内核历史转换为 OpenAI Responses API `input` 项列表（适配 store=false 无状态多轮）。"""

    items: list[dict[str, Any]] = []
    synthetic_call_seq = 0

    for entry in contents:
        parts = list(entry.parts or [])
        if not parts:
            continue

        if entry.role == "model":
            text_chunks: list[str] = []
            function_calls: list[dict[str, Any]] = []
            reasoning_items: list[dict[str, Any]] = []

            for part in parts:
                if getattr(part, "thought", False):
                    sig_meta = decode_openai_reasoning_signature(
                        getattr(part, "thought_signature", None)
                    )
                    if sig_meta is not None:
                        summary: list[dict[str, str]] = (
                            [{"type": "summary_text", "text": part.text}]
                            if part.text
                            else []
                        )
                        r_item: dict[str, Any] = {
                            "type": "reasoning",
                            "summary": summary,
                            "encrypted_content": sig_meta["encrypted_content"],
                        }
                        if sig_meta["id"]:
                            r_item["id"] = sig_meta["id"]
                        reasoning_items.append(r_item)
                    continue

                if part.text:
                    text_chunks.append(part.text)

                if part.function_call is not None:
                    fc = part.function_call
                    synthetic_call_seq += 1
                    call_id = fc.id or f"call_{synthetic_call_seq}"
                    function_calls.append(
                        {
                            "type": "function_call",
                            "call_id": call_id,
                            "name": fc.name or "",
                            "arguments": json.dumps(fc.args or {}, ensure_ascii=False),
                        }
                    )

            items.extend(reasoning_items)
            if text_chunks:
                items.append(
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [
                            {"type": "output_text", "text": "".join(text_chunks)}
                        ],
                    }
                )
            items.extend(function_calls)
            continue

        tool_outputs: list[dict[str, Any]] = []
        user_content: list[dict[str, Any]] = []

        for part in parts:
            if part.function_response is not None:
                fr = part.function_response
                payload = json.dumps(fr.response or {}, ensure_ascii=False)
                tool_outputs.append(
                    {
                        "type": "function_call_output",
                        "call_id": fr.id or "",
                        "output": payload,
                    }
                )
                continue
            if part.inline_data is not None:
                img = _encode_inline_image(part.inline_data)
                if img is not None:
                    user_content.append(img)
                    continue
            if part.text:
                user_content.append({"type": "input_text", "text": part.text})

        items.extend(tool_outputs)
        if user_content:
            items.append(
                {
                    "type": "message",
                    "role": "user",
                    "content": user_content,
                }
            )

    return items
