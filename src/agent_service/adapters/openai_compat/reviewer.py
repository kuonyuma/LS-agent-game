import json
from typing import Any, cast

from openai import AsyncOpenAI

from agent_service.adapters.security.reviewer import ReviewDecision, ReviewResult
from agent_service.config.settings import (
    ProviderConfig,
    model_supports_reasoning_effort,
    resolve_api_key,
    resolve_model_thinking_mode,
    resolve_thinking_param_style,
    settings,
)

_JSON_REVIEW_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["allow", "block"]},
        "category": {
            "type": "string",
            "enum": ["none", "sexual", "violence", "child_safety"],
        },
    },
    "required": ["verdict", "category"],
    "additionalProperties": False,
}

_JSON_OBJECT_HINT = (
    '\n\n请严格只输出一个 JSON 对象，不要包含额外文字或 Markdown 代码块，例如：'
    '{"verdict": "allow", "category": "none"} 或 {"verdict": "block", "category": "violence"}。'
)


class OpenAICompatibleTextReviewer:
    """使用 DeepSeek / Kimi / GLM / Qwen 等 OpenAI 协议接口执行结构化输出安全审核。"""

    def __init__(
        self,
        provider: ProviderConfig,
        *,
        model_name: str | None = None,
        api_key: str | None = None,
        client: AsyncOpenAI | None = None,
    ) -> None:
        self.provider = provider
        self.model_name = (model_name or provider.default_model).strip()
        self._api_key = api_key
        self._external_client = client is not None
        self._client: AsyncOpenAI | None = client

    def _ensure_client(self) -> AsyncOpenAI | None:
        if self._client is not None:
            return self._client
        key = resolve_api_key(self.provider, self._api_key)
        if not key:
            return None
        self._client = AsyncOpenAI(
            api_key=key,
            base_url=self.provider.base_url or None,
        )
        return self._client

    async def close(self) -> None:
        if self._client is not None and not self._external_client:
            client = self._client
            self._client = None
            await client.close()

    def _build_review_extra_body(self) -> dict[str, Any]:
        model_thinking_mode = resolve_model_thinking_mode(
            self.provider, self.model_name
        )
        style = resolve_thinking_param_style(self.provider, self.model_name)
        if style is None:
            return {}
        supports_effort = model_supports_reasoning_effort(
            self.provider, self.model_name
        )
        if model_thinking_mode == "toggleable":
            if style == "enable_thinking":
                return {"enable_thinking": False}
            if style == "thinking_object":
                return {"thinking": {"type": "disabled"}}
            return {}
        if model_thinking_mode == "always_on":
            if style == "thinking_object":
                payload: dict[str, Any] = {"thinking": {"type": "enabled"}}
                if supports_effort:
                    payload["reasoning_effort"] = "low"
                return payload
            if style == "enable_thinking":
                payload = {"enable_thinking": True}
                if supports_effort:
                    payload["reasoning_effort"] = "low"
                return payload
            return {"reasoning_effort": "low"} if supports_effort else {}
        return {}

    async def check(self, text: str) -> ReviewResult:
        client = self._ensure_client()
        if client is None:
            return ReviewResult("error")

        system_instruction = settings.safety_prompt
        model_thinking_mode = resolve_model_thinking_mode(
            self.provider, self.model_name
        )
        # 强制思考模型的思考过程与 JSON 正文共享 max_tokens，预留足够空间避免提前截断
        max_tokens = 1024 if model_thinking_mode == "always_on" else 256
        request_kwargs: dict[str, Any] = {
            "model": self.model_name,
            "max_tokens": max_tokens,
        }

        if self.provider.json_mode == "json_schema":
            request_kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "review_decision",
                    "strict": True,
                    "schema": _JSON_REVIEW_SCHEMA,
                },
            }
        else:
            system_instruction = f"{system_instruction}{_JSON_OBJECT_HINT}"
            request_kwargs["response_format"] = {"type": "json_object"}

        extra_body = self._build_review_extra_body()
        if extra_body:
            request_kwargs["extra_body"] = extra_body

        request_kwargs["messages"] = cast(
            Any,
            [
                {"role": "system", "content": system_instruction},
                {
                    "role": "user",
                    "content": json.dumps({"text_to_review": text}, ensure_ascii=False),
                },
            ],
        )

        response = await client.chat.completions.create(**request_kwargs)
        usage: dict[str, int] = {}
        raw_usage = getattr(response, "usage", None)
        if raw_usage is not None:
            in_tokens = getattr(raw_usage, "prompt_tokens", None)
            out_tokens = getattr(raw_usage, "completion_tokens", None)
            if isinstance(in_tokens, int):
                usage["input_len"] = in_tokens
            if isinstance(out_tokens, int):
                usage["output_len"] = out_tokens

        choices = getattr(response, "choices", None) or []
        if not choices:
            return ReviewResult("error", usage=usage)
        choice = choices[0]
        content = getattr(getattr(choice, "message", None), "content", None)
        if not isinstance(content, str) or not content.strip():
            return ReviewResult("error", usage=usage)
        if getattr(choice, "finish_reason", None) not in ("stop", None):
            return ReviewResult("error", usage=usage)

        try:
            decision = ReviewDecision.model_validate_json(content.strip())
        except ValueError:
            return ReviewResult("error", usage=usage)

        if (decision.verdict == "allow") != (decision.category == "none"):
            return ReviewResult("error", usage=usage)
        return ReviewResult(decision.verdict, decision.category, usage)
