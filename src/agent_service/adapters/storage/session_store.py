import json
import os
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from google.genai import types

SessionMode = Literal["single", "multi"]

_VALID_SESSION_ID = re.compile(r"^[a-zA-Z0-9_-]{4,64}$")


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def serialize_contents(contents: list[types.Content]) -> list[dict[str, Any]]:
    """将 Gemini types.Content 列表序列化为可持久化的 JSON 结构。"""

    return [
        item.model_dump(mode="json", exclude_none=True)
        for item in contents
    ]


def deserialize_contents(raw: list[dict[str, Any]]) -> list[types.Content]:
    """从持久化 JSON 恢复完整历史（含工具调用、响应与思考标记）。"""

    restored: list[types.Content] = []
    for item in raw:
        restored.append(types.Content.model_validate(item))
    return restored


@dataclass
class SessionRecord:
    session_id: str
    title: str
    mode: SessionMode
    provider: str
    model_name: str
    created_at: str
    updated_at: str
    workspace: str = ""
    knowledge_base_ids: list[str] = field(default_factory=list)
    usage: dict[str, int] = field(
        default_factory=lambda: {"input_len": 0, "output_len": 0, "cached_len": 0}
    )
    history: list[dict[str, Any]] = field(default_factory=list)
    ui_messages: list[dict[str, Any]] = field(default_factory=list)

    def to_summary(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "title": self.title,
            "mode": self.mode,
            "provider": self.provider,
            "model_name": self.model_name,
            "workspace": self.workspace,
            "knowledge_base_ids": list(self.knowledge_base_ids),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "message_count": len(self.ui_messages),
            "usage": dict(self.usage),
        }

    def to_detail(self) -> dict[str, Any]:
        summary = self.to_summary()
        summary["messages"] = list(self.ui_messages)
        return summary

    def to_disk_dict(self) -> dict[str, Any]:
        return {
            "version": 1,
            "session_id": self.session_id,
            "title": self.title,
            "mode": self.mode,
            "provider": self.provider,
            "model_name": self.model_name,
            "workspace": self.workspace,
            "knowledge_base_ids": list(self.knowledge_base_ids),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "usage": dict(self.usage),
            "history": list(self.history),
            "ui_messages": list(self.ui_messages),
        }

    @classmethod
    def from_disk_dict(cls, data: dict[str, Any]) -> SessionRecord:
        mode_raw = str(data.get("mode", "multi"))
        mode: SessionMode = "single" if mode_raw == "single" else "multi"
        usage_raw = data.get("usage")
        usage: dict[str, int] = {"input_len": 0, "output_len": 0, "cached_len": 0}
        if isinstance(usage_raw, dict):
            for key in ("input_len", "output_len", "cached_len"):
                val = usage_raw.get(key, 0)
                if isinstance(val, int):
                    usage[key] = val
        raw_kb_ids = data.get("knowledge_base_ids")
        kb_ids = (
            [str(item) for item in raw_kb_ids if isinstance(item, str) and item.strip()]
            if isinstance(raw_kb_ids, list)
            else []
        )
        return cls(
            session_id=str(data["session_id"]),
            title=str(data.get("title") or "新会话"),
            mode=mode,
            provider=str(data.get("provider") or "gemini"),
            model_name=str(data.get("model_name") or ""),
            workspace=str(data.get("workspace") or ""),
            knowledge_base_ids=kb_ids,
            created_at=str(data.get("created_at") or _now_iso()),
            updated_at=str(data.get("updated_at") or _now_iso()),
            usage=usage,
            history=list(data.get("history") or []),
            ui_messages=list(data.get("ui_messages") or []),
        )


class SessionStore:
    """基于本地原子 JSON 文件的多会话存储，单文件损坏不影响其他会话。"""

    def __init__(self, base_dir: Path) -> None:
        self.base_dir = base_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _path_for(self, session_id: str) -> Path:
        if not _VALID_SESSION_ID.match(session_id):
            raise ValueError(f"非法的会话 ID：{session_id}")
        return self.base_dir / f"{session_id}.json"

    def create(
        self,
        *,
        title: str = "新会话",
        mode: SessionMode = "multi",
        provider: str = "gemini",
        model_name: str = "",
        workspace: str = "",
        knowledge_base_ids: list[str] | None = None,
    ) -> SessionRecord:
        now = _now_iso()
        record = SessionRecord(
            session_id=f"sess_{uuid4().hex[:12]}",
            title=title.strip() or "新会话",
            mode=mode,
            provider=provider,
            model_name=model_name,
            workspace=workspace.strip(),
            knowledge_base_ids=list(knowledge_base_ids or []),
            created_at=now,
            updated_at=now,
        )
        self.save(record)
        return record

    def save(self, record: SessionRecord) -> None:
        record.updated_at = _now_iso()
        target = self._path_for(record.session_id)
        tmp_path = target.with_suffix(".json.tmp")
        payload = json.dumps(record.to_disk_dict(), ensure_ascii=False, indent=2)
        tmp_path.write_text(payload, encoding="utf-8")
        os.replace(tmp_path, target)

    def get(self, session_id: str) -> SessionRecord | None:
        path = self._path_for(session_id)
        if path.with_suffix(".deleted").exists() or not path.exists():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                return None
            return SessionRecord.from_disk_dict(raw)
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def list_sessions(self) -> list[SessionRecord]:
        records: list[SessionRecord] = []
        for path in self.base_dir.glob("*.json"):
            if path.with_suffix(".deleted").exists():
                continue
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(raw, dict) and "session_id" in raw:
                    records.append(SessionRecord.from_disk_dict(raw))
            except (OSError, ValueError, KeyError, TypeError):
                continue
        records.sort(key=lambda item: item.updated_at, reverse=True)
        return records

    def delete(self, session_id: str) -> bool:
        path = self._path_for(session_id)
        if not path.exists():
            return False
        # 保留原始历史。运行取消后的最后一次保存也不会复原已删除的会话。
        try:
            path.with_suffix(".deleted").touch(exist_ok=False)
        except FileExistsError:
            return False
        return True
