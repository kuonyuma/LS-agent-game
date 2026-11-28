from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, ClassVar

from google.genai import types


@dataclass
class ToolResult:
    content: str
    is_error: bool


class Tool(ABC):
    name: ClassVar[str]
    description: ClassVar[str]
    input_schema: ClassVar[dict[str, Any]]
    read_only: ClassVar[bool] = True

    @classmethod
    def requires_approval(cls) -> bool:
        return not cls.read_only

    @abstractmethod
    async def run(self, parameter: dict[str, Any]) -> ToolResult:
        pass

    def bind_arguments(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """在调度前绑定运行环境提供的工具参数。"""
        return arguments

    def to_function_declaration(self) -> types.FunctionDeclaration:
        return types.FunctionDeclaration(
            name=self.name,
            description=self.description,
            parameters=types.Schema(**self.input_schema),
        )
