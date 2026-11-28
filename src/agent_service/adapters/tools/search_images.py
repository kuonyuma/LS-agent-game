"""只读联网搜图工具：优先 Serper / Tavily 商业搜图 API，无 Key 或失败时自动回退 Wikimedia Commons + Wikipedia 免费图源。"""

import os
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from html import unescape
from typing import Any, ClassVar
from urllib.parse import urlparse

import httpx

from agent_service.adapters.tools.fetch_web_page import validate_public_http_url
from agent_service.kernel.dispatch.base import Tool, ToolResult

DEFAULT_MAX_IMAGE_RESULTS = 4
MAX_ALLOWED_IMAGE_RESULTS = 8
DEFAULT_IMAGE_SEARCH_TIMEOUT = 15.0
MIN_IMAGE_DIMENSION = 240

SERPER_IMAGES_ENDPOINT = "https://google.serper.dev/images"
TAVILY_SEARCH_ENDPOINT = "https://api.tavily.com/search"
WIKIMEDIA_COMMONS_API = "https://commons.wikimedia.org/w/api.php"
WIKIPEDIA_ZH_API = "https://zh.wikipedia.org/w/api.php"
WIKIPEDIA_EN_API = "https://en.wikipedia.org/w/api.php"

_ALLOWED_COMMONS_MIMES = frozenset(
    {
        "image/jpeg",
        "image/png",
        "image/webp",
        "image/gif",
        "image/svg+xml",
    }
)
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_WIKIMEDIA_HEADERS = {
    "User-Agent": "AgentServiceStudio/2.3 (https://github.com/kuonyuma/agent-service; image-search)",
    "Accept": "application/json",
}

SerperFetcher = Callable[[str, int, str], Awaitable[dict[str, Any]]]
TavilyFetcher = Callable[[str, int, str], Awaitable[dict[str, Any]]]
WikimediaFetcher = Callable[[str, int], Awaitable[dict[str, Any]]]
WikipediaFetcher = Callable[[str, int, str], Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class ImageSearchItem:
    """单条联网搜图结果结构。"""

    title: str
    image_url: str
    thumbnail_url: str = ""
    source_url: str = ""
    source_name: str = ""
    width: int | None = None
    height: int | None = None
    description: str = ""

    @property
    def preferred_url(self) -> str:
        """优先返回可直接加载的高清图地址。"""
        return self.image_url or self.thumbnail_url


def _clean_text(raw: Any, limit: int = 180) -> str:
    if not isinstance(raw, str):
        return ""
    stripped = _HTML_TAG_RE.sub(" ", unescape(raw))
    normalized = _WHITESPACE_RE.sub(" ", stripped).strip()
    if len(normalized) > limit:
        return normalized[: limit - 1].rstrip() + "…"
    return normalized


def _safe_public_url(raw: Any) -> str:
    if not isinstance(raw, str) or not raw.strip():
        return ""
    try:
        return validate_public_http_url(raw.strip())
    except ValueError:
        return ""


def _safe_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value > 0:
        return value
    if isinstance(value, str) and value.strip().isdigit():
        parsed = int(value.strip())
        return parsed if parsed > 0 else None
    return None


def _host_label(url: str) -> str:
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""
    return host.removeprefix("www.")


def _passes_size_filter(width: int | None, height: int | None) -> bool:
    if width is not None and width < MIN_IMAGE_DIMENSION:
        return False
    return not (height is not None and height < MIN_IMAGE_DIMENSION)


def parse_serper_image_results(
    payload: dict[str, Any],
    max_results: int = DEFAULT_MAX_IMAGE_RESULTS,
) -> list[ImageSearchItem]:
    """解析 Serper Google Images JSON 响应。"""
    raw_images = payload.get("images")
    if not isinstance(raw_images, list):
        return []

    items: list[ImageSearchItem] = []
    seen: set[str] = set()
    for entry in raw_images:
        if not isinstance(entry, dict):
            continue
        image_url = _safe_public_url(entry.get("imageUrl"))
        thumbnail_url = _safe_public_url(entry.get("thumbnailUrl"))
        url = image_url or thumbnail_url
        if not url or url in seen:
            continue
        width = _safe_int(entry.get("imageWidth"))
        height = _safe_int(entry.get("imageHeight"))
        if not _passes_size_filter(width, height):
            continue
        seen.add(url)
        source_url = _safe_public_url(entry.get("link"))
        source_name = (
            _clean_text(entry.get("source"), 60)
            or _clean_text(entry.get("domain"), 60)
            or _host_label(source_url or url)
        )
        title = _clean_text(entry.get("title"), 120) or source_name or "参考配图"
        items.append(
            ImageSearchItem(
                title=title,
                image_url=url,
                thumbnail_url=thumbnail_url,
                source_url=source_url,
                source_name=source_name,
                width=width,
                height=height,
            )
        )
        if len(items) >= max_results:
            break
    return items


def parse_tavily_image_results(
    payload: dict[str, Any],
    max_results: int = DEFAULT_MAX_IMAGE_RESULTS,
) -> list[ImageSearchItem]:
    """解析 Tavily Search API（include_images=True）响应中的图片列表。"""
    raw_images = payload.get("images")
    if not isinstance(raw_images, list):
        return []

    items: list[ImageSearchItem] = []
    seen: set[str] = set()
    for entry in raw_images:
        if isinstance(entry, str):
            url = _safe_public_url(entry)
            description = ""
        elif isinstance(entry, dict):
            url = _safe_public_url(entry.get("url"))
            description = _clean_text(entry.get("description"), 180)
        else:
            continue
        if not url or url in seen:
            continue
        seen.add(url)
        host = _host_label(url)
        title = description[:80] if description else (host or "参考配图")
        items.append(
            ImageSearchItem(
                title=title,
                image_url=url,
                source_url=url,
                source_name=host or "Web",
                description=description,
            )
        )
        if len(items) >= max_results:
            break
    return items


def parse_wikimedia_commons_results(
    payload: dict[str, Any],
    max_results: int = DEFAULT_MAX_IMAGE_RESULTS,
) -> list[ImageSearchItem]:
    """解析 Wikimedia Commons API 响应，优先采用高清光栅缩略图（避免原始超大文件或脚本化 SVG）。"""
    query_block = payload.get("query")
    if not isinstance(query_block, dict):
        return []
    pages = query_block.get("pages")
    if not isinstance(pages, dict):
        return []

    sorted_pages = sorted(
        (page for page in pages.values() if isinstance(page, dict)),
        key=lambda item: int(item.get("index", 9999))
        if isinstance(item.get("index"), int)
        else 9999,
    )

    items: list[ImageSearchItem] = []
    seen: set[str] = set()
    for page in sorted_pages:
        infos = page.get("imageinfo")
        if not isinstance(infos, list) or not infos or not isinstance(infos[0], dict):
            continue
        info = infos[0]
        mime = str(info.get("mime") or "").lower().strip()
        if mime not in _ALLOWED_COMMONS_MIMES:
            continue

        thumb_url = _safe_public_url(info.get("thumburl"))
        orig_url = _safe_public_url(info.get("url"))
        # SVG 必须优先取光栅缩略图；超大原图也优先取 1280px 缩略图，加载更快更稳
        chosen_url = thumb_url or ("" if mime == "image/svg+xml" else orig_url)
        if not chosen_url or chosen_url in seen:
            continue

        width = _safe_int(info.get("thumbwidth")) or _safe_int(info.get("width"))
        height = _safe_int(info.get("thumbheight")) or _safe_int(info.get("height"))
        if not _passes_size_filter(width, height):
            continue
        seen.add(chosen_url)

        raw_title = str(page.get("title") or "").removeprefix("File:")
        clean_title = _clean_text(re.sub(r"\.[a-zA-Z0-9]{2,5}$", "", raw_title).replace("_", " "), 120)

        extmeta = info.get("extmetadata")
        description = ""
        if isinstance(extmeta, dict):
            desc_entry = extmeta.get("ImageDescription") or extmeta.get("ObjectName")
            if isinstance(desc_entry, dict):
                description = _clean_text(desc_entry.get("value"), 180)

        source_url = _safe_public_url(info.get("descriptionurl")) or orig_url
        items.append(
            ImageSearchItem(
                title=clean_title or description or "Wikimedia Commons 图片",
                image_url=chosen_url,
                thumbnail_url=thumb_url,
                source_url=source_url,
                source_name="Wikimedia Commons",
                width=width,
                height=height,
                description=description,
            )
        )
        if len(items) >= max_results:
            break
    return items


def parse_wikipedia_pageimages_results(
    payload: dict[str, Any],
    *,
    max_results: int = DEFAULT_MAX_IMAGE_RESULTS,
    lang: str = "zh",
) -> list[ImageSearchItem]:
    """解析 Wikipedia 搜索结果中的条目主图（PageImages），弥补中文查询在 Commons 的覆盖短板。"""
    query_block = payload.get("query")
    if not isinstance(query_block, dict):
        return []
    pages = query_block.get("pages")
    if not isinstance(pages, dict):
        return []

    sorted_pages = sorted(
        (page for page in pages.values() if isinstance(page, dict)),
        key=lambda item: int(item.get("index", 9999))
        if isinstance(item.get("index"), int)
        else 9999,
    )

    items: list[ImageSearchItem] = []
    seen: set[str] = set()
    for page in sorted_pages:
        thumb = page.get("thumbnail")
        original = page.get("original")
        thumb_url = _safe_public_url(thumb.get("source")) if isinstance(thumb, dict) else ""
        orig_url = _safe_public_url(original.get("source")) if isinstance(original, dict) else ""
        chosen_url = thumb_url or orig_url
        if not chosen_url or chosen_url in seen:
            continue
        if chosen_url.lower().endswith(".svg") and thumb_url:
            chosen_url = thumb_url

        width = _safe_int(thumb.get("width")) if isinstance(thumb, dict) else None
        height = _safe_int(thumb.get("height")) if isinstance(thumb, dict) else None
        if not _passes_size_filter(width, height):
            continue
        seen.add(chosen_url)

        title = _clean_text(page.get("title"), 120) or "Wikipedia 图片"
        source_url = _safe_public_url(page.get("fullurl"))
        items.append(
            ImageSearchItem(
                title=title,
                image_url=chosen_url,
                thumbnail_url=thumb_url,
                source_url=source_url,
                source_name="维基百科" if lang == "zh" else "Wikipedia",
                width=width,
                height=height,
            )
        )
        if len(items) >= max_results:
            break
    return items


async def _default_serper_fetcher(
    query: str,
    max_results: int,
    api_key: str,
) -> dict[str, Any]:
    headers = {
        "X-API-KEY": api_key,
        "Content-Type": "application/json",
    }
    body: dict[str, Any] = {
        "q": query,
        "num": max(1, min(max_results * 2, 20)),
    }
    if _CJK_RE.search(query):
        body["hl"] = "zh-cn"
    async with httpx.AsyncClient(timeout=DEFAULT_IMAGE_SEARCH_TIMEOUT) as client:
        response = await client.post(SERPER_IMAGES_ENDPOINT, headers=headers, json=body)
        response.raise_for_status()
        data = response.json()
        return data if isinstance(data, dict) else {}


async def _default_tavily_fetcher(
    query: str,
    max_results: int,
    api_key: str,
) -> dict[str, Any]:
    body = {
        "api_key": api_key,
        "query": query,
        "search_depth": "basic",
        "max_results": max(3, min(max_results * 2, 10)),
        "include_images": True,
        "include_image_descriptions": True,
    }
    async with httpx.AsyncClient(timeout=DEFAULT_IMAGE_SEARCH_TIMEOUT) as client:
        response = await client.post(TAVILY_SEARCH_ENDPOINT, json=body)
        response.raise_for_status()
        data = response.json()
        return data if isinstance(data, dict) else {}


async def _default_wikimedia_fetcher(query: str, max_results: int) -> dict[str, Any]:
    params: dict[str, str | int] = {
        "action": "query",
        "format": "json",
        "generator": "search",
        "gsrnamespace": 6,
        "gsrsearch": query,
        "gsrlimit": max(4, min(max_results * 3, 20)),
        "prop": "imageinfo",
        "iiprop": "url|size|mime|extmetadata",
        "iiurlwidth": 1280,
    }
    async with httpx.AsyncClient(
        timeout=DEFAULT_IMAGE_SEARCH_TIMEOUT,
        headers=_WIKIMEDIA_HEADERS,
        follow_redirects=True,
    ) as client:
        response = await client.get(WIKIMEDIA_COMMONS_API, params=params)
        response.raise_for_status()
        data = response.json()
        return data if isinstance(data, dict) else {}


async def _default_wikipedia_fetcher(
    query: str,
    max_results: int,
    lang: str,
) -> dict[str, Any]:
    endpoint = WIKIPEDIA_ZH_API if lang == "zh" else WIKIPEDIA_EN_API
    params: dict[str, str | int] = {
        "action": "query",
        "format": "json",
        "generator": "search",
        "gsrsearch": query,
        "gsrlimit": max(4, min(max_results * 2, 12)),
        "prop": "pageimages|info",
        "piprop": "thumbnail|original",
        "pithumbsize": 1200,
        "inprop": "url",
    }
    async with httpx.AsyncClient(
        timeout=DEFAULT_IMAGE_SEARCH_TIMEOUT,
        headers=_WIKIMEDIA_HEADERS,
        follow_redirects=True,
    ) as client:
        response = await client.get(endpoint, params=params)
        response.raise_for_status()
        data = response.json()
        return data if isinstance(data, dict) else {}


def format_image_search_results(
    query: str,
    items: list[ImageSearchItem],
    *,
    engine_label: str,
    fallback_notes: list[str] | None = None,
) -> str:
    """将搜图结果格式化为清晰的高信息密度 Markdown，便于模型挑图并准确引用 URL。"""
    if not items:
        hint = (
            f"未找到与 `{query}` 匹配的高质量真实图片。请尝试更换更简洁的英文或中英双语关键词重新调用 `search_images`，"
            "或改用文字、表格与图表呈现，切勿凭空编造图片 URL。"
        )
        if fallback_notes:
            hint += "\n\n（检索备注：" + "；".join(fallback_notes) + "）"
        return hint

    lines = [
        f"### 🖼️ 图片检索结果：`{query}`（返回 {len(items)} 张 · 引擎：{engine_label}）",
        "",
    ]
    for idx, item in enumerate(items, start=1):
        meta_parts: list[str] = []
        if item.source_name:
            meta_parts.append(item.source_name)
        if item.width and item.height:
            meta_parts.append(f"{item.width}×{item.height}")
        meta_suffix = f"（{' · '.join(meta_parts)}）" if meta_parts else ""
        lines.append(f"{idx}. **{item.title}**{meta_suffix}")
        lines.append(f"   - 图片URL: `{item.preferred_url}`")
        if item.source_url and item.source_url != item.preferred_url:
            lines.append(f"   - 来源页: `{item.source_url}`")
        if item.description and item.description != item.title:
            lines.append(f"   - 说明: {item.description}")
    if fallback_notes:
        lines.append("")
        lines.append(f"> 备注：{'；'.join(fallback_notes)}")
    lines.append("")
    lines.append(
        "> 提示：在最终回答中展示真实配图时，请使用 ```` ```gallery ```` 代码块，"
        "并且**只能逐字引用上方返回的 `图片URL`**，严禁修改或编造链接。"
    )
    return "\n".join(lines)


class SearchImagesTool(Tool):
    """只读真实配图检索工具：优先 Serper / Tavily，无 Key 时自动回退 Wikimedia Commons 与 Wikipedia。"""

    name = "search_images"
    description = (
        "在互联网上检索真实、可验证的高清图片（如地理地图、管网示意图、真实新闻/实物照片、历史资料与科普配图），"
        "返回图片标题、尺寸、来源页与可直接嵌入回答 `gallery` 块的真实图片 URL。"
        "适用于需要图文并茂呈现事实、地理、工程、科普或行业分析的场景。"
        "提示：使用精炼的英文或中英双语主题词（如 'Germany oil pipelines refineries map'）通常能显著提高权威地图与高清实拍命中率。"
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "OBJECT",
        "properties": {
            "query": {
                "type": "STRING",
                "description": "图片检索关键词（1~200 字符；地图、示意图或国际题材优先使用简洁英文关键词）",
            },
            "max_results": {
                "type": "INTEGER",
                "description": "期望返回的图片数量（默认 4，范围 1~8）",
            },
        },
        "required": ["query"],
    }
    read_only = True

    def __init__(
        self,
        *,
        serper_api_key: str | None = None,
        tavily_api_key: str | None = None,
        serper_fetcher: SerperFetcher | None = None,
        tavily_fetcher: TavilyFetcher | None = None,
        wikimedia_fetcher: WikimediaFetcher | None = None,
        wikipedia_fetcher: WikipediaFetcher | None = None,
    ) -> None:
        self._serper_api_key = serper_api_key
        self._tavily_api_key = tavily_api_key
        self._serper_fetcher = serper_fetcher or _default_serper_fetcher
        self._tavily_fetcher = tavily_fetcher or _default_tavily_fetcher
        self._wikimedia_fetcher = wikimedia_fetcher or _default_wikimedia_fetcher
        self._wikipedia_fetcher = wikipedia_fetcher or _default_wikipedia_fetcher
        self.verified_urls: set[str] = set()

    def _resolve_serper_key(self) -> str:
        return (self._serper_api_key or os.getenv("SERPER_API_KEY", "")).strip()

    def _resolve_tavily_key(self) -> str:
        return (self._tavily_api_key or os.getenv("TAVILY_API_KEY", "")).strip()

    def _record_verified(self, items: list[ImageSearchItem]) -> None:
        for item in items:
            if item.image_url:
                self.verified_urls.add(item.image_url)
            if item.thumbnail_url:
                self.verified_urls.add(item.thumbnail_url)

    async def run(self, parameter: dict[str, Any]) -> ToolResult:
        raw_query = parameter.get("query", "")
        if not isinstance(raw_query, str) or not raw_query.strip():
            return ToolResult("图片检索失败：关键词 query 不能为空。", True)
        query = raw_query.strip()[:200]

        raw_max = parameter.get("max_results", DEFAULT_MAX_IMAGE_RESULTS)
        if isinstance(raw_max, bool) or not isinstance(raw_max, int):
            return ToolResult("图片检索失败：max_results 必须是 1 到 8 之间的整数。", True)
        max_results = max(1, min(raw_max, MAX_ALLOWED_IMAGE_RESULTS))

        fallback_notes: list[str] = []

        serper_key = self._resolve_serper_key()
        if serper_key:
            try:
                payload = await self._serper_fetcher(query, max_results, serper_key)
                items = parse_serper_image_results(payload, max_results=max_results)
                if items:
                    self._record_verified(items)
                    return ToolResult(
                        format_image_search_results(
                            query, items, engine_label="Serper Google Images"
                        ),
                        False,
                    )
                fallback_notes.append("Serper 未返回可用大图，已尝试回退图源")
            except (httpx.HTTPError, OSError, ValueError) as exc:
                fallback_notes.append(f"Serper 调用失败（{exc.__class__.__name__}），已自动回退")

        tavily_key = self._resolve_tavily_key()
        if tavily_key:
            try:
                payload = await self._tavily_fetcher(query, max_results, tavily_key)
                items = parse_tavily_image_results(payload, max_results=max_results)
                if items:
                    self._record_verified(items)
                    return ToolResult(
                        format_image_search_results(
                            query,
                            items,
                            engine_label="Tavily Images",
                            fallback_notes=fallback_notes or None,
                        ),
                        False,
                    )
                fallback_notes.append("Tavily 未返回可用图片，已回退免费百科图源")
            except (httpx.HTTPError, OSError, ValueError) as exc:
                fallback_notes.append(f"Tavily 调用失败（{exc.__class__.__name__}），已回退免费百科图源")

        merged: list[ImageSearchItem] = []
        seen_urls: set[str] = set()
        engines_used: list[str] = []
        free_errors: list[str] = []

        # 免费回退源 1：Wikimedia Commons（权威地图、示意图、公有领域照片）
        try:
            commons_payload = await self._wikimedia_fetcher(query, max_results)
            commons_items = parse_wikimedia_commons_results(
                commons_payload, max_results=max_results
            )
            if commons_items:
                engines_used.append("Wikimedia Commons")
                for item in commons_items:
                    if item.preferred_url not in seen_urls:
                        seen_urls.add(item.preferred_url)
                        merged.append(item)
        except (httpx.HTTPError, OSError, ValueError) as exc:
            free_errors.append(f"Wikimedia Commons 不可用（{exc.__class__.__name__}）")

        # 免费回退源 2：Wikipedia PageImages（补齐中文关键词或百科条目主图）
        if len(merged) < max_results:
            lang = "zh" if _CJK_RE.search(query) else "en"
            try:
                wiki_payload = await self._wikipedia_fetcher(
                    query, max_results - len(merged), lang
                )
                wiki_items = parse_wikipedia_pageimages_results(
                    wiki_payload,
                    max_results=max_results - len(merged),
                    lang=lang,
                )
                if wiki_items:
                    engines_used.append("Wikipedia" if lang == "en" else "中文维基百科")
                    for item in wiki_items:
                        if item.preferred_url not in seen_urls:
                            seen_urls.add(item.preferred_url)
                            merged.append(item)
                            if len(merged) >= max_results:
                                break
            except (httpx.HTTPError, OSError, ValueError) as exc:
                free_errors.append(f"Wikipedia 不可用（{exc.__class__.__name__}）")

        if not merged and free_errors:
            return ToolResult(
                content=(
                    f"图片检索失败（`{query}`）：{'；'.join([*fallback_notes, *free_errors])}。"
                    "请稍后重试，或在设置/环境变量中配置 `SERPER_API_KEY` 或 `TAVILY_API_KEY`。"
                ),
                is_error=True,
            )

        self._record_verified(merged)
        engine_label = " + ".join(engines_used) if engines_used else "Wikimedia / Wikipedia"
        return ToolResult(
            content=format_image_search_results(
                query,
                merged,
                engine_label=engine_label,
                fallback_notes=[*fallback_notes, *free_errors] or None,
            ),
            is_error=False,
        )
