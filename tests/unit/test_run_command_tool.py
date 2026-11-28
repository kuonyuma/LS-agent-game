"""RunCommand 工具的单元测试。"""

from pathlib import Path
from subprocess import CompletedProcess

import pytest

from agent_service.adapters.security.workspace import WorkspacePathPolicy
from agent_service.adapters.tools import run_command
from agent_service.adapters.tools.run_command import RunCommandTool


@pytest.mark.asyncio
async def test_run_command_passes_explicit_check_flag(monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return CompletedProcess(command, 0, stdout="完成".encode(), stderr=b"")

    monkeypatch.setattr(run_command.subprocess, "run", fake_run)

    result = await RunCommandTool().run({"command": "echo 完成"})

    assert not result.is_error
    assert result.content == "完成returncode0"
    assert calls == [
        (
            "echo 完成",
            {
                "shell": True,
                "capture_output": True,
                "text": False,
                "check": False,
                "cwd": None,
            },
        )
    ]


@pytest.mark.asyncio
async def test_run_command_returns_nonzero_exit_output(monkeypatch):
    def fake_run(command, **kwargs):
        return CompletedProcess(command, 1, stdout=b"", stderr="执行失败".encode())

    monkeypatch.setattr(run_command.subprocess, "run", fake_run)

    result = await RunCommandTool().run({"command": "exit 1"})

    assert result.is_error
    assert result.content == "执行失败returncode1"


@pytest.mark.asyncio
@pytest.mark.parametrize("stderr_encoding", ["gbk", "utf-16"])
async def test_run_command_decodes_stdout_and_stderr_independently(monkeypatch, stderr_encoding):
    def fake_run(command, **kwargs):
        return CompletedProcess(
            command,
            1,
            stdout="已读取文件（✓）\r\n".encode(),
            stderr="命令执行失败\r\n".encode(stderr_encoding),
        )

    monkeypatch.setattr(run_command.subprocess, "run", fake_run)
    monkeypatch.setattr(run_command.locale, "getencoding", lambda: "gbk")

    result = await RunCommandTool().run({"command": "echo 输出"})

    assert result.is_error
    assert result.content == "已读取文件（✓）\n命令执行失败\nreturncode1"


@pytest.mark.asyncio
async def test_empty_command_returns_error():
    result = await RunCommandTool().run({"command": ""})

    assert result.is_error


def test_tool_metadata():
    tool = RunCommandTool()

    assert tool.name == "run_command"
    assert tool.read_only is False
    assert tool.input_schema["required"] == ["command"]
    assert tool.to_function_declaration().name == "run_command"


@pytest.mark.asyncio
async def test_run_command_uses_workspace_root_as_cwd(monkeypatch, tmp_path: Path):
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return CompletedProcess(command, 0, stdout=b"ok", stderr=b"")

    monkeypatch.setattr(run_command.subprocess, "run", fake_run)

    policy = WorkspacePathPolicy(tmp_path)
    result = await RunCommandTool(policy).run({"command": "dir"})

    assert not result.is_error
    assert calls[0][1]["cwd"] == str(policy.root)
