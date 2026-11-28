import asyncio
import codecs
import locale
import subprocess
from typing import Any, ClassVar

from agent_service.adapters.security.workspace import WorkspacePathPolicy
from agent_service.kernel.dispatch.base import Tool, ToolResult


def _decode_output(data: bytes) -> str:
    if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        text = data.decode("utf-16", errors="replace")
    else:
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = data.decode(locale.getencoding(), errors="replace")
    return text.replace("\r\n", "\n").replace("\r", "\n")


class RunCommandTool(Tool):
    name = "run_command"
    description = (
        "在系统终端中执行一条 shell 命令并返回输出结果。"
        "适用于运行脚本、安装依赖、查看系统信息等操作。"
        "命令会在当前工作目录下执行。"
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "OBJECT",
        "properties": {
            "command": {
                "type": "STRING",
                "description": "要执行的本地 shell 命令，例如 'git status --short'",
            },
        },
        "required": ["command"],
    }
    read_only = False

    def __init__(self, workspace: WorkspacePathPolicy | None = None) -> None:
        self.workspace = workspace

    async def run(self, parameter: dict[str, Any]) -> ToolResult:
        command = parameter.get("command", "")

        if command == "":
            return ToolResult(is_error=True, content="执行的命令command为空")

        cwd = str(self.workspace.root) if self.workspace is not None else None
        try:
            result = await asyncio.to_thread(
                subprocess.run,
                command,
                shell=True,
                capture_output=True,
                # 先捕获字节，避免默认编码错误让读取线程丢掉整段输出。
                text=False,
                check=False,
                cwd=cwd,
            )
        except OSError as exc:
            return ToolResult(content=f"执行命令出错\n错误信息:{exc}", is_error=True)

        out = _decode_output(result.stdout) + _decode_output(result.stderr)
        out += f"returncode{result.returncode}"

        return ToolResult(content=out, is_error=result.returncode != 0)
