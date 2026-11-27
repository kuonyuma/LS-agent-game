"""OpenAI 兼容协议适配器（基于官方 openai SDK，覆盖 DeepSeek、Kimi 等厂商）。"""

from agent_service.adapters.openai_compat.model import OpenAICompatibleModel
from agent_service.adapters.openai_compat.reviewer import OpenAICompatibleTextReviewer

__all__ = ["OpenAICompatibleModel", "OpenAICompatibleTextReviewer"]
