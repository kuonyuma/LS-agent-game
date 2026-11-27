import os

from google import genai

from agent_service.config.settings import settings
from agent_service.errors import ConfigurationError

client: genai.Client | None = None


def resolve_gemini_api_key(api_key: str | None = None) -> str:
    """解析 Gemini API Key：显式传入 > GEMINI_API_KEY 环境变量 > config.yaml 回退。"""

    if api_key and api_key.strip():
        return api_key.strip()

    gemini_api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not gemini_api_key:
        gemini_api_key = settings.model_config.key.strip()

    if gemini_api_key == "" or gemini_api_key.startswith("your"):
        raise ConfigurationError(
            "请配置 GEMINI_API_KEY 环境变量，或在设置中填入 Google Gemini API Key"
        )
    return gemini_api_key


def create_client(api_key: str | None = None) -> genai.Client:
    """创建独立的 Gemini 客户端实例（用于按请求覆盖 API Key）。"""

    return genai.Client(api_key=resolve_gemini_api_key(api_key))


def get_client() -> genai.Client:
    global client
    if client is not None:
        return client

    client = genai.Client(api_key=resolve_gemini_api_key())
    return client


async def close_genai_client(target: genai.Client | None) -> None:
    """释放指定 Gemini 客户端的异步与同步连接资源。"""

    if target is None:
        return
    try:
        await target.aio.aclose()
    finally:
        target.close()


async def close_client() -> None:
    """关闭共享 Gemini 客户端，并允许后续重新初始化。"""

    global client
    current_client = client
    client = None
    await close_genai_client(current_client)
