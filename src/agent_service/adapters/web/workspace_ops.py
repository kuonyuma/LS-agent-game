"""本地工作区目录校验、服务端目录浏览与系统原生选目录弹窗辅助模块。"""

from __future__ import annotations

import asyncio
import json
import os
import stat
import string
import sys
from pathlib import Path
from typing import Any

from agent_service.adapters.security.workspace import WorkspacePathError


def _is_link_or_reparse(path: Path, status: os.stat_result) -> bool:
    attributes = getattr(status, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    is_junction = getattr(path, "is_junction", lambda: False)
    return (
        stat.S_ISLNK(status.st_mode)
        or bool(attributes & reparse_flag)
        or bool(is_junction())
    )


def validate_workspace_path(raw_path: str) -> Path:
    """校验用户指定的工作区路径必须是已存在的普通本地目录（拒绝链接或不存在路径）。"""

    cleaned = raw_path.strip()
    if not cleaned:
        raise WorkspacePathError("工作区路径不能为空。")
    if "\x00" in cleaned:
        raise WorkspacePathError("工作区路径包含非法空字符。")

    candidate = Path(cleaned).expanduser()
    try:
        status = candidate.lstat()
    except FileNotFoundError as exc:
        raise WorkspacePathError(f"工作区目录不存在：{cleaned}") from exc
    except OSError as exc:
        raise WorkspacePathError(f"无法访问工作区目录：{exc}") from exc

    if _is_link_or_reparse(candidate, status):
        raise WorkspacePathError("工作区目录不能是符号链接或重解析点。")
    if not stat.S_ISDIR(status.st_mode):
        raise WorkspacePathError(f"指定路径不是目录：{cleaned}")

    try:
        return candidate.resolve(strict=True)
    except OSError as exc:
        raise WorkspacePathError(f"无法解析工作区目录：{exc}") from exc


def workspace_display_name(path: Path) -> str:
    return path.name or str(path)


def _list_windows_drives() -> list[dict[str, str]]:
    drives: list[dict[str, str]] = []
    for letter in string.ascii_uppercase:
        root = Path(f"{letter}:\\")
        try:
            if root.exists() and root.is_dir():
                drives.append({"name": f"{letter}:\\", "path": str(root)})
        except OSError:
            continue
    return drives


def list_workspace_entries(raw_path: str | None = None) -> dict[str, Any]:
    """返回指定目录的子目录列表（或根盘符列表），用于前端目录浏览器。"""

    home_dir = str(Path.home().resolve())
    roots = (
        _list_windows_drives()
        if os.name == "nt"
        else [{"name": "/", "path": "/"}]
    )

    if raw_path is None or not raw_path.strip():
        return {
            "current": "",
            "name": "此计算机",
            "parent": "",
            "home": home_dir,
            "roots": roots,
            "directories": roots,
        }

    current = validate_workspace_path(raw_path)
    parent = str(current.parent) if current.parent != current else ""

    directories: list[dict[str, str]] = []
    try:
        with os.scandir(current) as entries:
            for entry in entries:
                try:
                    entry_status = entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                if not stat.S_ISDIR(entry_status.st_mode):
                    continue
                entry_path = Path(entry.path)
                if _is_link_or_reparse(entry_path, entry_status):
                    continue
                directories.append(
                    {
                        "name": entry.name,
                        "path": str(entry_path.resolve()),
                    }
                )
    except OSError as exc:
        raise WorkspacePathError(f"无法读取目录内容：{exc}") from exc

    directories.sort(key=lambda item: item["name"].casefold())
    return {
        "current": str(current),
        "name": workspace_display_name(current),
        "parent": parent,
        "home": home_dir,
        "roots": roots,
        "directories": directories,
    }


_TK_PICKER_SCRIPT = r"""
import json
import sys
import tkinter as tk
from tkinter import filedialog

initial = sys.argv[1] if len(sys.argv) > 1 else ""
root = tk.Tk()
root.withdraw()
try:
    root.attributes("-topmost", True)
except Exception:
    pass
root.update()
selected = filedialog.askdirectory(
    initialdir=initial or None,
    title="选择 Agent 工作目录",
    mustexist=True,
)
root.destroy()
print(json.dumps({"path": selected or ""}, ensure_ascii=False))
"""


async def pick_native_workspace_directory(
    initial_dir: str | None = None,
    *,
    timeout_seconds: float = 180.0,
) -> Path | None:
    """通过隔离短命子进程拉起操作系统原生文件夹选择弹窗，避免主线程/Tk 冲突。"""

    start_dir = ""
    if initial_dir and initial_dir.strip():
        try:
            start_dir = str(validate_workspace_path(initial_dir))
        except WorkspacePathError:
            start_dir = ""

    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"

    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            _TK_PICKER_SCRIPT,
            start_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
    except OSError as exc:
        raise RuntimeError(f"无法启动本机目录选择器：{exc}") from exc

    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=timeout_seconds
        )
    except TimeoutError as exc:
        proc.kill()
        await proc.wait()
        raise RuntimeError("等待系统目录选择超时，请重试。") from exc

    if proc.returncode != 0:
        err_msg = stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(
            f"当前环境无法弹出系统文件夹选择框（{err_msg or '无图形桌面支持'}），请改用内置目录浏览或直接粘贴路径。"
        )

    raw_out = stdout.decode("utf-8", errors="replace").strip()
    if not raw_out:
        return None
    try:
        parsed = json.loads(raw_out.splitlines()[-1])
    except json.JSONDecodeError as exc:
        raise RuntimeError("解析系统目录选择结果失败。") from exc

    picked = str(parsed.get("path") or "").strip()
    if not picked:
        return None
    return validate_workspace_path(picked)
