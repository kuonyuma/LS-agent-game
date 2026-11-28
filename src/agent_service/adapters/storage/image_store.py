import re
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from agent_service.adapters.dashscope.image_client import GeneratedImage
from agent_service.adapters.security.workspace import (
    WorkspacePathError,
    WorkspacePathPolicy,
)


@dataclass(frozen=True)
class ImageArtifact:
    id: str
    width: int
    height: int

    def to_message(self, session_id: str) -> dict[str, str | int]:
        url = f"/api/sessions/{session_id}/images/{self.id}"
        return {
            "id": self.id,
            "filename": f"生成图片-{self.id[:8]}.png",
            "width": self.width,
            "height": self.height,
            "preview_url": url,
            "download_url": f"{url}?download=true",
        }


class ImageStore:
    def __init__(self, root: Path) -> None:
        self.paths = WorkspacePathPolicy(root)

    def save(self, session_id: str, image: GeneratedImage) -> ImageArtifact:
        artifact_id = uuid4().hex
        path = self.paths.resolve_for_write(f"{session_id}/{artifact_id}.png")
        with path.open("xb") as output:
            output.write(image.png)
        return ImageArtifact(artifact_id, image.width, image.height)

    def resolve(self, session_id: str, artifact_id: str) -> Path:
        if not re.fullmatch(r"[a-f0-9]{32}", artifact_id):
            raise WorkspacePathError("图片成品 ID 无效。")
        return self.paths.resolve_existing(f"{session_id}/{artifact_id}.png", expected="file")
