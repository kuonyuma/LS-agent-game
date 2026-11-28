"""SearchImagesTool 的单元测试：验证商业源优先级、自动回退免费百科源与尺寸/安全过滤。"""

import httpx
import pytest

from agent_service.adapters.tools.search_images import (
    SearchImagesTool,
    parse_serper_image_results,
    parse_tavily_image_results,
    parse_wikimedia_commons_results,
    parse_wikipedia_pageimages_results,
)


def test_search_images_tool_metadata():
    tool = SearchImagesTool()

    assert tool.name == "search_images"
    assert tool.read_only is True
    assert tool.requires_approval() is False
    assert "query" in tool.input_schema["required"]
    assert tool.to_function_declaration().name == "search_images"


def test_parse_serper_image_results_filters_small_private_and_duplicates():
    payload = {
        "images": [
            {
                "title": "Germany Oil Pipelines Map",
                "imageUrl": "https://cdn.example.org/maps/germany-pipelines.png",
                "thumbnailUrl": "https://cdn.example.org/maps/germany-pipelines-thumb.png",
                "source": "Energy Institute",
                "link": "https://energy.example.org/germany",
                "imageWidth": 1200,
                "imageHeight": 800,
            },
            {
                "title": "Tiny Icon Should Be Filtered",
                "imageUrl": "https://cdn.example.org/icon.png",
                "imageWidth": 64,
                "imageHeight": 64,
            },
            {
                "title": "Private IP Must Be Rejected",
                "imageUrl": "http://127.0.0.1/internal.png",
                "imageWidth": 1000,
                "imageHeight": 800,
            },
            {
                "title": "Duplicate URL",
                "imageUrl": "https://cdn.example.org/maps/germany-pipelines.png",
                "imageWidth": 1200,
                "imageHeight": 800,
            },
        ]
    }
    items = parse_serper_image_results(payload, max_results=5)
    assert len(items) == 1
    assert items[0].image_url == "https://cdn.example.org/maps/germany-pipelines.png"
    assert items[0].source_name == "Energy Institute"
    assert items[0].width == 1200


def test_parse_tavily_image_results_supports_dict_and_string():
    payload = {
        "images": [
            {
                "url": "https://images.example.net/tanker.jpg",
                "description": "Crude oil tanker docked at Wilhelmshaven port",
            },
            "https://images.example.net/refinery.webp",
            "http://localhost:8080/blocked.png",
        ]
    }
    items = parse_tavily_image_results(payload, max_results=4)
    assert len(items) == 2
    assert items[0].image_url == "https://images.example.net/tanker.jpg"
    assert "Wilhelmshaven" in items[0].description
    assert items[1].image_url == "https://images.example.net/refinery.webp"


def test_parse_wikimedia_commons_prefers_raster_thumb_and_skips_non_images():
    payload = {
        "query": {
            "pages": {
                "10": {
                    "index": 2,
                    "title": "File:Germany_Pipelines_Map.svg",
                    "imageinfo": [
                        {
                            "mime": "image/svg+xml",
                            "url": "https://upload.wikimedia.org/wikipedia/commons/a/ab/Germany_Pipelines_Map.svg",
                            "thumburl": "https://upload.wikimedia.org/wikipedia/commons/thumb/a/ab/Germany_Pipelines_Map.svg/1280px-Germany_Pipelines_Map.svg.png",
                            "thumbwidth": 1280,
                            "thumbheight": 900,
                            "descriptionurl": "https://commons.wikimedia.org/wiki/File:Germany_Pipelines_Map.svg",
                            "extmetadata": {
                                "ImageDescription": {
                                    "value": "<p>Map of <b>German</b> refineries &amp; pipelines.</p>"
                                }
                            },
                        }
                    ],
                },
                "11": {
                    "index": 1,
                    "title": "File:Ignored_Document.pdf",
                    "imageinfo": [
                        {
                            "mime": "application/pdf",
                            "url": "https://upload.wikimedia.org/wikipedia/commons/doc.pdf",
                        }
                    ],
                },
            }
        }
    }
    items = parse_wikimedia_commons_results(payload, max_results=4)
    assert len(items) == 1
    assert items[0].image_url.endswith("1280px-Germany_Pipelines_Map.svg.png")
    assert items[0].title == "Germany Pipelines Map"
    assert items[0].description == "Map of German refineries & pipelines."
    assert items[0].source_name == "Wikimedia Commons"


def test_parse_wikipedia_pageimages_results():
    payload = {
        "query": {
            "pages": {
                "1": {
                    "index": 1,
                    "title": "友谊输油管道",
                    "fullurl": "https://zh.wikipedia.org/wiki/%E5%8F%8B%E8%B0%8A%E8%BE%93%E6%B2%B9%E7%AE%A1%E9%81%93",
                    "thumbnail": {
                        "source": "https://upload.wikimedia.org/wikipedia/commons/thumb/druzhba.png/1200px-druzhba.png",
                        "width": 1200,
                        "height": 750,
                    },
                }
            }
        }
    }
    items = parse_wikipedia_pageimages_results(payload, max_results=3, lang="zh")
    assert len(items) == 1
    assert items[0].source_name == "维基百科"
    assert items[0].title == "友谊输油管道"


@pytest.mark.asyncio
async def test_search_images_priority_and_automatic_fallback_chain(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("SERPER_API_KEY", raising=False)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)

    calls: list[str] = []

    async def failing_serper(query: str, max_results: int, key: str):
        calls.append(f"serper:{key}")
        raise httpx.ConnectError("serper down")

    async def ok_tavily(query: str, max_results: int, key: str):
        calls.append(f"tavily:{key}")
        return {
            "images": [
                {
                    "url": "https://media.example.org/oil-tanker.jpg",
                    "description": "Oil tanker at European terminal",
                }
            ]
        }

    tool = SearchImagesTool(
        serper_api_key="sk-serper",
        tavily_api_key="sk-tavily",
        serper_fetcher=failing_serper,
        tavily_fetcher=ok_tavily,
    )
    result = await tool.run({"query": "oil tanker Germany", "max_results": 3})

    assert not result.is_error
    assert calls == ["serper:sk-serper", "tavily:sk-tavily"]
    assert "Tavily Images" in result.content
    assert "https://media.example.org/oil-tanker.jpg" in result.content
    assert "https://media.example.org/oil-tanker.jpg" in tool.verified_urls


@pytest.mark.asyncio
async def test_search_images_falls_back_to_wikimedia_and_zh_wikipedia_without_keys(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.delenv("SERPER_API_KEY", raising=False)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)

    seen_wiki_langs: list[str] = []

    async def fake_commons(query: str, max_results: int):
        return {
            "query": {
                "pages": {
                    "1": {
                        "index": 1,
                        "title": "File:Oil_Tanker.jpg",
                        "imageinfo": [
                            {
                                "mime": "image/jpeg",
                                "url": "https://upload.wikimedia.org/commons/tanker.jpg",
                                "thumburl": "https://upload.wikimedia.org/commons/thumb/tanker.jpg",
                                "thumbwidth": 1000,
                                "thumbheight": 640,
                                "descriptionurl": "https://commons.wikimedia.org/wiki/File:Oil_Tanker.jpg",
                            }
                        ],
                    }
                }
            }
        }

    async def fake_wiki(query: str, max_results: int, lang: str):
        seen_wiki_langs.append(lang)
        return {
            "query": {
                "pages": {
                    "2": {
                        "index": 1,
                        "title": "德国炼油厂",
                        "fullurl": "https://zh.wikipedia.org/wiki/test",
                        "thumbnail": {
                            "source": "https://upload.wikimedia.org/commons/thumb/refinery.png",
                            "width": 960,
                            "height": 600,
                        },
                    }
                }
            }
        }

    tool = SearchImagesTool(
        wikimedia_fetcher=fake_commons,
        wikipedia_fetcher=fake_wiki,
    )
    result = await tool.run({"query": "德国 输油管道 炼油厂", "max_results": 3})

    assert not result.is_error
    assert seen_wiki_langs == ["zh"]
    assert "Wikimedia Commons + 中文维基百科" in result.content
    assert "https://upload.wikimedia.org/commons/thumb/tanker.jpg" in tool.verified_urls
    assert "https://upload.wikimedia.org/commons/thumb/refinery.png" in tool.verified_urls


@pytest.mark.asyncio
async def test_search_images_rejects_invalid_params_and_reports_total_outage(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.delenv("SERPER_API_KEY", raising=False)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)

    async def broken_commons(query: str, max_results: int):
        raise httpx.TimeoutException("timeout")

    async def broken_wiki(query: str, max_results: int, lang: str):
        raise httpx.ConnectError("offline")

    tool = SearchImagesTool(
        wikimedia_fetcher=broken_commons,
        wikipedia_fetcher=broken_wiki,
    )
    assert (await tool.run({"query": "   "})).is_error
    assert (await tool.run({"query": "oil", "max_results": "two"})).is_error

    outage = await tool.run({"query": "Germany oil map"})
    assert outage.is_error
    assert "SERPER_API_KEY" in outage.content
