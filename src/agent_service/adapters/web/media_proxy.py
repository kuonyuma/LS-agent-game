"""受控的公网图片代理与已验证媒体 URL 提取器。

安全约束：
1. 仅允许代理会话工具输出中实际出现过的图片 URL（阻止开放代理与模型臆造链接）。
2. 在发请求前及每次 3xx 重定向落点均执行公网 URL 规则 + DNS 解析校验，拒绝任何回环、私网、链路本地与保留 IP（防 SSRF）。
3. 严格限制图片 MIME 白名单与 8MB 体积上限，不透传客户端 Cookie/Referer（解决防盗链 403）。
"""

import asyncio
import ipaddress
import re
import socket
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

from agent_service.adapters.tools.fetch_web_page import validate_public_http_url

MAX_PROXIED_IMAGE_BYTES = 8 * 1024 * 1024  # 8 MB
DEFAULT_PROXY_TIMEOUT = 15.0
MAX_PROXY_REDIRECTS = 3
ALLOWED_IMAGE_MIMES = frozenset(
    {
        "image/jpeg",
        "image/jpg",
        "image/png",
        "image/webp",
        "image/gif",
        "image/avif",
        "image/svg+xml",
    }
)
_BROWSER_PROXY_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/143.0.0.0 Safari/537.36"
    ),
    "Accept": "image/avif,image/webp,image/png,image/jpeg,image/gif,*/*;q=0.5",
}
_WIKIMEDIA_PROXY_HEADERS = {
    "User-Agent": (
        "AgentServiceStudio/2.3 "
        "(https://github.com/kuonyuma/agent-service; image-proxy)"
    ),
    "Accept": "image/avif,image/webp,image/png,image/jpeg,image/gif,*/*;q=0.5",
}
# Clash / Mihomo / sing-box TUN Fake-IP 模式使用的 RFC 2544 网段（仅允许经公网域名 DNS 解析命中）
_TUN_FAKE_IP_NETWORK = ipaddress.ip_network("198.18.0.0/15")

_SEARCH_IMAGE_URL_RE = re.compile(r"图片URL:\s*`(https?://[^`\s]+)`")
_PAGE_IMAGE_SECTION_RE = re.compile(
    r"####\s*页面可引用配图[^\n]*\n((?:-\s+[^\n]+\n?)+)"
)
_BACKTICK_HTTP_URL_RE = re.compile(r"`(https?://[^`\s]+)`")
_MARKDOWN_IMAGE_URL_RE = re.compile(r"!\[[^\]]*\]\((https?://[^)\s]+)(?:\s+\"[^\"]*\")?\)")

HostResolver = Callable[[str, int], Awaitable[list[str]]]


class MediaProxyError(Exception):
    """媒体代理拉取或校验失败。"""

    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class ProxiedImage:
    """代理成功后的图片二进制载荷与元数据。"""

    content: bytes
    media_type: str
    final_url: str


def extract_verified_media_urls(tool_name: str, result_text: str) -> set[str]:
    """从受信任工具输出中提取已验证的公网图片 URL 集合。"""
    if not result_text:
        return set()

    candidates: set[str] = set()
    if tool_name == "search_images":
        candidates.update(_SEARCH_IMAGE_URL_RE.findall(result_text))
    elif tool_name == "fetch_web_page":
        for section_match in _PAGE_IMAGE_SECTION_RE.finditer(result_text):
            candidates.update(_BACKTICK_HTTP_URL_RE.findall(section_match.group(1)))
        candidates.update(_MARKDOWN_IMAGE_URL_RE.findall(result_text))
    elif tool_name == "subagent":
        # 仅吸收子代理报告中按约定格式转述的图片 URL，避免放行任意普通网页链接
        candidates.update(_SEARCH_IMAGE_URL_RE.findall(result_text))
        for section_match in _PAGE_IMAGE_SECTION_RE.finditer(result_text):
            candidates.update(_BACKTICK_HTTP_URL_RE.findall(section_match.group(1)))

    verified: set[str] = set()
    for raw in candidates:
        try:
            verified.add(validate_public_http_url(raw))
        except ValueError:
            continue
    return verified


def _headers_for_url(url: str) -> dict[str, str]:
    """针对 Wikimedia/Wikipedia 使用合规应用 UA（避免 429），其余站点使用通用浏览器 UA。"""
    host = (urlparse(url).hostname or "").strip().lower()
    if host in {"wikimedia.org", "wikipedia.org"} or host.endswith(
        (".wikimedia.org", ".wikipedia.org")
    ):
        return _WIKIMEDIA_PROXY_HEADERS
    return _BROWSER_PROXY_HEADERS


def _is_forbidden_ip(raw_ip: str, *, allow_tun_fake_ip: bool = False) -> bool:
    try:
        ip = ipaddress.ip_address(raw_ip)
    except ValueError:
        return True
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if (
        allow_tun_fake_ip
        and isinstance(ip, ipaddress.IPv4Address)
        and ip in _TUN_FAKE_IP_NETWORK
    ):
        return False
    return (
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


async def _default_host_resolver(hostname: str, port: int) -> list[str]:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    ips: list[str] = []
    for info in infos:
        sockaddr = info[4]
        if sockaddr and isinstance(sockaddr[0], str):
            ips.append(sockaddr[0])
    return ips


async def validate_proxy_target(
    raw_url: str,
    *,
    resolver: HostResolver = _default_host_resolver,
) -> str:
    """执行 URL 语法、协议与 DNS 解析 IP 的多层 SSRF 校验。"""
    try:
        url = validate_public_http_url(raw_url)
    except ValueError as exc:
        raise MediaProxyError(str(exc), status_code=400) from exc

    parsed = urlparse(url)
    hostname = (parsed.hostname or "").strip().lower()
    port = parsed.port or (443 if parsed.scheme == "https" else 80)

    # 若本身是字面量 IP，validate_public_http_url 已做检查，再兜底验证一次
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        try:
            resolved_ips = await resolver(hostname, port)
        except OSError as exc:
            raise MediaProxyError(f"无法解析图片主机名：{hostname}", status_code=502) from exc
        if not resolved_ips:
            raise MediaProxyError(f"图片主机名未解析到有效地址：{hostname}", status_code=502)
        for ip_str in resolved_ips:
            if _is_forbidden_ip(ip_str, allow_tun_fake_ip=True):
                raise MediaProxyError(
                    f"安全策略禁止代理内网或回环目标地址：{hostname}",
                    status_code=403,
                )
    else:
        if _is_forbidden_ip(hostname, allow_tun_fake_ip=False):
            raise MediaProxyError(
                f"安全策略禁止代理内网或回环目标地址：{hostname}",
                status_code=403,
            )

    return url


def _normalize_image_mime(raw_content_type: str) -> str:
    mime = raw_content_type.split(";", 1)[0].strip().lower()
    if mime == "image/jpg":
        return "image/jpeg"
    return mime


class ImageProxyService:
    """带受限内存 LRU 缓存的安全公网图片代理服务。"""

    def __init__(
        self,
        *,
        max_entries: int = 64,
        max_total_bytes: int = 32 * 1024 * 1024,
        transport: httpx.AsyncBaseTransport | None = None,
        resolver: HostResolver = _default_host_resolver,
        timeout: float = DEFAULT_PROXY_TIMEOUT,
    ) -> None:
        self._max_entries = max(1, max_entries)
        self._max_total_bytes = max(MAX_PROXIED_IMAGE_BYTES, max_total_bytes)
        self._transport = transport
        self._resolver = resolver
        self._timeout = timeout
        self._cache: OrderedDict[str, ProxiedImage] = OrderedDict()
        self._cached_bytes = 0

    def _cache_get(self, url: str) -> ProxiedImage | None:
        item = self._cache.get(url)
        if item is not None:
            self._cache.move_to_end(url)
        return item

    def _cache_put(self, url: str, image: ProxiedImage) -> None:
        size = len(image.content)
        if size > self._max_total_bytes // 2:
            return
        existing = self._cache.pop(url, None)
        if existing is not None:
            self._cached_bytes -= len(existing.content)
        while self._cache and (
            len(self._cache) >= self._max_entries
            or self._cached_bytes + size > self._max_total_bytes
        ):
            _, evicted = self._cache.popitem(last=False)
            self._cached_bytes -= len(evicted.content)
        self._cache[url] = image
        self._cached_bytes += size

    async def fetch(self, raw_url: str) -> ProxiedImage:
        url = await validate_proxy_target(raw_url, resolver=self._resolver)
        cached = self._cache_get(url)
        if cached is not None:
            return cached

        client_kwargs: dict[str, Any] = {
            "timeout": self._timeout,
            "follow_redirects": False,
        }
        if self._transport is not None:
            client_kwargs["transport"] = self._transport

        current_url = url
        async with httpx.AsyncClient(**client_kwargs) as client:
            for _ in range(MAX_PROXY_REDIRECTS + 1):
                try:
                    async with client.stream(
                        "GET", current_url, headers=_headers_for_url(current_url)
                    ) as response:
                        if response.status_code in {301, 302, 303, 307, 308}:
                            location = response.headers.get("location", "").strip()
                            if not location:
                                raise MediaProxyError("上游图片重定向缺少目标地址。", status_code=502)
                            next_url = urljoin(current_url, location)
                            current_url = await validate_proxy_target(
                                next_url, resolver=self._resolver
                            )
                            continue

                        if response.status_code >= 400:
                            raise MediaProxyError(
                                f"上游图片加载失败（HTTP {response.status_code}）。",
                                status_code=502,
                            )

                        media_type = _normalize_image_mime(
                            response.headers.get("content-type", "")
                        )
                        if media_type not in ALLOWED_IMAGE_MIMES:
                            raise MediaProxyError(
                                f"上游响应不是受支持的图片类型（{media_type or 'unknown'}）。",
                                status_code=415,
                            )

                        content_length = response.headers.get("content-length")
                        if (
                            content_length
                            and content_length.isdigit()
                            and int(content_length) > MAX_PROXIED_IMAGE_BYTES
                        ):
                            raise MediaProxyError("图片体积超过 8MB 上限。", status_code=413)

                        chunks: list[bytes] = []
                        total = 0
                        async for chunk in response.aiter_bytes():
                            total += len(chunk)
                            if total > MAX_PROXIED_IMAGE_BYTES:
                                raise MediaProxyError("图片体积超过 8MB 上限。", status_code=413)
                            chunks.append(chunk)

                        content = b"".join(chunks)
                        if not content:
                            raise MediaProxyError("上游返回空图片内容。", status_code=502)

                        image = ProxiedImage(
                            content=content,
                            media_type=media_type,
                            final_url=current_url,
                        )
                        self._cache_put(url, image)
                        return image
                except MediaProxyError:
                    raise
                except httpx.TimeoutException as exc:
                    raise MediaProxyError("拉取远程图片超时。", status_code=504) from exc
                except httpx.HTTPError as exc:
                    raise MediaProxyError(f"拉取远程图片网络出错：{exc}", status_code=502) from exc

        raise MediaProxyError("图片重定向次数过多。", status_code=502)
