import asyncio
import ipaddress
import re
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any, ClassVar, Literal
from urllib.parse import urljoin, urlparse

import httpx

from agent_service.config.settings import resolve_api_key, settings
from agent_service.kernel.dispatch.base import Tool, ToolResult

OutputFormat = Literal["markdown", "text", "html"]
VALID_FORMATS: tuple[OutputFormat, ...] = ("markdown", "text", "html")

MAX_RESPONSE_BYTES = 5 * 1024 * 1024  # 5 MB（对齐 OpenCode MAX_RESPONSE_SIZE）
DEFAULT_TIMEOUT_SECONDS = 30.0
MAX_TIMEOUT_SECONDS = 120.0
DEFAULT_MAX_CHARS = 8000
MAX_ALLOWED_CHARS = 25000
_MAX_REDIRECTS = 5
DEEPSEEK_FETCH_ENDPOINT = "https://api.deepseek.com/v1/tools/fetch"

_BLOCKED_HOSTNAMES = frozenset(
    {
        "localhost",
        "localhost.localdomain",
        "ip6-localhost",
        "ip6-loopback",
        "0.0.0.0",
        "metadata.google.internal",
    }
)

_SKIP_TAGS = frozenset(
    {
        "script",
        "style",
        "noscript",
        "iframe",
        "object",
        "embed",
        "svg",
        "canvas",
        "template",
        "meta",
        "link",
        "nav",
        "footer",
    }
)

_BLOCK_TAGS = frozenset(
    {
        "p",
        "div",
        "section",
        "article",
        "main",
        "header",
        "aside",
        "blockquote",
        "table",
        "tr",
        "ul",
        "ol",
        "dl",
        "dt",
        "dd",
        "figure",
        "figcaption",
    }
)

_HEADING_LEVELS: dict[str, int] = {
    "h1": 1,
    "h2": 2,
    "h3": 3,
    "h4": 4,
    "h5": 5,
    "h6": 6,
}

_INLINE_WS_RE = re.compile(r"[ \t\r\f\v]+")
_MULTI_NEWLINES_RE = re.compile(r"\n{3,}")
_MARKDOWN_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\((https?://[^)\s]+)(?:\s+\"[^\"]*\")?\)")
_LOW_VALUE_IMAGE_URL_RE = re.compile(
    r"(?:favicon|sprite|spacer|pixel|tracking|beacon|blank\.gif|1x1|avatar|emoji|badge|\.ico(?:$|\?))",
    re.IGNORECASE,
)
_MIN_WEB_IMAGE_DIMENSION = 160
_MAX_EXTRACTED_PAGE_IMAGES = 5


@dataclass(frozen=True)
class ExtractedWebImage:
    """从网页正文提取到的可引用公网配图。"""

    url: str
    alt: str = ""
    width: int | None = None
    height: int | None = None


@dataclass(frozen=True)
class FetchedPageResponse:
    """HTTP 页面抓取结果快照。"""

    url: str
    status_code: int
    content_type: str
    text: str


PageFetcher = Callable[[str, OutputFormat, float], Awaitable[FetchedPageResponse]]
DeepSeekPageFetcher = Callable[[str, float, str], Awaitable[dict[str, Any]]]


async def _default_deepseek_page_fetcher(
    url: str,
    timeout_seconds: float,
    api_key: str,
) -> dict[str, Any]:
    """调用 DeepSeek 官方 Fetch API（POST /v1/tools/fetch）返回结构化 Markdown。"""

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        response = await client.post(
            DEEPSEEK_FETCH_ENDPOINT,
            headers=headers,
            json={"url": url},
        )
        response.raise_for_status()
        data = response.json()
        return data if isinstance(data, dict) else {}


def _resolve_deepseek_key(explicit_key: str | None) -> str:
    provider = settings.providers.get("deepseek")
    if provider is not None:
        return resolve_api_key(provider, explicit_key)
    return (explicit_key or "").strip()


def validate_public_http_url(raw_url: str) -> str:
    """
    检查传入的 URL 是否符合这个工具的访问规则，符合就返回处理后的 URL，不符合就抛出错误。
    """

    if not isinstance(raw_url, str) or not raw_url.strip():
        raise ValueError("URL 不能为空。")
    url = raw_url.strip()
    if not url.startswith(("http://", "https://")):
        raise ValueError("URL 必须以 http:// 或 https:// 开头。")

    parsed = urlparse(url)
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("公开网页地址不能包含用户凭据。")
    hostname = (parsed.hostname or "").strip().lower()
    if not hostname:
        raise ValueError("URL 缺少有效的主机名（hostname）。")

    if hostname in _BLOCKED_HOSTNAMES or hostname.endswith(".local"):
        raise ValueError(f"安全策略禁止访问本地或内网主机：{hostname}")

    try:
        
        ip = ipaddress.ip_address(hostname) # 输入一个字符串地址，返回一个ip对象，无法接收域名。

    except ValueError:
        ip = None

    if ip is not None and (
        ip.is_loopback # 回环地址
        or ip.is_private # 私有地址
        or ip.is_link_local # 链路本地地址
        or ip.is_multicast # 组播地址
        or ip.is_reserved # 特殊地址
        or ip.is_unspecified # 未指定地址
    ):
        raise ValueError(f"安全策略禁止访问本地回环或私有网段地址：{hostname}")

    return url


def build_accept_header(output_format: OutputFormat) -> str:
    """参考 OpenCode webfetch 构建带权重的 Accept 请求头。"""

    if output_format == "markdown":
        return (
            "text/markdown;q=1.0, text/x-markdown;q=0.9, "
            "text/plain;q=0.8, text/html;q=0.7, */*;q=0.1"
        )
    if output_format == "text":
        return "text/plain;q=1.0, text/markdown;q=0.9, text/html;q=0.8, */*;q=0.1"
    return (
        "text/html;q=1.0, application/xhtml+xml;q=0.9, "
        "text/plain;q=0.8, text/markdown;q=0.7, */*;q=0.1"
    )


class _HTMLContentParser(HTMLParser):
    _IGNORED_TAGS = _SKIP_TAGS
    _IGNORED_VOID_TAGS = frozenset({"embed", "link", "meta"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._ignored_containers: list[str] = []

    def _ignore_start(self, tag: str) -> bool:
        if tag in self._IGNORED_TAGS:
            if tag not in self._IGNORED_VOID_TAGS:
                self._ignored_containers.append(tag)
            return True
        return bool(self._ignored_containers)

    def _ignore_end(self, tag: str) -> bool:
        if not self._ignored_containers:
            return False
        if tag == self._ignored_containers[-1]:
            self._ignored_containers.pop()
        return True


class _HTMLToTextParser(_HTMLContentParser):
    """对齐 OpenCode extractTextFromHTML，剥离脚本与样式后提取纯文本。"""

    def __init__(self) -> None:
        super().__init__()
        self._chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if self._ignore_start(tag):
            return
        if tag in _BLOCK_TAGS or tag in _HEADING_LEVELS or tag in {"br", "li", "hr"}:
            self._chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if self._ignore_end(tag):
            return
        if tag in _BLOCK_TAGS or tag in _HEADING_LEVELS or tag == "li":
            self._chunks.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._ignored_containers:
            self._chunks.append(data)


def extract_text_from_html(html_text: str) -> str:
    """从 HTML 中提取纯文本内容。"""

    parser = _HTMLToTextParser()
    parser.feed(html_text)
    parser.close()
    raw = "".join(parser._chunks)
    lines = [_INLINE_WS_RE.sub(" ", line).strip() for line in raw.splitlines()]
    cleaned = "\n".join(line for line in lines if line)
    return _MULTI_NEWLINES_RE.sub("\n\n", cleaned).strip()


class _HTMLToMarkdownParser(_HTMLContentParser):
    """轻量级 HTML -> Markdown 转换器，保留标题、代码块、列表与链接结构。"""

    def __init__(self, base_url: str = "") -> None:
        super().__init__()
        self.base_url = base_url
        self.title: str = ""

        self._in_title = False
        self._title_chunks: list[str] = []

        self._in_pre = False
        self._in_code = False

        self._link_href: str | None = None
        self._link_chunks: list[str] = []

        self._out: list[str] = []

    def _append(self, text: str) -> None:
        if self._link_href is not None and not self._in_pre:
            self._link_chunks.append(text)
        else:
            self._out.append(text)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._ignore_start(tag):
            return

        if tag == "title":
            self._in_title = True
            self._title_chunks = []
            return

        if tag in _HEADING_LEVELS:
            level = _HEADING_LEVELS[tag]
            self._out.append(f"\n\n{'#' * level} ")
            return

        if tag == "pre":
            self._in_pre = True
            self._out.append("\n\n```\n")
            return

        if tag == "code":
            self._in_code = True
            if not self._in_pre:
                self._append("`")
            return

        if tag == "a" and not self._in_pre:
            href = ""
            for k, v in attrs:
                if k == "href" and v:
                    href = v.strip()
                    break
            if href and not href.startswith(("javascript:", "mailto:", "#")):
                if self.base_url:
                    href = urljoin(self.base_url, href)
                self._link_href = href
                self._link_chunks = []
            return

        if tag == "li":
            self._out.append("\n- ")
            return

        if tag == "br":
            self._append("\n")
            return

        if tag == "hr":
            self._out.append("\n\n---\n\n")
            return

        if tag in _BLOCK_TAGS:
            self._out.append("\n\n")

    def handle_endtag(self, tag: str) -> None:
        if self._ignore_end(tag):
            return

        if tag == "title":
            self._in_title = False
            if not self.title:
                self.title = _INLINE_WS_RE.sub(
                    " ", "".join(self._title_chunks)
                ).strip()
            return

        if tag in _HEADING_LEVELS:
            self._out.append("\n\n")
            return

        if tag == "pre":
            self._in_pre = False
            self._out.append("\n```\n\n")
            return

        if tag == "code":
            self._in_code = False
            if not self._in_pre:
                self._append("`")
            return

        if tag == "a" and self._link_href is not None:
            link_text = _INLINE_WS_RE.sub(
                " ", "".join(self._link_chunks)
            ).strip()
            href = self._link_href
            self._link_href = None
            self._link_chunks = []
            if link_text:
                self._out.append(f"[{link_text}]({href})")
            return

        if tag in _BLOCK_TAGS:
            self._out.append("\n\n")

    def handle_data(self, data: str) -> None:
        if self._ignored_containers:
            return
        if self._in_title:
            self._title_chunks.append(data)
            return
        if self._in_pre:
            self._out.append(data)
            return
        normalized = _INLINE_WS_RE.sub(" ", data)
        if normalized:
            self._append(normalized)


def convert_html_to_markdown(html_text: str, base_url: str = "") -> tuple[str, str]:
    """将 HTML 转换为结构化 Markdown，返回 (page_title, markdown_body)。"""

    parser = _HTMLToMarkdownParser(base_url=base_url)
    parser.feed(html_text)
    parser.close()

    raw_md = "".join(parser._out)
    # 逐行整理非代码块中的多余首尾空白
    cleaned_lines: list[str] = []
    in_fence = False
    for line in raw_md.splitlines():
        if line.strip().startswith("```"):
            in_fence = not in_fence
            cleaned_lines.append(line.strip())
        elif in_fence:
            cleaned_lines.append(line.rstrip())
        else:
            cleaned_lines.append(line.strip())

    markdown = "\n".join(cleaned_lines)
    markdown = _MULTI_NEWLINES_RE.sub("\n\n", markdown).strip()
    return parser.title, markdown


def _parse_int_attr(raw: str | None) -> int | None:
    if not raw:
        return None
    digits = re.match(r"^\s*(\d+)", raw)
    if not digits:
        return None
    val = int(digits.group(1))
    return val if val > 0 else None


def _pick_srcset_candidate(srcset: str) -> str:
    best_url = ""
    best_weight = -1.0
    for chunk in srcset.split(","):
        parts = chunk.strip().split()
        if not parts:
            continue
        candidate = parts[0].strip()
        weight = 1.0
        if len(parts) > 1:
            desc = parts[1].strip().lower()
            if desc.endswith("w") and desc[:-1].isdigit():
                weight = float(desc[:-1])
            elif desc.endswith("x"):
                try:
                    weight = float(desc[:-1]) * 500.0
                except ValueError:
                    weight = 1.0
        if candidate and weight >= best_weight:
            best_weight = weight
            best_url = candidate
    return best_url


class _HTMLImageExtractor(_HTMLContentParser):
    """提取网页正文区域中具有信息价值的 <img>，自动跳过导航/页脚与小图标。"""

    _IGNORED_TAGS = _SKIP_TAGS | {"header"}

    def __init__(self, base_url: str = "", max_images: int = _MAX_EXTRACTED_PAGE_IMAGES) -> None:
        super().__init__()
        self._base_url = base_url
        self._max_images = max_images
        self._seen: set[str] = set()
        self.images: list[ExtractedWebImage] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._ignore_start(tag):
            return
        if tag != "img" or len(self.images) >= self._max_images:
            return
        attr_map = {k.lower(): (v or "").strip() for k, v in attrs}
        raw_src = (
            attr_map.get("data-original")
            or attr_map.get("data-src")
            or attr_map.get("data-lazy-src")
            or _pick_srcset_candidate(attr_map.get("srcset", ""))
            or attr_map.get("src", "")
        )
        if not raw_src or raw_src.startswith(("data:", "blob:", "javascript:")):
            return
        resolved = urljoin(self._base_url, raw_src) if self._base_url else raw_src
        try:
            safe_url = validate_public_http_url(resolved)
        except ValueError:
            return
        if safe_url in self._seen or _LOW_VALUE_IMAGE_URL_RE.search(safe_url):
            return

        width = _parse_int_attr(attr_map.get("width"))
        height = _parse_int_attr(attr_map.get("height"))
        if (width is not None and width < _MIN_WEB_IMAGE_DIMENSION) or (
            height is not None and height < _MIN_WEB_IMAGE_DIMENSION
        ):
            return

        self._seen.add(safe_url)
        alt = _INLINE_WS_RE.sub(" ", attr_map.get("alt") or attr_map.get("title") or "").strip()[:120]
        self.images.append(
            ExtractedWebImage(url=safe_url, alt=alt, width=width, height=height)
        )

    def handle_endtag(self, tag: str) -> None:
        self._ignore_end(tag)


def extract_images_from_html(
    html_text: str,
    base_url: str = "",
    max_images: int = _MAX_EXTRACTED_PAGE_IMAGES,
) -> list[ExtractedWebImage]:
    """从 HTML 正文中提取去重后的公网配图列表。"""
    parser = _HTMLImageExtractor(base_url=base_url, max_images=max_images)
    parser.feed(html_text)
    parser.close()
    return parser.images


def extract_images_from_markdown(
    markdown_text: str,
    max_images: int = _MAX_EXTRACTED_PAGE_IMAGES,
) -> list[ExtractedWebImage]:
    """从 Markdown 正文中提取已有的公网图片链接。"""
    images: list[ExtractedWebImage] = []
    seen: set[str] = set()
    for match in _MARKDOWN_IMAGE_RE.finditer(markdown_text):
        raw_alt, raw_url = match.group(1), match.group(2)
        try:
            safe_url = validate_public_http_url(raw_url)
        except ValueError:
            continue
        if safe_url in seen or _LOW_VALUE_IMAGE_URL_RE.search(safe_url):
            continue
        seen.add(safe_url)
        alt = _INLINE_WS_RE.sub(" ", raw_alt).strip()[:120]
        images.append(ExtractedWebImage(url=safe_url, alt=alt))
        if len(images) >= max_images:
            break
    return images


def _format_extracted_images_section(images: list[ExtractedWebImage]) -> str:
    if not images:
        return ""
    lines = ["", "#### 页面可引用配图（已验证 URL）"]
    for img in images:
        label = img.alt or "网页配图"
        size_hint = f" ({img.width}×{img.height})" if img.width and img.height else ""
        lines.append(f"- {label}{size_hint}: `{img.url}`")
    return "\n".join(lines)


async def _public_ip_for(url: httpx.URL) -> str:
    port = url.port or (443 if url.scheme == "https" else 80)
    if port not in (80, 443):
        raise ValueError("公开网页抓取仅支持端口 80 和 443。")
    host = url.raw_host.decode("ascii")
    answers = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    addresses = [ipaddress.ip_address(answer[4][0]) for answer in answers]
    if not addresses or any(
        not address.is_global or address.is_multicast or address.is_reserved
        for address in addresses
    ):
        raise ValueError("目标主机必须只解析到可访问的公网单播地址。")
    # URL 用已验证的地址建立连接，防止连接时重新解析域名。
    return str(next((address for address in addresses if address.version == 4), addresses[0]))


async def _default_page_fetcher(
    url: str,
    output_format: OutputFormat,
    timeout_seconds: float,
) -> FetchedPageResponse:
    headers = {
        "User-Agent": "agent-service/2.0",
        "Accept": build_accept_header(output_format),
        "Accept-Language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "identity",
    }
    async with asyncio.timeout(timeout_seconds):
        for _ in range(_MAX_REDIRECTS + 1):
            try:
                origin = httpx.URL(validate_public_http_url(url))
            except httpx.InvalidURL as exc:
                raise ValueError("网页地址格式无效。") from exc
            address = await _public_ip_for(origin)
            # 每跳独立连接。Host 与 TLS 证书校验仍使用原始域名。
            async with (
                httpx.AsyncClient(
                    timeout=timeout_seconds, trust_env=False, follow_redirects=False
                ) as client,
                client.stream(
                    "GET",
                    origin.copy_with(host=address),
                    headers={**headers, "Host": origin.netloc.decode("ascii")},
                    extensions={"sni_hostname": origin.raw_host.decode("ascii")},
                ) as response,
            ):
                if response.has_redirect_location:
                    url = urljoin(str(origin), response.headers["Location"])
                    continue
                response.raise_for_status()
                if response.headers.get("content-encoding", "identity").lower() != "identity":
                    raise ValueError("网页服务器返回了不支持的压缩响应。")
                content_length = response.headers.get("content-length", "")
                if content_length.isascii() and content_length.isdecimal() and (
                    len(content_length) > 7 or int(content_length) > MAX_RESPONSE_BYTES
                ):
                    raise ValueError("网页响应体积过大（超过 5 MB 上限）。")
                raw_bytes = bytearray()
                async for chunk in response.aiter_raw(chunk_size=65536):
                    if len(raw_bytes) + len(chunk) > MAX_RESPONSE_BYTES:
                        raise ValueError("网页响应体积过大（超过 5 MB 上限）。")
                    raw_bytes.extend(chunk)
                return FetchedPageResponse(
                    url=str(origin),
                    status_code=response.status_code,
                    content_type=response.headers.get("content-type", ""),
                    text=raw_bytes.decode(response.encoding or "utf-8", errors="replace"),
                )
    raise ValueError(f"网页重定向超过 {_MAX_REDIRECTS} 次。")


class FetchWebPageTool(Tool):
    """参考 OpenCode webfetch 设计的网页深度阅读工具（支持 markdown / text / html 格式）。"""

    name = "fetch_web_page"
    description = (
        "抓取指定 URL 的网页内容并转换为易于阅读的格式（默认为结构化 Markdown，保留标题、代码块与链接）。"
        "用户给出公开网页链接并要求阅读或总结时，直接使用此工具。"
        "当通过 `web_search` 发现某条结果（如官方文档、GitHub Issue、StackOverflow）可能包含答案时，"
        "主动调用此工具深入阅读完整正文。此工具为只读工具，不会修改任何本地文件。"
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "OBJECT",
        "properties": {
            "url": {
                "type": "STRING",
                "description": "要抓取的完整网页地址（必须以 http:// 或 https:// 开头）",
            },
            "format": {
                "type": "STRING", 
                "enum": ["markdown", "text", "html"],
                "description": "返回内容格式：'markdown'（默认，推荐阅读代码与文档）、'text'（纯文本）、'html'（原始 HTML）",
            },
            "max_chars": {
                "type": "INTEGER",
                "description": f"返回正文的最大字符数（默认 {DEFAULT_MAX_CHARS}，上限 {MAX_ALLOWED_CHARS}）",
            },
            "timeout": {
                "type": "INTEGER",
                "description": "请求超时时间（秒，默认 30，最大 120）",
            },
        },
        "required": ["url"],
    }
    read_only = True

    def __init__(
        self,
        fetcher: PageFetcher | None = None,
        *,
        deepseek_api_key: str | None = None,
        deepseek_fetcher: DeepSeekPageFetcher | None = None,
        allow_deepseek: bool = True,
    ) -> None:
        """自由指定抓取网页的函数；默认在配置 DeepSeek Key 时优先走 DeepSeek Fetch API。"""
        self._custom_fetcher = fetcher
        self._fetcher = fetcher or _default_page_fetcher
        self._deepseek_api_key = deepseek_api_key
        self._deepseek_fetcher = deepseek_fetcher or _default_deepseek_page_fetcher
        self._allow_deepseek = allow_deepseek

    async def run(self, parameter: dict[str, Any]) -> ToolResult:
        raw_url = parameter.get("url", "")
        try:
            url = validate_public_http_url(raw_url)  # 防止SSRF
        except ValueError as exc:
            return ToolResult(content=f"抓取网页被拒绝：{exc}", is_error=True)

        raw_format = parameter.get("format", "markdown")
        fmt_str = (
            raw_format.strip().lower()
            if isinstance(raw_format, str) and raw_format.strip()
            else "markdown"
        )
        if fmt_str not in VALID_FORMATS:
            return ToolResult(
                content=(
                    f"抓取网页失败：不支持的 format='{fmt_str}'，"
                    "仅支持 'markdown'、'text' 或 'html'。"
                ),
                is_error=True,
            )
        output_format: OutputFormat = fmt_str  # type: ignore[assignment]

        raw_max_chars = parameter.get("max_chars", DEFAULT_MAX_CHARS)
        if isinstance(raw_max_chars, bool) or not isinstance(raw_max_chars, int):
            return ToolResult(
                content="抓取网页失败：max_chars 必须是正整数。",
                is_error=True,
            )
        max_chars = max(500, min(raw_max_chars, MAX_ALLOWED_CHARS))

        raw_timeout = parameter.get("timeout", int(DEFAULT_TIMEOUT_SECONDS))
        if isinstance(raw_timeout, bool) or not isinstance(
            raw_timeout, (int, float)
        ):
            return ToolResult(
                content="抓取网页失败：timeout 必须是正数。",
                is_error=True,
            )
        timeout_seconds = float(
            max(1.0, min(float(raw_timeout), MAX_TIMEOUT_SECONDS))
        )

        # 未注入自定义 fetcher 且输出格式为 markdown 时，优先使用 DeepSeek Fetch API
        if self._allow_deepseek and self._custom_fetcher is None and output_format == "markdown":
            ds_key = _resolve_deepseek_key(self._deepseek_api_key)
            if ds_key:
                try:
                    page = await self._deepseek_fetcher(url, timeout_seconds, ds_key)
                    md_content = str(page.get("content") or "").strip()
                    if md_content:
                        page_title = str(page.get("title") or "").strip()
                        total_chars = len(md_content)
                        truncated = False
                        if total_chars > max_chars:
                            md_content = md_content[:max_chars]
                            truncated = True
                        page_images = extract_images_from_markdown(md_content)
                        header_lines = [f"### 🌐 网页内容：`{url}`"]
                        if page_title:
                            header_lines.append(f"- **标题**：{page_title}")
                        header_lines.append(
                            "- **来源**：`DeepSeek Fetch API` | **格式**：`markdown`"
                        )
                        header_lines.append("")
                        header_lines.append(md_content)
                        if truncated:
                            header_lines.append(
                                f"\n\n...[网页内容已按 max_chars={max_chars} 截断，原文共 {total_chars} 字符]"
                            )
                        images_section = _format_extracted_images_section(page_images)
                        if images_section:
                            header_lines.append(images_section)
                        return ToolResult(
                            content="\n".join(header_lines),
                            is_error=False,
                        )
                except (httpx.HTTPError, OSError, ValueError):
                    # DeepSeek Fetch 失败时静默回退到本地直连抓取
                    pass

        try:
            fetched = await self._fetcher(url, output_format, timeout_seconds)
        except ValueError as exc:
            return ToolResult(content=f"抓取网页被拒绝：{exc}", is_error=True)
        except (httpx.TimeoutException, TimeoutError):
            return ToolResult(
                content=f"抓取网页超时（>{timeout_seconds:g}s）：`{url}`",
                is_error=True,
            )
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            return ToolResult(
                content=f"抓取网页失败：HTTP {status_code}（`{url}`）",
                is_error=True,
            )
        except (httpx.HTTPError, OSError) as exc:
            return ToolResult(
                content=f"抓取网页网络请求出错：{exc}",
                is_error=True,
            )

        content_type = fetched.content_type.lower()
        is_html = "text/html" in content_type or "application/xhtml+xml" in content_type
        page_title = ""

        page_images: list[ExtractedWebImage] = []
        # 将从网站中获取到的body转化成对应的格式
        if output_format == "markdown":
            if is_html:
                page_title, body = convert_html_to_markdown(
                    fetched.text,
                    base_url=fetched.url,
                )
                page_images = extract_images_from_html(
                    fetched.text,
                    base_url=fetched.url,
                )
            else:
                body = fetched.text.strip()
                page_images = extract_images_from_markdown(body)
        elif output_format == "text":
            if is_html:
                body = extract_text_from_html(fetched.text)
            else:
                body = fetched.text.strip()
        else:
            body = fetched.text.strip()

        if not body:
            return ToolResult(
                content=f"网页 `{fetched.url}` 内容为空或未能提取到可读文本。",
                is_error=False,
            )

        total_chars = len(body)
        truncated = False # 标记文本是否被截断
        if total_chars > max_chars:
            body = body[:max_chars]
            truncated = True

        header_lines = [f"### 🌐 网页内容：`{fetched.url}`"] # 创建一个字符串列表储存结果
        if page_title:
            header_lines.append(f"- **标题**：{page_title}")
        header_lines.append(
            f"- **类型**：`{fetched.content_type or 'unknown'}` | **格式**：`{output_format}`"
        )
        header_lines.append("")
        header_lines.append(body)
        if truncated:
            header_lines.append(
                f"\n\n...[网页内容已按 max_chars={max_chars} 截断，原文共 {total_chars} 字符]"
            )
        images_section = _format_extracted_images_section(page_images)
        if images_section:
            header_lines.append(images_section)

        return ToolResult(
            content="\n".join(header_lines),
            is_error=False,
        )
