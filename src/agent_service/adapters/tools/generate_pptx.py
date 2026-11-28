import asyncio
import io
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Literal
from uuid import UUID, uuid4
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile

import httpx
import pymupdf
from pydantic import BaseModel, Field, ValidationError, field_validator

from agent_service.adapters.security.workspace import (
    WorkspacePathError,
    WorkspacePathPolicy,
)
from agent_service.kernel.dispatch.base import Tool, ToolResult

PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
_PRESENTATION_API = "/api/v1/ppt/presentation"
_MAX_EXPORT_BYTES = 32 * 1024 * 1024
_PML = "http://schemas.openxmlformats.org/presentationml/2006/main"


class PresentationError(ValueError):
    """可安全展示给用户的生成错误。"""


class PresentationInput(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    slides_markdown: list[str] = Field(min_length=1, max_length=50)
    language: str = Field(default="Chinese", min_length=1, max_length=60)
    instructions: str = Field(default="", max_length=2000)

    @field_validator("title", "language")
    @classmethod
    def clean_required_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("内容不能为空。")
        return value.strip()

    @field_validator("slides_markdown")
    @classmethod
    def clean_slides(cls, values: list[str]) -> list[str]:
        slides = [value.strip() for value in values]
        if any(not slide or len(slide) > 4000 for slide in slides):
            raise ValueError("每页必须有内容，且最多 4000 字符。")
        return slides


@dataclass(frozen=True)
class PresentonConfig:
    base_url: httpx.URL
    api_key: str = field(repr=False)

    @classmethod
    def from_environment(cls) -> tuple[PresentonConfig | None, tuple[str, ...]]:
        env = {
            name: os.environ.get(name, "").strip()
            for name in ("PRESENTON_BASE_URL", "PRESENTON_API_KEY")
        }
        missing = tuple(name for name, value in env.items() if not value)
        if missing:
            return None, missing
        try:
            url = httpx.URL(env["PRESENTON_BASE_URL"])
        except httpx.InvalidURL as exc:
            raise ValueError("PRESENTON_BASE_URL 必须是有效的 HTTP 服务地址。") from exc
        if (
            url.scheme not in {"http", "https"}
            or not url.host
            or url.userinfo
            or url.query
            or url.fragment
            or url.path != "/"
        ):
            raise ValueError("PRESENTON_BASE_URL 必须是 HTTP 服务根地址，例如 http://127.0.0.1:5001。")
        return cls(url, env["PRESENTON_API_KEY"]), ()


@dataclass(frozen=True)
class PresentationFiles:
    pptx: bytes
    slide_count: int
    preview_pdf: bytes | None
    cover: bytes | None


def _slide_count(pptx: bytes) -> int:
    try:
        with ZipFile(io.BytesIO(pptx)) as archive:
            content_types = archive.getinfo("[Content_Types].xml")
            presentation = archive.getinfo("ppt/presentation.xml")
            if max(content_types.file_size, presentation.file_size) > 1024 * 1024:
                raise PresentationError("Presenton 返回的 PPTX 结构超出限制。")
            root = ElementTree.fromstring(archive.read(presentation))
            count = len(root.findall(f"{{{_PML}}}sldIdLst/{{{_PML}}}sldId"))
    except (BadZipFile, ElementTree.ParseError, KeyError, RuntimeError) as exc:
        raise PresentationError("Presenton 未返回有效的 PPTX 文件。") from exc
    if not count:
        raise PresentationError("Presenton 返回的 PPTX 没有幻灯片。")
    return count


def _render_cover(pdf: bytes) -> bytes | None:
    with pymupdf.open(stream=pdf, filetype="pdf") as document:
        if not document.page_count:
            return None
        page = document[0]
        scale = min(2.0, 720 / max(page.rect.width, page.rect.height))
        return page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False).tobytes("png")


class PresentonClient:
    """向配置的 Presenton 实例提交逐页内容并取回导出文件。"""

    def __init__(
        self, config: PresentonConfig, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self.config = config
        self.transport = transport

    async def generate(self, request: PresentationInput) -> PresentationFiles:
        async with httpx.AsyncClient(
            base_url=self.config.base_url,
            headers={"Authorization": f"Bearer {self.config.api_key}"},
            timeout=httpx.Timeout(240.0, connect=8.0),
            transport=self.transport,
        ) as client:
            async with asyncio.timeout(240):
                export = await self._post(
                    client,
                    f"{_PRESENTATION_API}/generate",
                    {
                        "content": request.title,
                        "slides_markdown": request.slides_markdown,
                        "instructions": request.instructions,
                        "language": request.language,
                        "include_title_slide": True,
                        "include_table_of_contents": False,
                        "export_as": "pptx",
                    },
                )
                pptx = await self._download(client, export.get("path"), ".pptx")
                count = _slide_count(pptx)
            if count != len(request.slides_markdown):
                raise PresentationError(
                    f"请求 {len(request.slides_markdown)} 页，Presenton 返回了 {count} 页。"
                )
            preview_pdf, cover = await self._preview(client, export.get("presentation_id"))
        return PresentationFiles(pptx, count, preview_pdf, cover)

    @staticmethod
    async def _post(
        client: httpx.AsyncClient, path: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        response = await client.post(path, json=body)
        response.raise_for_status()
        try:
            value = response.json()
        except ValueError as exc:
            raise PresentationError("Presenton 返回的数据无法解析。") from exc
        if not isinstance(value, dict):
            raise PresentationError("Presenton 未返回导出记录。")
        return value

    @staticmethod
    async def _download(client: httpx.AsyncClient, path: object, suffix: str) -> bytes:
        if not isinstance(path, str) or not path or "\\" in path:
            raise PresentationError("Presenton 未返回有效的文件地址。")
        try:
            url = client.base_url.join(path)
        except httpx.InvalidURL as exc:
            raise PresentationError("Presenton 未返回有效的文件地址。") from exc
        origin = client.base_url
        if (
            (url.scheme, url.host, url.port) != (origin.scheme, origin.host, origin.port)
            or url.userinfo
            or not url.path.lower().endswith(suffix)
        ):
            raise PresentationError("Presenton 返回了不受支持的导出地址。")
        data = bytearray()
        async with client.stream("GET", url, follow_redirects=False) as response:
            response.raise_for_status()
            async for block in response.aiter_bytes(chunk_size=65536):
                data.extend(block)
                if len(data) > _MAX_EXPORT_BYTES:
                    raise PresentationError("Presenton 导出的文件超过 32 MB。")
        return bytes(data)

    async def _preview(
        self, client: httpx.AsyncClient, presentation_id: object
    ) -> tuple[bytes | None, bytes | None]:
        if not isinstance(presentation_id, str):
            return None, None
        try:
            deck_id = UUID(presentation_id)
            async with asyncio.timeout(25):
                export = await self._post(
                    client, f"{_PRESENTATION_API}/{deck_id}/export", {"export_as": "pdf"}
                )
                pdf = await self._download(client, export.get("path"), ".pdf")
                cover = await asyncio.to_thread(_render_cover, pdf)
                if cover is not None:
                    return pdf, cover
        except (httpx.HTTPError, TimeoutError, RuntimeError, ValueError, ZeroDivisionError):
            pass
        return None, None


@dataclass(frozen=True)
class PresentationArtifact:
    id: str
    filename: str
    slide_count: int
    download_url: str
    preview_url: str | None
    thumbnail_url: str | None

    def to_message(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "filename": self.filename,
            "slide_count": self.slide_count,
            "download_url": self.download_url,
            "preview_url": self.preview_url,
            "thumbnail_url": self.thumbnail_url,
        }


class PresentationStore:
    def __init__(self, root: Path) -> None:
        self.paths = WorkspacePathPolicy(root)

    def save(self, session_id: str, title: str, files: PresentationFiles) -> PresentationArtifact:
        artifact_id = uuid4().hex
        self.paths.resolve_for_write(f"{session_id}/{artifact_id}.pptx").write_bytes(files.pptx)
        url = f"/api/sessions/{session_id}/presentations/{artifact_id}"
        preview_url = None
        thumbnail_url = None
        if files.preview_pdf is not None and files.cover is not None:
            try:
                self.paths.resolve_for_write(f"{session_id}/{artifact_id}.pdf").write_bytes(
                    files.preview_pdf
                )
                preview_url = f"{url}/pdf"
                self.paths.resolve_for_write(f"{session_id}/{artifact_id}.png").write_bytes(
                    files.cover
                )
            except (OSError, WorkspacePathError):
                pass
            else:
                thumbnail_url = f"{url}/png"
        filename = re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f]', "_", title).strip(". ")
        filename = filename.removesuffix(".pptx") or "演示文稿"
        return PresentationArtifact(
            artifact_id, f"{filename}.pptx", files.slide_count, f"{url}/pptx", preview_url, thumbnail_url
        )

    def resolve(
        self, session_id: str, artifact_id: str, suffix: Literal["pptx", "pdf", "png"]
    ) -> Path:
        if not re.fullmatch(r"[a-f0-9]{32}", artifact_id):
            raise WorkspacePathError("成品 ID 无效。")
        return self.paths.resolve_existing(f"{session_id}/{artifact_id}.{suffix}", expected="file")


class GeneratePptxTool(Tool):
    name = "generate_pptx"
    description = "将逐页 Markdown 发送给已配置的 Presenton 服务，生成可下载的 PowerPoint 文件。"
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "OBJECT",
        "properties": {
            "title": {"type": "STRING", "description": "整套演示的标题"},
            "slides_markdown": {
                "type": "ARRAY",
                "items": {"type": "STRING"},
                "description": "按顺序排列的逐页 Markdown，每项一页，包含封面，最多 50 页",
            },
            "language": {"type": "STRING", "description": "输出语言，默认 Chinese"},
            "instructions": {"type": "STRING", "description": "可选的视觉风格、图示和排版要求"},
        },
        "required": ["title", "slides_markdown"],
    }
    read_only = False

    def __init__(self, client: PresentonClient, store: PresentationStore, session_id: str) -> None:
        self.client = client
        self.store = store
        self.session_id = session_id
        self.artifacts: list[PresentationArtifact] = []

    async def run(self, parameter: dict[str, Any]) -> ToolResult:
        try:
            request = PresentationInput.model_validate(parameter)
            files = await self.client.generate(request)
            artifact = self.store.save(self.session_id, request.title, files)
        except ValidationError:
            return ToolResult("PPT 参数无效。请提供标题和 1 至 50 页有效的逐页内容。", True)
        except httpx.HTTPStatusError as exc:
            return ToolResult(f"Presenton 请求失败（HTTP {exc.response.status_code}）。", True)
        except (TimeoutError, httpx.TimeoutException):
            return ToolResult("Presenton 生成或下载超时。", True)
        except httpx.RequestError:
            return ToolResult("无法连接 Presenton 服务，请检查后端配置和服务状态。", True)
        except PresentationError as exc:
            return ToolResult(str(exc), True)
        except (OSError, WorkspacePathError):
            return ToolResult("无法保存 PPT 文件，请检查会话数据目录。", True)
        self.artifacts.append(artifact)
        return ToolResult(f"已生成《{artifact.filename}》（{artifact.slide_count} 页）。请使用聊天成品卡。", False)
