import asyncio
import json
import math
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import httpx

from agent_service.kernel.dispatch.base import ToolResult


class DataProviderError(RuntimeError):
    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


async def get_json(
    client: httpx.AsyncClient,
    url: str,
    source: str,
    *,
    params: dict[str, str | int | float] | None = None,
    headers: dict[str, str] | None = None,
    decimal_numbers: bool = False,
) -> Any:
    """向已配置的数据源读取有大小和时间上限的 JSON。"""
    try:
        async with (
            asyncio.timeout(12),
            client.stream(
                "GET", url, params=params, headers=headers, follow_redirects=False
            ) as response,
        ):
            if not response.is_success:
                status = response.status_code
                if status in (401, 403):
                    detail = "凭据无效或接口未授权"
                elif status == 429:
                    detail = "请求频率或配额超限"
                else:
                    detail = f"HTTP {status}"
                raise DataProviderError(f"{source}：{detail}。", status=status)
            data = bytearray()
            async for chunk in response.aiter_bytes():
                if len(data) + len(chunk) > 2 * 1024 * 1024:
                    raise DataProviderError(f"{source}：响应超出大小限制。")
                data.extend(chunk)
            return json.loads(data, parse_float=Decimal if decimal_numbers else float)
    except (TimeoutError, httpx.TimeoutException) as exc:
        raise DataProviderError(f"{source}：请求超时。") from exc
    except (httpx.HTTPError, httpx.InvalidURL) as exc:
        # 供应商异常可能包含带 Key 的请求地址，仅返回固定描述。
        raise DataProviderError(f"{source}：网络请求失败。") from exc
    except (ValueError, RecursionError) as exc:
        raise DataProviderError(f"{source}：响应无法解析。") from exc


def as_object(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def as_records(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def number(value: Any) -> float | None:
    if type(value) not in (int, float, str):
        return None
    try:
        parsed = float(value)
    except OverflowError, ValueError:
        return None
    return parsed if math.isfinite(parsed) else None


def text(value: Any) -> str:
    return value.strip()[:240] if isinstance(value, str) else ""


def coordinates(longitude: Any, latitude: Any) -> tuple[float, float]:
    lon, lat = number(longitude), number(latitude)
    if lon is None or lat is None or abs(lon) > 180 or abs(lat) > 90:
        raise ValueError("请提供有效的经纬度，经度为 -180～180，纬度为 -90～90。")
    return lon, lat


def country_code(value: Any) -> str:
    if value is None:
        return ""
    if (
        not isinstance(value, str)
        or len(value) != 2
        or not value.isascii()
        or not value.isalpha()
    ):
        raise ValueError("country_code 请传两位国家代码，例如 CN、JP 或 SG。")
    return value.upper()


def integer(value: Any, name: str, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{name} 需要是 {minimum}～{maximum} 的整数。")
    return value


def iso_date(value: Any) -> date:
    if isinstance(value, str):
        try:
            parsed = date.fromisoformat(value)
        except ValueError:
            pass
        else:
            if parsed.isoformat() == value:
                return parsed
    raise ValueError("日期请使用 YYYY-MM-DD 格式。")


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def result(data: dict[str, Any], *, error: bool = False) -> ToolResult:
    return ToolResult(
        content=json.dumps(data, ensure_ascii=False, allow_nan=False), is_error=error
    )
