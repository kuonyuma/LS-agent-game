import asyncio
import base64
import binascii
import json
import os
import struct
from dataclasses import dataclass, field
from typing import Literal

import httpx

ImageAspectRatio = Literal["1:1", "9:16", "3:4", "4:3", "16:9"]
IMAGE_SIZES: dict[ImageAspectRatio, str] = {
    "1:1": "1024*1024",
    "9:16": "864*1536",
    "3:4": "768*1024",
    "4:3": "1024*768",
    "16:9": "1536*864",
}
_GENERATION_PATH = "/api/v1/services/aigc/multimodal-generation/generation"
_MAX_RESPONSE_BYTES = 24 * 1024 * 1024
_PNG_HEADER = b"\x89PNG\r\n\x1a\n\x00\x00\x00\x0dIHDR"
_PNG_END = b"\x00\x00\x00\x00IEND\xae\x42\x60\x82"


class ImageGenerationError(ValueError):
    """可安全展示给用户的生图错误。"""


@dataclass(frozen=True)
class DashScopeImageConfig:
    base_url: httpx.URL
    api_key: str = field(repr=False)
    model: str

    @classmethod
    def from_environment(cls, model: str) -> DashScopeImageConfig | None:
        raw_url = os.environ.get("DASHSCOPE_IMAGE_BASE_URL", "").strip()
        api_key = os.environ.get("DASHSCOPE_API_KEY", "").strip()
        if not raw_url or not api_key or not model:
            return None
        try:
            url = httpx.URL(raw_url)
        except httpx.InvalidURL as exc:
            raise ValueError("DASHSCOPE_IMAGE_BASE_URL 必须是有效的百炼 HTTPS 服务根地址。") from exc
        if url.scheme != "https" or not url.host or url.userinfo or url.query or url.fragment or url.path != "/":
            raise ValueError("DASHSCOPE_IMAGE_BASE_URL 必须是百炼业务空间的 HTTPS 服务根地址。")
        return cls(url, api_key, model)


@dataclass(frozen=True)
class GeneratedImage:
    png: bytes = field(repr=False)
    width: int
    height: int


def _decode_image(payload: bytes) -> GeneratedImage:
    try:
        data = json.loads(payload)
        for choice in data["output"]["choices"]:
            for part in choice["message"]["content"]:
                image = part.get("image")
                if isinstance(image, str):
                    mime, _, encoded = image.partition(",")
                    if mime != "data:image/png;base64":
                        raise ImageGenerationError("百炼返回了不受支持的图片格式。")
                    png = base64.b64decode(encoded, validate=True)
                    if not png.startswith(_PNG_HEADER) or not png.endswith(_PNG_END):
                        raise ImageGenerationError("百炼没有返回有效的 PNG 图片。")
                    width, height = struct.unpack_from(">II", png, len(_PNG_HEADER))
                    if not width or not height or width * height > 2048**2:
                        raise ImageGenerationError(f"百炼返回的图片尺寸超出限制（{width} × {height}）。")
                    return GeneratedImage(png, width, height)
    except ImageGenerationError:
        raise
    except (ValueError, TypeError, KeyError, AttributeError, binascii.Error, struct.error) as exc:
        raise ImageGenerationError("无法解析百炼返回的图片数据。") from exc
    raise ImageGenerationError("百炼没有返回图片。")


class DashScopeImageClient:
    def __init__(
        self, config: DashScopeImageConfig, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self.config = config
        self.transport = transport

    async def generate(self, prompt: str, aspect_ratio: ImageAspectRatio | None) -> GeneratedImage:
        parameters = {"n": 1, "response_format": "b64_json", "size": IMAGE_SIZES[aspect_ratio or "1:1"]}
        body = {
            "model": self.config.model,
            "input": {"messages": [{"role": "user", "content": [{"text": prompt}]}]},
            "parameters": parameters,
        }
        async with (
            httpx.AsyncClient(
                base_url=self.config.base_url,
                headers={"Authorization": f"Bearer {self.config.api_key}"},
                timeout=httpx.Timeout(180, connect=8),
                transport=self.transport,
            ) as client,
            asyncio.timeout(180),
            client.stream("POST", _GENERATION_PATH, json=body) as response,
        ):
            response.raise_for_status()
            result = bytearray()
            async for chunk in response.aiter_bytes(chunk_size=65536):
                result.extend(chunk)
                if len(result) > _MAX_RESPONSE_BYTES:
                    raise ImageGenerationError("百炼返回的图片数据超过大小上限。")
        return _decode_image(bytes(result))
