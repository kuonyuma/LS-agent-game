from typing import Any, ClassVar

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agent_service.adapters.dashscope.image_client import (
    IMAGE_SIZES,
    DashScopeImageClient,
    ImageAspectRatio,
    ImageGenerationError,
)
from agent_service.adapters.security.workspace import WorkspacePathError
from agent_service.adapters.storage.image_store import ImageArtifact, ImageStore
from agent_service.kernel.dispatch.base import Tool, ToolResult


class ImageGenerationInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    prompt: str = Field(min_length=1, max_length=4000)
    aspect_ratio: ImageAspectRatio | None = None


class GenerateImageTool(Tool):
    name = "generate_image"
    description = "根据文字描述生成一张 PNG 图片。用于用户明确要求画图、生成插图或海报成品的场景。"
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "OBJECT",
        "properties": {
            "prompt": {"type": "STRING", "description": "用户希望生成的画面、风格和要保留的文字。"},
            "aspect_ratio": {
                "type": "STRING",
                "enum": list(IMAGE_SIZES),
                "description": "宽高比。可从用户描述中确定，未指定时省略并使用默认的 1:1。",
            },
        },
        "required": ["prompt"],
    }
    read_only = False

    @classmethod
    def requires_approval(cls) -> bool:
        return False

    def __init__(
        self,
        client: DashScopeImageClient,
        store: ImageStore,
        session_id: str,
        *,
        selected_ratio: ImageAspectRatio | None = None,
    ) -> None:
        self.client = client
        self.store = store
        self.session_id = session_id
        self.selected_ratio = selected_ratio
        self.artifacts: list[ImageArtifact] = []

    def bind_arguments(self, arguments: dict[str, Any]) -> dict[str, Any]:
        if self.selected_ratio is None:
            return arguments
        return {**arguments, "aspect_ratio": self.selected_ratio}

    async def run(self, parameter: dict[str, Any]) -> ToolResult:
        try:
            request = ImageGenerationInput.model_validate(parameter)
            image = await self.client.generate(request.prompt, request.aspect_ratio)
            artifact = self.store.save(self.session_id, image)
        except ValidationError:
            return ToolResult("生图参数无效，请提供图片描述和有效的宽高比。", True)
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            message = {
                401: "百炼生图鉴权失败，请检查业务空间与 API Key。",
                403: "当前百炼账号无权调用生图接口。",
                429: "百炼生图请求达到限流，请稍后重试。",
            }.get(status, f"百炼生图请求失败（HTTP {status}）。")
            return ToolResult(message, True)
        except (TimeoutError, httpx.TimeoutException):
            return ToolResult("等待百炼生成图片超时。", True)
        except httpx.RequestError:
            return ToolResult("无法连接百炼生图服务。", True)
        except ImageGenerationError as exc:
            return ToolResult(str(exc), True)
        except (OSError, WorkspacePathError):
            return ToolResult("无法保存图片成品，请检查会话数据目录。", True)
        self.artifacts.append(artifact)
        return ToolResult(f"图片已生成（{image.width} × {image.height}），可在聊天中预览并下载。", False)
