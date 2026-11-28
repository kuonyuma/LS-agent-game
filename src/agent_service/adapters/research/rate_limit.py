"""学术 API 异步限速器、轻量 TTL/LRU 缓存与统一礼貌请求头工具。"""

from __future__ import annotations

import asyncio
import os
import time
from collections import OrderedDict

DEFAULT_USER_AGENT_PREFIX = "LS-Agent-CS-Research/1.0"


def resolve_contact_email(explicit_email: str | None = None) -> str:
    """获取可选的学术 API 联系邮箱（用于 Crossref Polite Pool）。"""
    if explicit_email and explicit_email.strip():
        return explicit_email.strip()
    return os.getenv("ACADEMIC_CONTACT_EMAIL", "").strip()


def build_research_headers(
    *,
    contact_email: str | None = None,
    extra: dict[str, str] | None = None,
) -> dict[str, str]:
    """构建礼貌且符合学术平台规范的 HTTP User-Agent 请求头。"""
    email = resolve_contact_email(contact_email)
    ua = (
        f"{DEFAULT_USER_AGENT_PREFIX} (mailto:{email})"
        if email
        else f"{DEFAULT_USER_AGENT_PREFIX} (+https://github.com/kuonyuma/agent-service)"
    )
    headers = {
        "User-Agent": ua,
        "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8",
    }
    if extra:
        headers.update(extra)
    return headers


class AsyncRateLimiter:
    """基于单调时钟的进程级异步最小间隔限速器。"""

    def __init__(self, min_interval_seconds: float) -> None:
        self.min_interval = max(0.0, float(min_interval_seconds))
        self._lock = asyncio.Lock()
        self._last_ts: float = 0.0

    async def acquire(self) -> None:
        if self.min_interval <= 0.0:
            return
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_ts
            if self._last_ts > 0.0 and elapsed < self.min_interval:
                await asyncio.sleep(self.min_interval - elapsed)
            self._last_ts = time.monotonic()


class TTLCache[T]:
    """线程安全边界清晰的轻量 LRU + TTL 内存缓存。"""

    def __init__(self, *, max_size: int = 128, ttl_seconds: float = 1800.0) -> None:
        self._max_size = max(1, int(max_size))
        self._ttl = max(1.0, float(ttl_seconds))
        self._entries: OrderedDict[str, tuple[float, T]] = OrderedDict()

    def get(self, key: str) -> T | None:
        item = self._entries.get(key)
        if item is None:
            return None
        expires_at, value = item
        if time.monotonic() > expires_at:
            self._entries.pop(key, None)
            return None
        self._entries.move_to_end(key)
        return value

    def set(self, key: str, value: T) -> None:
        self._entries[key] = (time.monotonic() + self._ttl, value)
        self._entries.move_to_end(key)
        while len(self._entries) > self._max_size:
            self._entries.popitem(last=False)

    def clear(self) -> None:
        self._entries.clear()


# 各学术源默认共享限速器（防止多工具/多子代理并发击穿上游限流）
_DEFAULT_LIMITERS: dict[str, AsyncRateLimiter] = {
    "arxiv": AsyncRateLimiter(3.05),
    "openalex": AsyncRateLimiter(0.15),
    "dblp": AsyncRateLimiter(1.1),
    "crossref": AsyncRateLimiter(1.1),
    "opencitations": AsyncRateLimiter(0.45),
    "semantic_scholar": AsyncRateLimiter(1.05),
}


def get_default_limiter(source: str) -> AsyncRateLimiter:
    limiter = _DEFAULT_LIMITERS.get(source)
    if limiter is None:
        limiter = AsyncRateLimiter(0.5)
        _DEFAULT_LIMITERS[source] = limiter
    return limiter
