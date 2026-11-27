from collections.abc import AsyncGenerator

from google import genai
from google.genai import types

from agent_service.adapters.gemini.client import (
    close_client,
    close_genai_client,
    create_client,
)
from agent_service.adapters.gemini.stream import stream_message
from agent_service.kernel.model.port import StreamEvent


class GeminiModel:
    """将 Gemini 流式 SDK 适配到内核模型端口，支持按实例覆盖模型名与 API Key。"""

    def __init__(
        self,
        *,
        model_name: str | None = None,
        api_key: str | None = None,
        thinking_level: str | None = None,
    ) -> None:
        self.model_name = model_name
        self._api_key = api_key.strip() if api_key and api_key.strip() else None
        self.thinking_level = (
            thinking_level.strip().lower()
            if isinstance(thinking_level, str) and thinking_level.strip()
            else None
        )
        self._dedicated_client: genai.Client | None = None

    def _get_dedicated_client(self) -> genai.Client | None:
        if self._api_key is None:
            return None
        if self._dedicated_client is None:
            self._dedicated_client = create_client(self._api_key)
        return self._dedicated_client

    def stream(
        self,
        *,
        contents: list[types.Content],
        system_prompt: str,
        max_tokens: int,
        tools: list[types.Tool] | None = None,
    ) -> AsyncGenerator[StreamEvent]:
        dedicated = self._get_dedicated_client()
        if self.model_name is None and dedicated is None and self.thinking_level is None:
            return stream_message(
                contents=contents,
                system_prompt=system_prompt,
                max_tokens=max_tokens,
                tools=tools,
            )
        return stream_message(
            contents=contents,
            system_prompt=system_prompt,
            max_tokens=max_tokens,
            tools=tools,
            model_name=self.model_name,
            client=dedicated,
            thinking_level=self.thinking_level,
        )

    async def close(self) -> None:
        if self._dedicated_client is not None:
            dedicated = self._dedicated_client
            self._dedicated_client = None
            await close_genai_client(dedicated)
            return
        if self._api_key is None:
            await close_client()
