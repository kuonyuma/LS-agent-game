"""使用 OpenAI Responses API（流式 + store=false）执行结构化安全审核。"""

from __future__ import annotations

import json
from typing import Any, cast

from openai import AsyncOpenAI

from agent_service.adapters.openai_compat.reviewer import _JSON_REVIEW_SCHEMA
from agent_service.adapters.openai_responses.auth import ChatGPTAuthManager
from agent_service.adapters.security.reviewer import ReviewDecision, ReviewResult
from agent_service.config.settings import ProviderConfig, resolve_api_key, settings


class OpenAIResponsesTextReviewer:
    """使用 Responses API 流式结构化输出执行文本安全审核（兼容 ChatGPT 套餐直连限制）。"""

    def __init__(
        self,
        provider: ProviderConfig,
        *,
        model_name: str | None = None,
        api_key: str | None = None,
        auth_manager: ChatGPTAuthManager | None = None,
        client: AsyncOpenAI | None = None,
    ) -> None:
        self.provider = provider
        self.model_name = (model_name or provider.default_model).strip()
        self._api_key = api_key.strip() if isinstance(api_key, str) and api_key.strip() else None
        self._auth_manager = auth_manager
        self._external_client = client is not None
        self._client: AsyncOpenAI | None = client
        self._active_token: str = ""

    async def _ensure_client(self) -> tuple[AsyncOpenAI | None, bool]:
        if self._external_client and self._client is not None:
            via_oauth = bool(
                self._api_key is None
                and self._auth_manager is not None
                and self._auth_manager.is_connected()
            )
            return self._client, via_oauth

        token = ""
        via_oauth = False
        if self._api_key:
            token = self._api_key
        elif self._auth_manager is not None and self._auth_manager.is_connected():
            try:
                token = await self._auth_manager.get_valid_access_token()
                via_oauth = True
            except Exception:  # noqa: BLE001
                return None, True
        else:
            token = resolve_api_key(self.provider)

        if not token:
            return None, via_oauth

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

    async def close(self) -> None:
        if self._client is not None and not self._external_client:
            client = self._client
            self._client = None
            self._active_token = ""
            await client.close()

    async def check(self, text: str) -> ReviewResult:
        client, via_oauth = await self._ensure_client()
        if client is None:
            return ReviewResult("error")

        request_kwargs: dict[str, Any] = {
            "model": self.model_name,
            "instructions": settings.safety_prompt,
            "input": cast(
                Any,
                [
                    {
                        "type": "message",
                        "role": "user",
                        "content": [
                            {
                                "type": "input_text",
                                "text": json.dumps(
                                    {"text_to_review": text}, ensure_ascii=False
                                ),
                            }
                        ],
                    }
                ],
            ),
            "store": False,
            "stream": True,
            "reasoning": {"effort": "low"},
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "review_decision",
                    "strict": True,
                    "schema": _JSON_REVIEW_SCHEMA,
                }
            },
        }
        if not via_oauth:
            request_kwargs["max_output_tokens"] = 1024

        response = await client.responses.create(**request_kwargs)
        text_parts: list[str] = []
        usage: dict[str, int] = {}
        completed_ok = False

        try:
            async for event in response:
                ev_type = str(getattr(event, "type", "") or "")
                if ev_type == "response.output_text.delta":
                    delta = getattr(event, "delta", None)
                    if isinstance(delta, str) and delta:
                        text_parts.append(delta)
                elif ev_type == "response.completed":
                    completed_ok = True
                    resp_obj = getattr(event, "response", None)
                    raw_usage = getattr(resp_obj, "usage", None)
                    if raw_usage is not None:
                        in_tok = getattr(raw_usage, "input_tokens", None)
                        out_tok = getattr(raw_usage, "output_tokens", None)
                        if isinstance(in_tok, int):
                            usage["input_len"] = in_tok
                        if isinstance(out_tok, int):
                            usage["output_len"] = out_tok
                elif ev_type in {"response.incomplete", "response.failed", "error"}:
                    return ReviewResult("error", usage=usage)
        finally:
            close_fn = getattr(response, "close", None) or getattr(
                response, "aclose", None
            )
            if callable(close_fn):
                maybe = close_fn()
                if hasattr(maybe, "__await__"):
                    await cast(Any, maybe)

        content = "".join(text_parts).strip()
        if not completed_ok or not content:
            return ReviewResult("error", usage=usage)

        try:
            decision = ReviewDecision.model_validate_json(content)
        except ValueError:
            return ReviewResult("error", usage=usage)

        if (decision.verdict == "allow") != (decision.category == "none"):
            return ReviewResult("error", usage=usage)
        return ReviewResult(decision.verdict, decision.category, usage)
