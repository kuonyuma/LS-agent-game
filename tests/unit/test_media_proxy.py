"""安全公网图片代理与已验证 URL 提取器的单元测试。"""

import httpx
import pytest

from agent_service.adapters.web.media_proxy import (
    MAX_PROXIED_IMAGE_BYTES,
    ImageProxyService,
    MediaProxyError,
    extract_verified_media_urls,
    validate_proxy_target,
)


def test_extract_verified_media_urls_scopes_to_trusted_formats():
    search_output = (
        "### 🖼️ 图片检索结果\n"
        "1. **Map**\n"
        "   - 图片URL: `https://upload.wikimedia.org/commons/map.png`\n"
        "   - 来源页: `https://commons.wikimedia.org/wiki/File:Map.png`\n"
        "2. **Blocked**\n"
        "   - 图片URL: `http://127.0.0.1/secret.png`\n"
    )
    urls = extract_verified_media_urls("search_images", search_output)
    assert urls == {"https://upload.wikimedia.org/commons/map.png"}

    fetch_output = (
        "### 🌐 网页内容\n"
        "正文见 [链接](https://example.org/page) 与 ![配图](https://cdn.example.org/body.jpg)\n\n"
        "#### 页面可引用配图（已验证 URL）\n"
        "- 油轮实拍: `https://cdn.example.org/tanker.webp`\n"
    )
    fetch_urls = extract_verified_media_urls("fetch_web_page", fetch_output)
    assert fetch_urls == {
        "https://cdn.example.org/body.jpg",
        "https://cdn.example.org/tanker.webp",
    }

    # 普通非搜图工具不放行任意文本链接
    assert extract_verified_media_urls("web_search", "URL: `https://evil.example/a.png`") == set()


@pytest.mark.asyncio
async def test_validate_proxy_target_blocks_private_dns_resolution():
    async def private_dns(host: str, port: int) -> list[str]:
        del host, port
        return ["10.0.0.5"]

    with pytest.raises(MediaProxyError) as exc_info:
        await validate_proxy_target(
            "https://internal-rebind.example.com/map.png",
            resolver=private_dns,
        )
    assert exc_info.value.status_code == 403

    async def mapped_v6_dns(host: str, port: int) -> list[str]:
        del host, port
        return ["::ffff:127.0.0.1"]

    with pytest.raises(MediaProxyError) as exc_info_v6:
        await validate_proxy_target(
            "https://loopback-v6.example.com/map.png",
            resolver=mapped_v6_dns,
        )
    assert exc_info_v6.value.status_code == 403

    # Clash / Mihomo TUN Fake-IP（198.18.0.0/15）经公网域名解析应放行
    async def tun_fake_ip_dns(host: str, port: int) -> list[str]:
        del host, port
        return ["198.18.237.161"]

    allowed = await validate_proxy_target(
        "https://thumb.wikimedia.org/wikipedia/commons/thumb/a.png",
        resolver=tun_fake_ip_dns,
    )
    assert allowed == "https://thumb.wikimedia.org/wikipedia/commons/thumb/a.png"

    # 但直接在 URL 中写字面量 198.18.x.x 依然禁止
    with pytest.raises(MediaProxyError):
        await validate_proxy_target("http://198.18.237.161/a.png", resolver=tun_fake_ip_dns)


@pytest.mark.asyncio
async def test_image_proxy_fetches_caches_strips_referer_and_blocks_bad_responses():
    upstream_hits = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal upstream_hits
        upstream_hits += 1
        assert "referer" not in {k.lower() for k in request.headers}

        if request.url.path == "/ok.png":
            return httpx.Response(200, content=b"\x89PNG\r\n\x1a\nfake", headers={"Content-Type": "image/png"})
        if request.url.path == "/redirect-safe":
            return httpx.Response(302, headers={"Location": "https://cdn.example.org/ok.png"})
        if request.url.path == "/redirect-ssrf":
            return httpx.Response(302, headers={"Location": "http://127.0.0.1:8000/admin"})
        if request.url.path == "/not-image":
            return httpx.Response(200, content=b"<html>no</html>", headers={"Content-Type": "text/html"})
        if request.url.path == "/too-large":
            return httpx.Response(
                200,
                content=b"0" * 16,
                headers={
                    "Content-Type": "image/jpeg",
                    "Content-Length": str(MAX_PROXIED_IMAGE_BYTES + 1),
                },
            )
        return httpx.Response(404)

    async def public_dns(host: str, port: int) -> list[str]:
        del host, port
        return ["93.184.216.34"]

    proxy = ImageProxyService(
        transport=httpx.MockTransport(handler),
        resolver=public_dns,
    )

    first = await proxy.fetch("https://cdn.example.org/ok.png")
    second = await proxy.fetch("https://cdn.example.org/ok.png")
    assert first.media_type == "image/png"
    assert second.content == first.content
    assert upstream_hits == 1  # 第二次命中缓存

    redirected = await proxy.fetch("https://cdn.example.org/redirect-safe")
    assert redirected.final_url == "https://cdn.example.org/ok.png"

    with pytest.raises(MediaProxyError) as ssrf_err:
        await proxy.fetch("https://cdn.example.org/redirect-ssrf")
    assert ssrf_err.value.status_code == 400

    with pytest.raises(MediaProxyError) as mime_err:
        await proxy.fetch("https://cdn.example.org/not-image")
    assert mime_err.value.status_code == 415

    with pytest.raises(MediaProxyError) as size_err:
        await proxy.fetch("https://cdn.example.org/too-large")
    assert size_err.value.status_code == 413


@pytest.mark.asyncio
async def test_image_proxy_uses_compliant_wikimedia_user_agent():
    seen_uas: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_uas.append(request.headers.get("user-agent", ""))
        return httpx.Response(200, content=b"\x89PNG\r\n\x1a\nok", headers={"Content-Type": "image/png"})

    async def fake_dns(host: str, port: int) -> list[str]:
        del host, port
        return ["198.18.237.161"]

    proxy = ImageProxyService(
        transport=httpx.MockTransport(handler),
        resolver=fake_dns,
    )
    img = await proxy.fetch("https://thumb.wikimedia.org/wikipedia/commons/thumb/test.png")
    assert img.media_type == "image/png"
    assert len(seen_uas) == 1
    assert seen_uas[0].startswith("AgentServiceStudio/")
