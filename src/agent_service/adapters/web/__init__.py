"""Web 交互适配器：将内核 LoopEvent 流与人工审批暴露为 FastAPI + SSE 接口。"""

from agent_service.adapters.web.app import create_web_app

__all__ = ["create_web_app"]
