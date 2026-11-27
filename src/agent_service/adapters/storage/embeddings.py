"""可插拔文本向量化（Embedding）适配器：默认 Gemini Embedding 2，支持本地 Ollama 与 OpenAI 兼容端点。"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Sequence
from typing import Any, Protocol, cast

from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from openai import APIConnectionError, APIStatusError, AsyncOpenAI, AuthenticationError

from agent_service.adapters.gemini.client import close_genai_client, create_client
from agent_service.config.settings import (
    EmbeddingProviderType,
    resolve_api_key,
    settings,
)
from agent_service.errors import AgentRuntimeError, ConfigurationError

DEFAULT_OLLAMA_BASE_URL = "http://127.0.0.1:11434/v1"


class EmbeddingProvider(Protocol):
    """统一的向量化接口，屏蔽底层云端或本地模型差异。"""

    provider_id: str
    model_name: str
    dimensions: int

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...

    async def embed_query(self, text: str) -> list[float]: ...

    async def close(self) -> None: ...


class GeminiEmbeddingProvider:
    """基于官方 google-genai SDK 调用 gemini-embedding-2 等模型，支持任务类型优化与灵活降维。"""

    provider_id = "gemini"

    def __init__(
        self,
        *,
        model_name: str = "gemini-embedding-2",
        dimensions: int = 1536,
        api_key: str | None = None,
        batch_size: int = 48,
        client: genai.Client | None = None,
    ) -> None:
        self.model_name = model_name.strip() or "gemini-embedding-2"
        self.dimensions = dimensions
        self._api_key = api_key
        self._batch_size = max(1, batch_size)
        self._external_client = client is not None
        self._client: genai.Client | None = client

    def _ensure_client(self) -> genai.Client:
        if self._client is not None:
            return self._client
        gemini_cfg = settings.providers.get("gemini")
        key = (
            resolve_api_key(gemini_cfg, self._api_key)
            if gemini_cfg is not None
            else (self._api_key or os.getenv("GEMINI_API_KEY", "")).strip()
        )
        if not key:
            raise ConfigurationError(
                "构建或检索 Gemini 向量知识库需要配置 GEMINI_API_KEY，"
                "可在环境变量或页面“模型设置与密钥”中填入 Google Gemini API Key，"
                "或在新建知识库时改用本地 Ollama Embedding。"
            )
        self._client = create_client(key)
        return self._client

    async def _embed_batch(
        self,
        texts: Sequence[str],
        *,
        task_type: str,
    ) -> list[list[float]]:
        if not texts:
            return []
        client = self._ensure_client()
        results: list[list[float]] = []
        config = types.EmbedContentConfig(
            task_type=task_type,
            output_dimensionality=self.dimensions,
        )
        for start in range(0, len(texts), self._batch_size):
            batch = list(texts[start : start + self._batch_size])
            # 必须把每条切片显式包装为独立的 types.Content：
            # google-genai 对 gemini-embedding-2 会先调用 t_contents(contents)，
            # 若直接传 list[str]，SDK 会把连续字符串当成同一条多模态消息的多个 Part 合并成 1 条，
            # 导致 batchEmbedContents 只返回 1 个向量。
            batch_contents = [
                types.Content(parts=[types.Part.from_text(text=item)])
                for item in batch
            ]
            response = await self._call_embed_with_retry(
                client=client,
                contents=batch_contents,
                config=config,
            )
            embeddings = response.embeddings or []
            if len(embeddings) != len(batch):
                raise AgentRuntimeError(
                    f"Gemini Embedding 返回向量数量不匹配：预期 {len(batch)}，实际 {len(embeddings)}"
                )
            for item in embeddings:
                values = list(item.values or [])
                if not values:
                    raise AgentRuntimeError("Gemini Embedding 返回了空向量。")
                results.append([float(v) for v in values])
                if len(values) != self.dimensions:
                    self.dimensions = len(values)
        return results

    async def _call_embed_with_retry(
        self,
        *,
        client: genai.Client,
        contents: list[types.Content],
        config: types.EmbedContentConfig,
        max_attempts: int = 4,
    ) -> types.EmbedContentResponse:
        last_exc: Exception | None = None
        for attempt in range(max_attempts):
            try:
                return await client.aio.models.embed_content(
                    model=self.model_name,
                    contents=cast(Any, contents),
                    config=config,
                )
            except genai_errors.APIError as exc:
                last_exc = exc
                status = getattr(exc, "code", None)
                retryable = status in (429, 500, 502, 503, 504)
                if not retryable or attempt + 1 >= max_attempts:
                    raise AgentRuntimeError(
                        f"Gemini Embedding 调用失败（模型 `{self.model_name}`）：{exc}"
                    ) from exc
                await asyncio.sleep(min(8.0, 1.5 * (2**attempt)))
            except Exception as exc:
                raise AgentRuntimeError(
                    f"Gemini Embedding 调用失败（模型 `{self.model_name}`）：{exc}"
                ) from exc
        raise AgentRuntimeError(
            f"Gemini Embedding 调用失败（模型 `{self.model_name}`）：{last_exc}"
        )

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return await self._embed_batch(texts, task_type="RETRIEVAL_DOCUMENT")

    async def embed_query(self, text: str) -> list[float]:
        vectors = await self._embed_batch([text], task_type="RETRIEVAL_QUERY")
        return vectors[0]

    async def close(self) -> None:
        if self._client is not None and not self._external_client:
            client = self._client
            self._client = None
            await close_genai_client(client)


class OpenAICompatibleEmbeddingProvider:
    """适配本地 Ollama (/v1/embeddings) 及任意 OpenAI 兼容向量模型接口。"""

    def __init__(
        self,
        *,
        provider_id: str = "openai_compat",
        model_name: str,
        base_url: str,
        dimensions: int = 0,
        api_key: str | None = None,
        batch_size: int = 32,
        client: AsyncOpenAI | None = None,
    ) -> None:
        self.provider_id = provider_id
        self.model_name = model_name.strip()
        self.base_url = base_url.strip()
        self.dimensions = dimensions
        self._api_key = api_key
        self._batch_size = max(1, batch_size)
        self._external_client = client is not None
        self._client: AsyncOpenAI | None = client

    def _ensure_client(self) -> AsyncOpenAI:
        if self._client is not None:
            return self._client
        key = (self._api_key or "").strip()
        if not key:
            if self.provider_id == "ollama":
                key = "ollama"
            else:
                key = os.getenv("EMBEDDING_API_KEY", "").strip() or os.getenv(
                    "OPENAI_API_KEY", ""
                ).strip()
        if not key:
            raise ConfigurationError(
                "OpenAI 兼容 Embedding 缺少 API Key，请在设置或环境变量 EMBEDDING_API_KEY 中配置。"
            )
        self._client = AsyncOpenAI(
            api_key=key,
            base_url=self.base_url or None,
        )
        return self._client

    async def _embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        client = self._ensure_client()
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self._batch_size):
            batch = list(texts[start : start + self._batch_size])
            kwargs: dict[str, Any] = {
                "model": self.model_name,
                "input": batch,
            }
            if self.dimensions > 0 and self.provider_id != "ollama":
                kwargs["dimensions"] = self.dimensions
            try:
                resp = await client.embeddings.create(**kwargs)
            except AuthenticationError as exc:
                raise ConfigurationError(
                    f"Embedding 接口鉴权失败（{self.base_url}），请检查 API Key。"
                ) from exc
            except APIConnectionError as exc:
                hint = (
                    "请确认本机 Ollama 服务已启动，且已拉取对应模型（如 `ollama pull bge-m3`）。"
                    if self.provider_id == "ollama"
                    else f"无法连接 Embedding 接口（{self.base_url}）。"
                )
                raise AgentRuntimeError(f"{hint} 详情：{exc}") from exc
            except APIStatusError as exc:
                raise AgentRuntimeError(
                    f"Embedding 接口返回错误（HTTP {exc.status_code}）：{exc.message}"
                ) from exc

            ordered = sorted(resp.data, key=lambda item: item.index)
            if len(ordered) != len(batch):
                raise AgentRuntimeError(
                    f"Embedding 返回向量数量不匹配：预期 {len(batch)}，实际 {len(ordered)}"
                )
            for item in ordered:
                vec = [float(v) for v in item.embedding]
                if not vec:
                    raise AgentRuntimeError("Embedding 接口返回了空向量。")
                vectors.append(vec)
                if self.dimensions <= 0:
                    self.dimensions = len(vec)
        return vectors

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return await self._embed(texts)

    async def embed_query(self, text: str) -> list[float]:
        return (await self._embed([text]))[0]

    async def close(self) -> None:
        if self._client is not None and not self._external_client:
            client = self._client
            self._client = None
            await client.close()


def create_embedding_provider(
    *,
    provider: str | None = None,
    model_name: str | None = None,
    dimensions: int | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
) -> EmbeddingProvider:
    """按显式参数或系统默认配置构造可插拔 Embedding 适配器。"""

    cfg = settings.embedding
    target_provider = cast(
        EmbeddingProviderType,
        (provider or cfg.provider or "gemini").strip().lower(),
    )
    if target_provider == "gemini":
        return GeminiEmbeddingProvider(
            model_name=(model_name or cfg.model or "gemini-embedding-2").strip(),
            dimensions=dimensions if dimensions and dimensions > 0 else cfg.dimensions,
            api_key=api_key,
        )
    if target_provider == "ollama":
        return OpenAICompatibleEmbeddingProvider(
            provider_id="ollama",
            model_name=(model_name or "bge-m3").strip(),
            base_url=(base_url or cfg.base_url or DEFAULT_OLLAMA_BASE_URL).strip(),
            dimensions=dimensions if dimensions and dimensions > 0 else 0,
            api_key=api_key or "ollama",
        )
    if target_provider == "openai_compat":
        resolved_base = (base_url or cfg.base_url).strip()
        if not resolved_base:
            raise ConfigurationError("使用 OpenAI 兼容 Embedding 时必须提供 base_url。")
        resolved_model = (model_name or cfg.model).strip()
        if not resolved_model:
            raise ConfigurationError("使用 OpenAI 兼容 Embedding 时必须提供模型名。")
        return OpenAICompatibleEmbeddingProvider(
            provider_id="openai_compat",
            model_name=resolved_model,
            base_url=resolved_base,
            dimensions=dimensions if dimensions and dimensions > 0 else 0,
            api_key=api_key,
        )
    raise ConfigurationError(
        f"不支持的 Embedding 提供方 `{target_provider}`，可选：gemini、ollama、openai_compat。"
    )
