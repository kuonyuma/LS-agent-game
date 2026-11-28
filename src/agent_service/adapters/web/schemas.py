from typing import Literal

from pydantic import BaseModel, Field

from agent_service.adapters.dashscope.image_client import ImageAspectRatio
from agent_service.adapters.openai_compat.model import ReasoningEffort
from agent_service.adapters.storage.session_store import SessionMode


class CreateSessionRequest(BaseModel):
    title: str = Field(default="新会话", max_length=120)
    mode: SessionMode = "multi"
    provider: str = "gemini"
    model_name: str = ""
    workspace: str = ""
    knowledge_base_ids: list[str] = Field(default_factory=list)


class UpdateSessionRequest(BaseModel):
    title: str | None = Field(default=None, max_length=120)
    mode: SessionMode | None = None
    provider: str | None = None
    model_name: str | None = None
    workspace: str | None = None
    knowledge_base_ids: list[str] | None = None


class RunAttachment(BaseModel):
    name: str = Field(default="attachment", max_length=180)
    mime_type: str = Field(default="application/octet-stream", max_length=120)
    kind: Literal["image", "file"] = "image"
    data_base64: str = ""
    text_content: str | None = None


class ImageOptions(BaseModel):
    aspect_ratio: ImageAspectRatio | None = None


class RunTurnRequest(BaseModel):
    query: str = Field(min_length=1, max_length=32000)
    provider: str | None = None
    model_name: str | None = None
    workspace: str | None = None
    api_key: str | None = None
    search_api_key: str | None = None
    serper_api_key: str | None = None
    tavily_api_key: str | None = None
    embedding_api_key: str | None = None
    knowledge_base_ids: list[str] | None = None
    mode: SessionMode | None = None
    thinking_enabled: bool = True
    reasoning_effort: ReasoningEffort = "high"
    safety_enabled: bool = True
    plan_and_execute: bool = False
    max_tokens: int | None = Field(default=None, ge=256, le=1048576)
    timezone: str | None = Field(default=None, min_length=1, max_length=100)
    system_prompt_override: str | None = None
    attachments: list[RunAttachment] = Field(default_factory=list)
    image_options: ImageOptions | None = None


class CreateKnowledgeBaseRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=400)
    embedding_provider: Literal["gemini", "ollama", "openai_compat"] = "gemini"
    embedding_model: str = Field(default="", max_length=120)
    embedding_base_url: str = Field(default="", max_length=300)
    embedding_dim: int = Field(default=1536, ge=0, le=8192)


class KnowledgeSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    top_k: int = Field(default=6, ge=1, le=20)
    api_key: str | None = None


class ApprovalDecisionRequest(BaseModel):
    approved: bool
    request_hash: str = Field(min_length=1)


class ValidateWorkspaceRequest(BaseModel):
    path: str = Field(min_length=1, max_length=4096)


class PickWorkspaceRequest(BaseModel):
    initial_dir: str | None = None


class StopRunResponse(BaseModel):
    stopped: bool
    status: Literal["stopped", "idle"]
