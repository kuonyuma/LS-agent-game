from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, ClassVar

import httpx

from agent_service.adapters.tools.data_provider import (
    DataProviderError,
    as_object,
    as_records,
    get_json,
    integer,
    iso_date,
    result,
    utc_now,
)
from agent_service.kernel.dispatch.base import Tool, ToolResult

_API = "https://api.frankfurter.dev/v2"
_SOURCE = "Frankfurter"
_SOURCE_URL = "https://frankfurter.dev/"


def _currency(value: Any) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 3
        or not value.isascii()
        or not value.isalpha()
    ):
        raise ValueError("货币代码应为三个英文字母，例如 USD、CNY 或 JPY。")
    return value.upper()


def _observation(row: dict[str, Any], base: str, quote: str) -> tuple[str, str] | None:
    if row.get("base") != base or row.get("quote") != quote:
        return None
    try:
        observed_on = iso_date(row.get("date")).isoformat()
        rate = Decimal(str(row.get("rate")))
    except InvalidOperation, ValueError:
        return None
    if not rate.is_finite() or rate <= 0 or abs(rate.adjusted()) > 30:
        return None
    return observed_on, format(rate, "f")


class GetExchangeRateTool(Tool):
    name = "get_exchange_rate"
    description = (
        "查询两种货币的参考汇率，提供真实观测日；可查询指定日期，或最近 2～30 个自然日的观测序列。"
        "汇率表示 1 单位 base 可兑换的 quote 数量。金额计算交给 calculate，时序缺失日保持空缺。"
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "OBJECT",
        "properties": {
            "base": {"type": "STRING", "description": "来源币种，例如 USD"},
            "quote": {"type": "STRING", "description": "目标币种，例如 CNY"},
            "date": {"type": "STRING", "description": "可选历史日期 YYYY-MM-DD"},
            "days": {
                "type": "INTEGER",
                "description": "可选最近 2～30 个自然日，与 date 二选一",
            },
        },
        "required": ["base", "quote"],
    }

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._transport = transport

    async def run(self, parameter: dict[str, Any]) -> ToolResult:
        try:
            base = _currency(parameter.get("base"))
            quote = _currency(parameter.get("quote"))
            if base == quote:
                raise ValueError("请选择两个不同币种。")
            requested = parameter.get("date")
            days = parameter.get("days")
            if requested is not None and days is not None:
                raise ValueError("date 和 days 请选择一项。")
            today = datetime.now(UTC).date()
            requested_date = iso_date(requested) if requested is not None else None
            if requested_date is not None and requested_date > today:
                raise ValueError("参考汇率仅支持今天或过去的观测。")
            async with httpx.AsyncClient(
                timeout=8, transport=self._transport
            ) as client:
                if days is None:
                    return await self._single(
                        client,
                        base,
                        quote,
                        requested_date.isoformat() if requested_date else None,
                    )
                count = integer(days, "days", 2, 30)
                start = today - timedelta(days=count - 1)
                return await self._series(
                    client, base, quote, start.isoformat(), today.isoformat()
                )
        except (ValueError, DataProviderError) as exc:
            message = str(exc)
            if isinstance(exc, DataProviderError) and exc.status in (404, 422):
                message = "Frankfurter 没有提供该币种组合或日期的参考汇率。"
            return result(
                {"error": message, "source": _SOURCE, "source_url": _SOURCE_URL},
                error=True,
            )

    @staticmethod
    async def _single(
        client: httpx.AsyncClient, base: str, quote: str, requested_date: str | None
    ) -> ToolResult:
        raw = await get_json(
            client,
            f"{_API}/rate/{base.lower()}/{quote.lower()}",
            _SOURCE,
            params={"date": requested_date} if requested_date else None,
            decimal_numbers=True,
        )
        observation = _observation(as_object(raw), base, quote)
        if observation is None:
            raise DataProviderError("Frankfurter 未返回可用的汇率观测。")
        observed_on, rate = observation
        if requested_date and observed_on > requested_date:
            raise DataProviderError("Frankfurter 返回的观测晚于所请求的日期。")
        warnings = []
        if requested_date and observed_on != requested_date:
            warnings.append("请求日期没有新观测，返回该日之前最近一次可用参考汇率。")
        return result(
            {
                "source": _SOURCE,
                "source_url": _SOURCE_URL,
                "kind": "reference_rate",
                "base": base,
                "quote": quote,
                "rate": rate,
                "observed_on": observed_on,
                "requested_date": requested_date,
                "fetched_at": utc_now(),
                "warnings": warnings,
            }
        )

    @staticmethod
    async def _series(
        client: httpx.AsyncClient, base: str, quote: str, start: str, end: str
    ) -> ToolResult:
        raw = await get_json(
            client,
            f"{_API}/rates",
            _SOURCE,
            params={"base": base, "quotes": quote, "from": start, "to": end},
            decimal_numbers=True,
        )
        observations: dict[str, str] = {}
        for row in as_records(raw):
            observation = _observation(row, base, quote)
            if observation is not None and start <= observation[0] <= end:
                observations[observation[0]] = observation[1]
        if not observations:
            raise DataProviderError("Frankfurter 在该区间内没有可用观测。")
        return result(
            {
                "source": _SOURCE,
                "source_url": _SOURCE_URL,
                "kind": "reference_rate",
                "base": base,
                "quote": quote,
                "start_date": start,
                "end_date": end,
                "observations": [
                    {"date": observed_on, "rate": rate}
                    for observed_on, rate in sorted(observations.items())
                ],
                "fetched_at": utc_now(),
                "warnings": ["序列仅含实际观测日，缺失日期未填补。"],
            }
        )
