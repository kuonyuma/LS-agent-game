import json
from dataclasses import dataclass, field
from typing import Literal, Protocol

from google import genai
from google.genai import types
from pydantic import BaseModel, ConfigDict

from agent_service.adapters.gemini.client import (
    close_genai_client,
    create_client,
    get_client,
)
from agent_service.config.settings import settings

SafetyCategory = Literal["none", "sexual", "violence", "child_safety"]


class ReviewDecision(BaseModel):
    """模型只提供判断和类别，不回传原文或自由文本理由。"""

    model_config = ConfigDict(
        extra="forbid",  # 拒绝额外字段
        strict=True,  # 严格校验
    )

    verdict: Literal["allow", "block"]
    category: SafetyCategory


@dataclass(frozen=True)
class ReviewResult:
    verdict: Literal["allow", "block", "error"]
    category: SafetyCategory = "none"
    usage: dict[str, int] = field(default_factory=dict)


class TextReviewer(Protocol):
    async def check(self, text: str) -> ReviewResult: ...


class PassThroughTextReviewer:
    """当用户在调试界面显式关闭输出安全审核时使用的直通审核器。"""

    async def check(self, text: str) -> ReviewResult:
        _ = text
        return ReviewResult(verdict="allow", category="none")


class GeminiTextReviewer:
    """复用共享或独立 Gemini 客户端；超时及任务生命周期由运行协调器管理。"""

    def __init__(
        self,
        *,
        model_name: str | None = None,
        api_key: str | None = None,
    ) -> None:
        self.model_name = model_name
        self._api_key = api_key.strip() if api_key and api_key.strip() else None
        self._dedicated_client: genai.Client | None = None

    def _active_client(self) -> genai.Client:
        if self._api_key is None:
            return get_client()
        if self._dedicated_client is None:
            self._dedicated_client = create_client(self._api_key)
        return self._dedicated_client

    async def close(self) -> None:
        if self._dedicated_client is not None:
            dedicated = self._dedicated_client
            self._dedicated_client = None
            await close_genai_client(dedicated)

    async def check(self, text: str) -> ReviewResult:
        response = await self._active_client().aio.models.generate_content(
            model=self.model_name or settings.model_config.name,
            contents=json.dumps({"text_to_review": text}, ensure_ascii=False),
            config=types.GenerateContentConfig(
                system_instruction=settings.safety_prompt,
                response_mime_type="application/json",
                response_schema=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "verdict": types.Schema(
                            type=types.Type.STRING, enum=["allow", "block"]
                        ),
                        "category": types.Schema(
                            type=types.Type.STRING,
                            enum=["none", "sexual", "violence", "child_safety"],
                        ),
                    },
                    required=["verdict", "category"],
                ),
                temperature=1,
                max_output_tokens=1024,
                thinking_config=types.ThinkingConfig(
                    thinking_level=types.ThinkingLevel.LOW,
                ),
            ),
        )
        usage: dict[str, int] = {}
        metadata = response.usage_metadata
        if metadata is not None:
            input_tokens = getattr(metadata, "prompt_token_count", None)
            if isinstance(input_tokens, int):
                usage["input_len"] = input_tokens

            output_tokens = getattr(metadata, "candidates_token_count", None)
            if isinstance(output_tokens, int):
                usage["output_len"] = output_tokens
        candidates = response.candidates or []
        if (
            not candidates
            or candidates[0].finish_reason != types.FinishReason.STOP
            or not response.text
        ):
            return ReviewResult("error", usage=usage)
        try:
            decision = ReviewDecision.model_validate_json(response.text)
        except ValueError:
            return ReviewResult("error", usage=usage)
        if (decision.verdict == "allow") != (decision.category == "none"):
            return ReviewResult("error", usage=usage)
        return ReviewResult(decision.verdict, decision.category, usage)
