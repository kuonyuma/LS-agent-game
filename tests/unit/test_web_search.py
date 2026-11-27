"""WebSearchTool 的单元测试。"""

import httpx
import pytest

from agent_service.adapters.tools.web_search import (
    WebSearchTool,
    parse_deepseek_search_results,
    parse_duckduckgo_html,
    unwrap_search_url,
)

SAMPLE_DDG_HTML = """
<!DOCTYPE html>
<html>
<body>
  <div class="results">
    <div class="result results_links results_links_deep web-result">
      <h2 class="result__title">
        <a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fdocs.python.org%2F3%2Flibrary%2Fasyncio.html&amp;rut=abc">
          <b>asyncio</b> — Asynchronous I/O — Python 3.14 documentation
        </a>
      </h2>
      <a class="result__snippet" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fdocs.python.org%2F3%2Flibrary%2Fasyncio.html">
        <b>asyncio</b> is used as a foundation for multiple Python asynchronous frameworks.
      </a>
    </div>

    <div class="result results_links results_links_deep web-result">
      <h2 class="result__title">
        <a rel="nofollow" class="result__a" href="https://stackoverflow.com/questions/12345/python-asyncio-gather">
          How to use asyncio.gather with timeout in Python?
        </a>
      </h2>
      <div class="result__snippet">
        You can combine <b>asyncio.timeout</b> with gather to bound concurrent tasks.
      </div>
    </div>

    <div class="result results_links results_links_deep web-result">
      <h2 class="result__title">
        <a rel="nofollow" class="result__a" href="https://github.com/anomalyco/opencode/issues/1">
          Duplicate Check Issue
        </a>
      </h2>
      <div class="result__snippet">First snippet for github issue.</div>
    </div>

    <div class="result results_links results_links_deep web-result">
      <h2 class="result__title">
        <a rel="nofollow" class="result__a" href="https://github.com/anomalyco/opencode/issues/1">
          Duplicate Check Issue Again
        </a>
      </h2>
      <div class="result__snippet">Duplicate URL should be deduplicated.</div>
    </div>
  </div>
</body>
</html>
"""


def test_web_search_tool_metadata():
    tool = WebSearchTool()

    assert tool.name == "web_search"
    assert tool.read_only is True
    assert "query" in tool.input_schema["required"]
    assert tool.to_function_declaration().name == "web_search"


def test_unwrap_search_url_decodes_duckduckgo_redirect():
    raw = "//duckduckgo.com/l/?uddg=https%3A%2F%2Fgithub.com%2Fanomalyco%2Fopencode&rut=123"
    assert unwrap_search_url(raw) == "https://github.com/anomalyco/opencode"
    assert unwrap_search_url("https://example.com/docs") == "https://example.com/docs"


def test_parse_duckduckgo_html_extracts_and_deduplicates_results():
    items = parse_duckduckgo_html(SAMPLE_DDG_HTML, max_results=5)

    assert len(items) == 3
    assert items[0].title == "asyncio — Asynchronous I/O — Python 3.14 documentation"
    assert items[0].url == "https://docs.python.org/3/library/asyncio.html"
    assert "foundation for multiple Python asynchronous frameworks" in items[0].snippet

    assert items[1].url == "https://stackoverflow.com/questions/12345/python-asyncio-gather"
    assert items[2].url == "https://github.com/anomalyco/opencode/issues/1"


@pytest.mark.asyncio
async def test_web_search_returns_formatted_results_and_respects_max_results():
    async def fake_fetcher(query: str, max_results: int) -> str:
        assert query == "python asyncio gather"
        assert max_results == 2
        return SAMPLE_DDG_HTML

    tool = WebSearchTool(fetcher=fake_fetcher)
    result = await tool.run({"query": "  python asyncio gather  ", "max_results": 2})

    assert not result.is_error
    assert "python asyncio gather" in result.content
    assert "https://docs.python.org/3/library/asyncio.html" in result.content
    assert "https://stackoverflow.com/questions/12345/python-asyncio-gather" in result.content
    assert "https://github.com/anomalyco/opencode/issues/1" not in result.content
    assert "fetch_web_page" in result.content


@pytest.mark.asyncio
async def test_web_search_rejects_empty_query_or_invalid_max_results():
    tool = WebSearchTool()

    empty_res = await tool.run({"query": "   "})
    assert empty_res.is_error
    assert "不能为空" in empty_res.content

    bad_max = await tool.run({"query": "python", "max_results": "five"})
    assert bad_max.is_error
    assert "整数" in bad_max.content


@pytest.mark.asyncio
async def test_web_search_handles_network_timeout_gracefully():
    async def timeout_fetcher(query: str, max_results: int) -> str:
        raise httpx.TimeoutException("timed out")

    tool = WebSearchTool(fetcher=timeout_fetcher)
    result = await tool.run({"query": "fastapi lifespan"})

    assert result.is_error
    assert "超时" in result.content


def test_parse_deepseek_search_results_prefers_summary_and_deduplicates():
    payload = {
        "results": [
            {
                "rerank_score": 2.4,
                "title": "DeepSeek V4 发布",
                "url": "https://example.com/v4",
                "snippet": "简短摘要",
                "summary": "面向模型的完整长摘要内容，包含关键细节。",
                "site_name": "科技资讯",
                "date": "2026-11-18T12:00:00+00:00",
            },
            {
                "title": "重复链接应去重",
                "url": "https://example.com/v4",
                "snippet": "duplicate",
            },
            {
                "title": "仅含 snippet 的条目",
                "url": "https://example.com/other",
                "snippet": "回退到 snippet",
                "summary": "",
                "site_name": None,
                "date": None,
            },
        ]
    }
    items = parse_deepseek_search_results(payload, max_results=5)
    assert len(items) == 2
    assert items[0].snippet == "面向模型的完整长摘要内容，包含关键细节。"
    assert items[0].site_name == "科技资讯"
    assert items[0].published_at == "2026-11-18"
    assert items[1].snippet == "回退到 snippet"


@pytest.mark.asyncio
async def test_web_search_uses_deepseek_api_when_key_present():
    captured: dict[str, object] = {}

    async def fake_ds_fetcher(query: str, max_results: int, api_key: str):
        captured["query"] = query
        captured["max_results"] = max_results
        captured["api_key"] = api_key
        return {
            "results": [
                {
                    "title": "英伟达最新财报解读",
                    "url": "https://example.com/nvda",
                    "summary": "英伟达公布最新季度营收与数据中心业务增速。",
                    "site_name": "财经头条",
                    "date": "2026-11-20T08:00:00+00:00",
                }
            ]
        }

    tool = WebSearchTool(
        deepseek_api_key="sk-ds-test",
        deepseek_fetcher=fake_ds_fetcher,
    )
    result = await tool.run({"query": "英伟达最新财报", "max_results": 3})

    assert not result.is_error
    assert captured == {
        "query": "英伟达最新财报",
        "max_results": 3,
        "api_key": "sk-ds-test",
    }
    assert "DeepSeek Search API" in result.content
    assert "英伟达最新财报解读" in result.content
    assert "财经头条 · 2026-11-20" in result.content


@pytest.mark.asyncio
async def test_web_search_reports_deepseek_auth_error():
    async def unauthorized_ds(query: str, max_results: int, api_key: str):
        _ = (query, max_results, api_key)
        req = httpx.Request("POST", "https://api.deepseek.com/v1/tools/search")
        resp = httpx.Response(401, request=req)
        raise httpx.HTTPStatusError("401", request=req, response=resp)

    tool = WebSearchTool(
        deepseek_api_key="sk-bad",
        deepseek_fetcher=unauthorized_ds,
    )
    result = await tool.run({"query": "test"})
    assert result.is_error
    assert "HTTP 401" in result.content
    assert "无效" in result.content
