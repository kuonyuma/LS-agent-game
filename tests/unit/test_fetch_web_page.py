"""FetchWebPageTool 的单元测试。"""

import asyncio
import socket
from collections.abc import AsyncIterator

import httpx
import pytest

from agent_service.adapters.tools import fetch_web_page
from agent_service.adapters.tools.fetch_web_page import (
    FetchedPageResponse,
    FetchWebPageTool,
    OutputFormat,
    build_accept_header,
    convert_html_to_markdown,
    extract_images_from_html,
    extract_text_from_html,
    validate_public_http_url,
)


class PageBody(httpx.AsyncByteStream):
    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield b"<h1>Public page</h1><p>Readable text</p>"


def test_html_metadata_and_navigation_void_tags_preserve_article():
    page = """
    <html><head><meta charset="utf-8"><link rel="stylesheet" href="theme.css">
    <title>Article title</title></head><body>
    <nav><img src="menu.png"><br><span>Navigation only</span></nav>
    <main><h1>Article title</h1><p>Useful article text.</p>
    <img src="article.jpg" width="800" height="500"></main>
    </body></html>
    """
    title, markdown = convert_html_to_markdown(page)
    text = extract_text_from_html(page)
    images = extract_images_from_html(page, "https://articles.example/page")
    assert title == "Article title"
    for body in (markdown, text):
        assert "Useful article text." in body
        assert "Navigation only" not in body
    assert [image.url for image in images] == ["https://articles.example/article.jpg"]


@pytest.mark.parametrize("can_read_destination", [True, False])
@pytest.mark.asyncio
async def test_direct_fetch_checks_each_destination_before_connecting(monkeypatch, can_read_destination):
    requested_hosts: list[str] = []
    resolved_hosts: list[str] = []

    async def resolve(host: str, port: int, **_: object):
        resolved_hosts.append(host)
        result = "93.184.216.34" if host == "start.example" or can_read_destination else "192.0.2.1"
        return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", (result, port))]

    def respond(request: httpx.Request) -> httpx.Response:
        host = request.headers["host"]
        requested_hosts.append(host)
        assert request.url.host == "93.184.216.34"
        assert request.extensions["sni_hostname"] == host
        if host == "start.example":
            return httpx.Response(302, headers={"Location": "https://destination.example/article"})
        return httpx.Response(200, headers={"Content-Type": "text/html"}, stream=PageBody())

    async def unused_provider(*_: object):
        pytest.fail("共享抓取不应调用 DeepSeek")

    original_client = httpx.AsyncClient
    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", resolve)
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kwargs: original_client(transport=httpx.MockTransport(respond), **kwargs)
    )
    tool = FetchWebPageTool(
        allow_deepseek=False, deepseek_api_key="test-key", deepseek_fetcher=unused_provider
    )
    outcome = await tool.run({"url": "https://start.example/article"})
    assert resolved_hosts == ["start.example", "destination.example"]
    if can_read_destination:
        assert requested_hosts == resolved_hosts
        assert not outcome.is_error
        assert "Readable text" in outcome.content
    else:
        assert requested_hosts == ["start.example"]
        assert outcome.is_error
        assert "公网单播" in outcome.content


@pytest.mark.asyncio
async def test_direct_fetch_limits_body_without_content_length(monkeypatch):
    async def resolve(_: httpx.URL) -> str:
        return "93.184.216.34"

    client = httpx.AsyncClient
    transport = httpx.MockTransport(lambda _: httpx.Response(200, stream=PageBody()))
    monkeypatch.setattr(fetch_web_page, "_public_ip_for", resolve)
    monkeypatch.setattr(fetch_web_page, "MAX_RESPONSE_BYTES", 20)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client(transport=transport, **kwargs))
    result = await FetchWebPageTool(allow_deepseek=False).run({"url": "https://start.example/article"})
    assert result.is_error
    assert "响应体积过大" in result.content

SAMPLE_ARTICLE_HTML = """
<!DOCTYPE html>
<html>
<head>
  <title>StackOverflow - Asyncio Gather Example</title>
  <style>body { color: black; }</style>
  <script>console.log("tracking");</script>
</head>
<body>
  <nav><a href="/home">Home Navigation</a></nav>
  <main>
    <h1>How to run coroutines concurrently?</h1>
    <p>Use <code>asyncio.gather</code> to run tasks in parallel. See <a href="/docs/asyncio">official docs</a>.</p>
    <pre><code>import asyncio

async def main():
    await asyncio.gather(task1(), task2())
</code></pre>
    <ul>
      <li>First item</li>
      <li>Second item</li>
    </ul>
  </main>
  <footer>Footer copyright</footer>
</body>
</html>
"""


def test_fetch_web_page_tool_metadata():
    tool = FetchWebPageTool()

    assert tool.name == "fetch_web_page"
    assert tool.read_only is True
    assert "url" in tool.input_schema["required"]
    assert tool.to_function_declaration().name == "fetch_web_page"


@pytest.mark.parametrize(
    "blocked_url",
    [
        "",
        "ftp://example.com/file.txt",
        "file:///etc/passwd",
        "http://localhost:8000/runs",
        "http://127.0.0.1:3307/",
        "http://10.0.0.1/admin",
        "http://172.16.0.5/internal",
        "http://192.168.1.1/router",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]:8000/",
        "https://service.local/api",
    ],
)
def test_validate_public_http_url_blocks_ssrf_and_invalid_schemes(blocked_url: str):
    with pytest.raises(ValueError):
        validate_public_http_url(blocked_url)


def test_validate_public_http_url_allows_public_https():
    url = "https://docs.python.org/3/library/asyncio.html"
    assert validate_public_http_url(url) == url


def test_build_accept_header_matches_opencode_priorities():
    assert build_accept_header("markdown").startswith("text/markdown;q=1.0")
    assert build_accept_header("text").startswith("text/plain;q=1.0")
    assert build_accept_header("html").startswith("text/html;q=1.0")


def test_convert_html_to_markdown_preserves_headings_code_and_strips_noise():
    title, md = convert_html_to_markdown(
        SAMPLE_ARTICLE_HTML,
        base_url="https://stackoverflow.com/questions/1",
    )

    assert title == "StackOverflow - Asyncio Gather Example"
    assert "# How to run coroutines concurrently?" in md
    assert "`asyncio.gather`" in md
    assert "[official docs](https://stackoverflow.com/docs/asyncio)" in md
    assert "```\nimport asyncio" in md
    assert "- First item" in md
    assert "tracking" not in md
    assert "Home Navigation" not in md
    assert "Footer copyright" not in md


def test_extract_text_from_html_strips_scripts_and_tags():
    text = extract_text_from_html(SAMPLE_ARTICLE_HTML)

    assert "How to run coroutines concurrently?" in text
    assert "tracking" not in text
    assert "color: black" not in text


@pytest.mark.asyncio
async def test_fetch_web_page_markdown_and_truncation():
    async def fake_fetcher(
        url: str,
        output_format: OutputFormat,
        timeout_seconds: float,
    ) -> FetchedPageResponse:
        assert url == "https://stackoverflow.com/questions/1"
        assert output_format == "markdown"
        assert timeout_seconds == 30.0
        return FetchedPageResponse(
            url=url,
            status_code=200,
            content_type="text/html; charset=utf-8",
            text=SAMPLE_ARTICLE_HTML,
        )

    tool = FetchWebPageTool(fetcher=fake_fetcher)
    result = await tool.run({"url": "https://stackoverflow.com/questions/1"})

    assert not result.is_error
    assert "StackOverflow - Asyncio Gather Example" in result.content
    assert "# How to run coroutines concurrently?" in result.content
    assert "await asyncio.gather(task1(), task2())" in result.content


@pytest.mark.asyncio
async def test_fetch_web_page_truncates_when_exceeding_max_chars():
    long_body = "<p>" + ("A" * 1200) + "</p>"

    async def fake_fetcher(
        url: str,
        output_format: OutputFormat,
        timeout_seconds: float,
    ) -> FetchedPageResponse:
        return FetchedPageResponse(
            url=url,
            status_code=200,
            content_type="text/html",
            text=long_body,
        )

    tool = FetchWebPageTool(fetcher=fake_fetcher)
    result = await tool.run(
        {"url": "https://example.com/long", "max_chars": 600}
    )

    assert not result.is_error
    assert "网页内容已按 max_chars=600 截断" in result.content


@pytest.mark.asyncio
async def test_fetch_web_page_rejects_ssrf_and_invalid_format():
    tool = FetchWebPageTool()

    ssrf_res = await tool.run({"url": "http://127.0.0.1:8000/health"})
    assert ssrf_res.is_error
    assert "安全策略禁止访问" in ssrf_res.content

    fmt_res = await tool.run(
        {"url": "https://example.com", "format": "pdf"}
    )
    assert fmt_res.is_error
    assert "不支持的 format" in fmt_res.content


@pytest.mark.asyncio
async def test_fetch_web_page_handles_http_errors():
    async def error_fetcher(
        url: str,
        output_format: OutputFormat,
        timeout_seconds: float,
    ) -> FetchedPageResponse:
        request = httpx.Request("GET", url)
        response = httpx.Response(404, request=request)
        raise httpx.HTTPStatusError("404 Not Found", request=request, response=response)

    tool = FetchWebPageTool(fetcher=error_fetcher)
    result = await tool.run({"url": "https://example.com/missing"})

    assert result.is_error
    assert "HTTP 404" in result.content


@pytest.mark.asyncio
async def test_fetch_web_page_uses_deepseek_fetch_api_for_markdown():
    async def fake_ds_fetch(url: str, timeout: float, api_key: str):
        assert url == "https://example.com/article"
        assert timeout == 30.0
        assert api_key == "sk-ds-test"
        return {
            "title": "DeepSeek Fetch 文章标题",
            "content": "# 标题\n\n这是由 DeepSeek Fetch 返回的 Markdown 正文。",
        }

    tool = FetchWebPageTool(
        deepseek_api_key="sk-ds-test",
        deepseek_fetcher=fake_ds_fetch,
    )
    result = await tool.run({"url": "https://example.com/article"})

    assert not result.is_error
    assert "DeepSeek Fetch API" in result.content
    assert "DeepSeek Fetch 文章标题" in result.content
    assert "Markdown 正文" in result.content


def test_extract_images_from_html_filters_noise_and_resolves_relative_urls():
    html = """
    <html><body>
      <nav><img src="/logo.png" width="400" height="200" alt="Nav Logo" /></nav>
      <main>
        <img src="/assets/pipeline-map.png" width="900" height="600" alt="德国输油管道地图" />
        <img data-src="https://cdn.example.org/tanker.webp" alt="大型油轮" />
        <img src="/assets/tiny-icon.png" width="32" height="32" alt="Tiny" />
        <img src="data:image/png;base64,AAAA" alt="Inline" />
        <img src="http://127.0.0.1/private.png" alt="Local" />
      </main>
    </body></html>
    """
    images = extract_images_from_html(html, base_url="https://energy.example.org/report")
    assert [img.url for img in images] == [
        "https://energy.example.org/assets/pipeline-map.png",
        "https://cdn.example.org/tanker.webp",
    ]
    assert images[0].alt == "德国输油管道地图"


@pytest.mark.asyncio
async def test_fetch_web_page_appends_verified_images_section():
    html = """
    <html><head><title>Energy Report</title></head><body>
      <main>
        <h1>Germany Oil Transport</h1>
        <p>Crude oil arrives via ports and pipelines.</p>
        <img src="/maps/refineries.png" width="1024" height="768" alt="炼油厂管网图" />
      </main>
    </body></html>
    """

    async def fake_fetcher(url: str, output_format: OutputFormat, timeout_seconds: float):
        del output_format, timeout_seconds
        return FetchedPageResponse(
            url=url,
            status_code=200,
            content_type="text/html; charset=utf-8",
            text=html,
        )

    tool = FetchWebPageTool(fetcher=fake_fetcher)
    result = await tool.run({"url": "https://energy.example.org/germany"})
    assert not result.is_error
    assert "页面可引用配图" in result.content
    assert "https://energy.example.org/maps/refineries.png" in result.content
