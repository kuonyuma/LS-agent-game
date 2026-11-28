import asyncio
import os
from datetime import datetime
from typing import Any, ClassVar
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

from agent_service.adapters.tools.data_provider import (
    DataProviderError,
    as_object,
    as_records,
    coordinates,
    country_code,
    get_json,
    integer,
    number,
    result,
    text,
    utc_now,
)
from agent_service.kernel.dispatch.base import Tool, ToolResult

_QWEATHER = "和风天气"
_OPEN_METEO = "Open-Meteo"
_QWEATHER_SOURCE = "https://dev.qweather.com/docs/api/weather/"
_OPEN_METEO_SOURCE = "https://open-meteo.com/en/docs"
_AIR_QUALITY_SOURCE = "https://open-meteo.com/en/docs/air-quality-api"
_SECTIONS = {
    "current": "当前天气",
    "daily": "逐日预报",
    "hourly": "逐小时预报",
    "air_quality": "空气质量",
}
_WMO_CONDITIONS = {
    0: "晴",
    1: "大致晴朗",
    2: "部分多云",
    3: "阴",
    45: "雾",
    48: "雾凇",
    51: "小毛毛雨",
    53: "中等毛毛雨",
    55: "强毛毛雨",
    56: "小冻毛毛雨",
    57: "强冻毛毛雨",
    61: "小雨",
    63: "中雨",
    65: "大雨",
    66: "小冻雨",
    67: "强冻雨",
    71: "小雪",
    73: "中雪",
    75: "大雪",
    77: "雪粒",
    80: "小阵雨",
    81: "中等阵雨",
    82: "强阵雨",
    85: "小阵雪",
    86: "强阵雪",
    95: "雷暴",
    96: "雷暴伴小冰雹",
    99: "雷暴伴强冰雹",
}


def _configured_qweather_host() -> str:
    raw = os.getenv("QWEATHER_API_HOST", "").strip()
    if not raw:
        return ""
    try:
        url = httpx.URL(raw if "://" in raw else f"https://{raw}")
    except httpx.InvalidURL:
        return ""
    if (
        url.scheme == "https"
        and url.host.endswith(".qweatherapi.com")
        and url.port in (None, 443)
        and not (url.userinfo or url.query or url.fragment)
        and url.path == "/"
    ):
        return str(url).rstrip("/")
    return ""


def _percent(raw: Any, scale: int = 1) -> float | None:
    parsed = number(raw)
    if parsed is None or not 0 <= parsed * scale <= 100:
        return None
    return round(parsed * scale, 2)


def _quantity(record: dict[str, Any], name: str, unit: str) -> float | None:
    value = as_object(record.get(name))
    return number(value.get("value")) if value.get("unit") == unit else None


def _local_date(stamp: str, timezone: str) -> str | None:
    if not (stamp and timezone):
        return None
    try:
        instant = datetime.fromisoformat(stamp)
        if instant.tzinfo is not None:
            return instant.astimezone(ZoneInfo(timezone)).date().isoformat()
    except ValueError, ZoneInfoNotFoundError:
        pass
    return None


def _qweather_hour(record: dict[str, Any], time_field: str) -> dict[str, Any] | None:
    time = text(record.get(time_field))
    temp = _quantity(record, "temperature", "°C")
    if not time or temp is None:
        return None
    precipitation = as_object(record.get("precipitation"))
    wind_speed = _quantity(as_object(record.get("wind")), "speed", "m/s")
    return {
        "time": time,
        "temperature_c": temp,
        "feels_like_c": _quantity(record, "feelsLike", "°C"),
        "condition": text(as_object(record.get("condition")).get("text")),
        "humidity_percent": _percent(record.get("humidity"), 100),
        "wind_kmh": round(wind_speed * 3.6, 2) if wind_speed is not None else None,
        "precipitation_probability_percent": _percent(
            precipitation.get("probability"), 100
        ),
    }


def _qweather_day(record: dict[str, Any], timezone: str) -> dict[str, Any] | None:
    start = text(record.get("forecastStartTime"))
    temp_max = _quantity(record, "temperatureMax", "°C")
    temp_min = _quantity(record, "temperatureMin", "°C")
    if not start or temp_max is None or temp_min is None:
        return None
    daytime = as_object(record.get("daytime"))
    nighttime = as_object(record.get("nighttime"))
    return {
        "date": _local_date(text(daytime.get("forecastStartTime")) or start, timezone),
        "starts_at": start,
        "ends_at": text(record.get("forecastEndTime")),
        "temperature_max_c": temp_max,
        "temperature_min_c": temp_min,
        "daytime_condition": text(as_object(daytime.get("condition")).get("text")),
        "nighttime_condition": text(as_object(nighttime.get("condition")).get("text")),
        "daytime_precipitation_probability_percent": _percent(
            as_object(daytime.get("precipitation")).get("probability"), 100
        ),
        "nighttime_precipitation_probability_percent": _percent(
            as_object(nighttime.get("precipitation")).get("probability"), 100
        ),
    }


def _qweather_section(
    section: str, raw: dict[str, Any], timezone: str
) -> dict[str, Any] | None:
    if section == "current":
        data = _qweather_hour(raw, "validTime")
    elif section in ("daily", "hourly"):
        if section == "daily":
            rows = [_qweather_day(row, timezone) for row in as_records(raw.get("days"))]
        else:
            rows = [
                _qweather_hour(row, "forecastTime")
                for row in as_records(raw.get("hours"))
            ]
        data = (
            {"items": [item for item in rows if item is not None]}
            if any(rows)
            else None
        )
    else:
        indexes = [
            {
                "standard": text(row.get("code")),
                "name": text(row.get("name")),
                "aqi": aqi,
                "category": text(row.get("category")),
                "primary_pollutant": text(
                    as_object(row.get("primaryPollutant")).get("name")
                ),
            }
            for row in as_records(raw.get("indexes"))
            if (aqi := number(row.get("aqi"))) is not None and text(row.get("code"))
        ]
        data = (
            {"time": text(raw.get("validTime")) or None, "indexes": indexes}
            if indexes
            else None
        )
    if data is None:
        return None
    url = (
        "https://dev.qweather.com/docs/api/air-quality/air-current/"
        if section == "air_quality"
        else _QWEATHER_SOURCE
    )
    return {"source": _QWEATHER, "source_url": url, **data}


def _meteo_hour(record: dict[str, Any]) -> dict[str, Any] | None:
    stamp = text(record.get("time"))
    temp = number(record.get("temperature_2m"))
    if not stamp or temp is None:
        return None
    code = number(record.get("weather_code"))
    return {
        "time": stamp,
        "temperature_c": temp,
        "feels_like_c": number(record.get("apparent_temperature")),
        "condition": _WMO_CONDITIONS.get(int(code), "")
        if code is not None and code.is_integer()
        else "",
        "humidity_percent": _percent(record.get("relative_humidity_2m")),
        "wind_kmh": number(record.get("wind_speed_10m")),
        "precipitation_probability_percent": _percent(
            record.get("precipitation_probability")
        ),
    }


def _meteo_rows(raw: Any, fields: tuple[str, ...]) -> list[dict[str, Any]]:
    columns = as_object(raw)
    times = columns.get("time")
    if not isinstance(times, list):
        return []
    values = {key: columns[key] for key in fields if isinstance(columns.get(key), list)}
    return [
        {
            "time": stamp,
            **{
                key: sequence[index]
                for key, sequence in values.items()
                if index < len(sequence)
            },
        }
        for index, stamp in enumerate(times[:240])
        if isinstance(stamp, str)
    ]


_HOURLY_FIELDS = (
    "temperature_2m",
    "apparent_temperature",
    "relative_humidity_2m",
    "weather_code",
    "wind_speed_10m",
    "precipitation_probability",
)
_DAILY_FIELDS = (
    "temperature_2m_max",
    "temperature_2m_min",
    "weather_code",
    "precipitation_probability_max",
)


def _meteo_forecast(raw: dict[str, Any], days: int, hours: int) -> dict[str, Any]:
    forecast: dict[str, Any] = {}
    source = {"source": _OPEN_METEO, "source_url": _OPEN_METEO_SOURCE}
    current = _meteo_hour(as_object(raw.get("current")))
    if current is not None:
        forecast["current"] = {**source, **current}
    daily = []
    for row in _meteo_rows(raw.get("daily"), _DAILY_FIELDS):
        hi, lo = (
            number(row.get("temperature_2m_max")),
            number(row.get("temperature_2m_min")),
        )
        if hi is None or lo is None:
            continue
        code = number(row.get("weather_code"))
        daily.append(
            {
                "date": row["time"],
                "temperature_max_c": hi,
                "temperature_min_c": lo,
                "condition": _WMO_CONDITIONS.get(int(code), "")
                if code is not None and code.is_integer()
                else "",
                "max_hourly_precipitation_probability_percent": _percent(
                    row.get("precipitation_probability_max")
                ),
            }
        )
    if daily:
        forecast["daily"] = {**source, "items": daily[:days]}
    if hours:
        start_hour = current["time"][:13] if current else ""
        hourly = [
            parsed
            for row in _meteo_rows(raw.get("hourly"), _HOURLY_FIELDS)
            if row["time"][:13] >= start_hour
            and (parsed := _meteo_hour(row)) is not None
        ]
        if hourly:
            forecast["hourly"] = {**source, "items": hourly[:hours]}
    return forecast


def _meteo_air_quality(raw: dict[str, Any]) -> dict[str, Any] | None:
    current = as_object(raw.get("current"))
    indexes = [
        {"standard": standard, "aqi": aqi}
        for field, standard in (("us_aqi", "美国 AQI"), ("european_aqi", "欧洲 AQI"))
        if (aqi := number(current.get(field))) is not None
    ]
    if not indexes:
        return None
    return {
        "source": _OPEN_METEO,
        "source_url": _AIR_QUALITY_SOURCE,
        "attribution": "Open-Meteo / CAMS",
        "time": text(current.get("time")) or None,
        "indexes": indexes,
        "pm2_5_ug_m3": number(current.get("pm2_5")),
        "pm10_ug_m3": number(current.get("pm10")),
    }


def _location_items(
    payload: Any, source: str, requested_country: str
) -> list[dict[str, Any]]:
    is_qweather = source == _QWEATHER
    candidates = []
    seen: set[tuple[float, float]] = set()
    for row in as_records(payload):
        name = text(row.get("name"))
        if not name:
            continue
        try:
            point = coordinates(
                row.get("lon" if is_qweather else "longitude"),
                row.get("lat" if is_qweather else "latitude"),
            )
        except ValueError:
            continue
        if point in seen:
            continue
        seen.add(point)
        country_name = text(row.get("country"))
        region = text(row.get("adm1" if is_qweather else "admin1"))
        country = text(row.get("country_code")).upper() or requested_country
        if not country and country_name.casefold() in (
            "china",
            "中国",
            "中华人民共和国",
        ):
            country = "CN"
        mainland = country == "CN" and not any(
            name in region.casefold()
            for name in (
                "香港",
                "澳门",
                "澳門",
                "台湾",
                "臺灣",
                "hong kong",
                "macao",
                "macau",
                "taiwan",
            )
        )
        candidates.append(
            {
                "name": name,
                "longitude": point[0],
                "latitude": point[1],
                "country": country_name,
                "country_code": country,
                "region": region,
                "subregion": text(row.get("adm2" if is_qweather else "admin2")),
                "timezone": text(row.get("tz" if is_qweather else "timezone")) or None,
                "geocoding_source": source,
                "coordinate_system": "GCJ-02" if is_qweather and mainland else "WGS84",
            }
        )
    return candidates


def _city_name(value: str) -> str:
    return value.casefold().removesuffix("市")


class GetWeatherTool(Tool):
    name = "get_weather"
    description = (
        "查询城市或带坐标系的经纬度的当前天气、1～7 天预报、未来最多 48 小时降水概率与空气质量。"
        "若海外地名用中文，请同时传入已知的英文或当地名称 alternate_place，例如 东京 / Tokyo。"
        "同名地点返回候选，需要确认地区或坐标；回答时注明观测时刻、来源和 AQI 标准。"
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "OBJECT",
        "properties": {
            "place": {"type": "STRING", "description": "城市或地名，例如 北京 或 东京"},
            "alternate_place": {
                "type": "STRING",
                "description": "同一地点的英文或当地名称，例如 Tokyo。主名称无结果时尝试。",
            },
            "country_code": {
                "type": "STRING",
                "description": "两位国家代码，帮助区分同名地点",
            },
            "longitude": {"type": "NUMBER", "description": "经度，与纬度一起传"},
            "latitude": {"type": "NUMBER", "description": "纬度，与经度一起传"},
            "coordinate_system": {
                "type": "STRING",
                "enum": ["WGS84", "GCJ-02"],
                "description": "默认 WGS84。若坐标来自高德，传 GCJ-02。",
            },
            "days": {"type": "INTEGER", "description": "1～7 天，默认 7"},
            "hours": {
                "type": "INTEGER",
                "description": "0～48 小时，默认 24，传 0 可省略",
            },
            "include_air_quality": {
                "type": "BOOLEAN",
                "description": "是否包含空气质量，默认是",
            },
        },
    }

    def __init__(
        self,
        *,
        default_open_meteo: bool = True,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        flag = os.getenv("OPEN_METEO_ENABLED")
        self._open_meteo = (
            default_open_meteo
            if flag is None
            else flag.strip().lower() in ("1", "true", "yes")
        )
        self._qweather_key = os.getenv("QWEATHER_API_KEY", "").strip()
        self._qweather_host = _configured_qweather_host()
        self._transport = transport

    @property
    def _has_qweather(self) -> bool:
        return bool(self._qweather_host and self._qweather_key)

    async def _qweather(
        self,
        client: httpx.AsyncClient,
        path: str,
        params: dict[str, str | int | float] | None = None,
    ) -> dict[str, Any]:
        payload = as_object(
            await get_json(
                client,
                self._qweather_host + path,
                _QWEATHER,
                params=params,
                headers={"X-QW-Api-Key": self._qweather_key},
            )
        )
        code = str(payload.get("code", "200"))
        if code != "200":
            if code == "404":
                return {}
            raise DataProviderError(
                f"和风天气：接口返回状态 {code if code.isascii() and code.isdecimal() else '异常'}。"
            )
        return payload

    async def _locations(
        self,
        client: httpx.AsyncClient,
        names: list[str],
        country: str,
        warnings: list[str],
    ) -> list[dict[str, Any]]:
        if self._has_qweather:
            try:
                params: dict[str, str | int | float] = {"lang": "zh", "number": 5}
                if country:
                    params["range"] = country.lower()
                for name in names:
                    payload = await self._qweather(
                        client,
                        "/geo/v2/city/lookup",
                        {**params, "location": name},
                    )
                    if candidates := _location_items(
                        payload.get("location"), _QWEATHER, country
                    ):
                        return candidates
            except DataProviderError as exc:
                warnings.append(str(exc))
        if self._open_meteo:
            for name in names:
                if candidates := await self._open_meteo_locations(
                    client, name, country
                ):
                    return candidates
        return []

    @staticmethod
    async def _open_meteo_locations(
        client: httpx.AsyncClient, place: str, country: str
    ) -> list[dict[str, Any]]:
        # 英文检索请求英文返回名，便于与调用者传入的地名精确匹配。
        params: dict[str, str | int | float] = {
            "name": place,
            "language": "en" if place.isascii() else "zh",
            "count": 5,
        }
        if country:
            params["countryCode"] = country
        payload = as_object(
            await get_json(
                client,
                "https://geocoding-api.open-meteo.com/v1/search",
                _OPEN_METEO,
                params=params,
            )
        )
        return _location_items(payload.get("results"), _OPEN_METEO, country)

    async def _primary_weather(
        self,
        client: httpx.AsyncClient,
        location: dict[str, Any],
        requested: set[str],
        days: int,
        hours: int,
        warnings: list[str],
    ) -> dict[str, Any]:
        coordinate = f"{location['latitude']:.2f}/{location['longitude']:.2f}"
        queries: dict[str, tuple[str, dict[str, str | int | float]]] = {
            "current": (f"/weather/v1/current/{coordinate}", {"lang": "zh"}),
            "daily": (f"/weather/v1/daily/{coordinate}", {"lang": "zh", "days": days}),
            "hourly": (
                f"/weather/v1/hourly/{coordinate}",
                {"lang": "zh", "hours": hours},
            ),
            "air_quality": (f"/airquality/v1/current/{coordinate}", {"lang": "zh"}),
        }
        sections = [name for name in _SECTIONS if name in requested]
        responses = await asyncio.gather(
            *(self._qweather(client, *queries[name]) for name in sections),
            return_exceptions=True,
        )
        forecast = {}
        for name, response in zip(sections, responses, strict=True):
            if isinstance(response, DataProviderError):
                warnings.append(f"{_SECTIONS[name]}：{response}")
            elif isinstance(response, BaseException):
                raise response
            elif data := _qweather_section(
                name, response, location.get("timezone") or ""
            ):
                forecast[name] = data
        return forecast

    async def _fill_from_open_meteo(
        self,
        client: httpx.AsyncClient,
        location: dict[str, Any],
        requested: set[str],
        forecast: dict[str, Any],
        days: int,
        hours: int,
        warnings: list[str],
    ) -> None:
        meteo_location = location
        if location["coordinate_system"] == "GCJ-02":
            if location.get("geocoding_source") != _QWEATHER:
                warnings.append(
                    "Open-Meteo 需要 WGS84 坐标。请传 WGS84 坐标或直接按城市名查询。"
                )
                return
            try:
                candidates = await self._open_meteo_locations(
                    client, location["name"], "CN"
                )
            except DataProviderError as exc:
                warnings.append(str(exc))
                return
            matches = [
                candidate
                for candidate in candidates
                if _city_name(candidate["name"]) == _city_name(location["name"])
            ]
            if len(matches) > 1 and location.get("region"):
                matches = [
                    candidate
                    for candidate in matches
                    if _city_name(candidate["region"]) == _city_name(location["region"])
                ]
            if len(matches) != 1:
                warnings.append(
                    "Open-Meteo 未能定位同一城市的 WGS84 坐标，回退的天气数据暂缺。"
                )
                return
            meteo_location = matches[0]
        coord = {
            "latitude": meteo_location["latitude"],
            "longitude": meteo_location["longitude"],
            "timezone": "auto",
        }
        fallback_point = {
            key: meteo_location[key]
            for key in ("longitude", "latitude", "coordinate_system")
        }
        if requested.intersection(("current", "daily", "hourly")) - forecast.keys():
            try:
                params = {
                    **coord,
                    "current": ",".join(
                        field
                        for field in _HOURLY_FIELDS
                        if field != "precipitation_probability"
                    ),
                    "daily": ",".join(_DAILY_FIELDS),
                    "forecast_days": max(days, (hours + 23) // 24 + 1)
                    if hours
                    else days,
                }
                if "hourly" in requested and "hourly" not in forecast:
                    params["hourly"] = ",".join(_HOURLY_FIELDS)
                payload = as_object(
                    await get_json(
                        client,
                        "https://api.open-meteo.com/v1/forecast",
                        _OPEN_METEO,
                        params=params,
                    )
                )
                location["timezone"] = (
                    location.get("timezone") or text(payload.get("timezone")) or None
                )
                for name, data in _meteo_forecast(payload, days, hours).items():
                    if name in requested:
                        forecast.setdefault(
                            name, {**data, "location_coordinates": fallback_point}
                        )
            except DataProviderError as exc:
                warnings.append(str(exc))
        if "air_quality" in requested and "air_quality" not in forecast:
            try:
                payload = as_object(
                    await get_json(
                        client,
                        "https://air-quality-api.open-meteo.com/v1/air-quality",
                        _OPEN_METEO,
                        params={**coord, "current": "us_aqi,european_aqi,pm2_5,pm10"},
                    )
                )
                location["timezone"] = (
                    location.get("timezone") or text(payload.get("timezone")) or None
                )
                if air := _meteo_air_quality(payload):
                    forecast["air_quality"] = {
                        **air,
                        "location_coordinates": fallback_point,
                    }
            except DataProviderError as exc:
                warnings.append(f"空气质量：{exc}")

    async def run(self, parameter: dict[str, Any]) -> ToolResult:
        warnings: list[str] = []
        try:
            place = text(parameter.get("place"))
            alternative = text(parameter.get("alternate_place"))
            country = country_code(parameter.get("country_code"))
            days = integer(parameter.get("days", 7), "days", 1, 7)
            hours = integer(parameter.get("hours", 24), "hours", 0, 48)
            include_air = parameter.get("include_air_quality", True)
            if not isinstance(include_air, bool):
                raise TypeError("include_air_quality 应为 true 或 false。")
            lon, lat = parameter.get("longitude"), parameter.get("latitude")
            if lon is not None or lat is not None:
                lon, lat = coordinates(lon, lat)
                system = parameter.get("coordinate_system", "WGS84")
                if system not in ("WGS84", "GCJ-02"):
                    raise ValueError("coordinate_system 请填写 WGS84 或 GCJ-02。")
                if system == "GCJ-02" and country not in ("CN", ""):
                    raise ValueError("中国大陆以外的天气坐标请使用 WGS84。")
                location: dict[str, Any] = {
                    "name": place or None,
                    "longitude": lon,
                    "latitude": lat,
                    "timezone": None,
                    "country_code": country,
                    "coordinate_system": system,
                }
            elif not place:
                raise ValueError("请提供地名，或提供带坐标系的经纬度。")
            else:
                location = {}
            if not self._has_qweather and not self._open_meteo:
                raise ValueError(
                    "天气服务未启用。请配置和风天气 API Host 与 Key，或在适用部署启用 Open-Meteo。"
                )
            if not self._has_qweather and (self._qweather_host or self._qweather_key):
                warnings.append("和风天气配置不完整，使用已启用的 Open-Meteo。")
            requested = {"current", "daily"}
            if hours:
                requested.add("hourly")
            if include_air:
                requested.add("air_quality")
            async with httpx.AsyncClient(
                timeout=8, transport=self._transport
            ) as client:
                if not location:
                    names = [place]
                    if alternative and alternative.casefold() != place.casefold():
                        names.append(alternative)
                    candidates = await self._locations(client, names, country, warnings)
                    if not candidates:
                        raise ValueError(
                            "未找到匹配地点，请补充城市的英文或当地名称、地区或经纬度。"
                        )
                    normalized_names = {_city_name(name) for name in names}
                    exact = [
                        item
                        for item in candidates
                        if _city_name(item["name"]) in normalized_names
                    ]
                    selected = exact or candidates
                    if len(selected) != 1:
                        return result(
                            {
                                "error": "地点有多个匹配，请确认地区或使用候选的经纬度。",
                                "candidates": selected,
                                "warnings": warnings,
                            },
                            error=True,
                        )
                    location = selected[0]
                forecast = {}
                if self._has_qweather and (
                    location.get("geocoding_source") == _QWEATHER
                    or location["coordinate_system"] == "GCJ-02"
                    or location["country_code"] not in ("", "CN")
                ):
                    forecast = await self._primary_weather(
                        client, location, requested, days, hours, warnings
                    )
                elif self._has_qweather and not self._open_meteo:
                    warnings.append(
                        "查中国大陆天气时，和风天气需要 GCJ-02 坐标。也可直接按城市名查询。"
                    )
                if self._open_meteo and requested - forecast.keys():
                    await self._fill_from_open_meteo(
                        client, location, requested, forecast, days, hours, warnings
                    )
            missing = [
                _SECTIONS[name]
                for name in _SECTIONS
                if name in requested and name not in forecast
            ]
            for name, count in (("daily", days), ("hourly", hours)):
                if name in forecast:
                    items = forecast[name]["items"][:count]
                    forecast[name]["items"] = items
                    if len(items) < count:
                        warnings.append(
                            f"{_SECTIONS[name]}仅返回 {len(items)} 条，共请求 {count} 条。"
                        )
            if not forecast:
                return result(
                    {
                        "error": "当前未获得可用天气数据。",
                        "location": location,
                        "warnings": warnings,
                    },
                    error=True,
                )
            return result(
                {
                    "location": location,
                    "fetched_at": utc_now(),
                    **forecast,
                    "missing": missing,
                    "warnings": warnings,
                }
            )
        except (ValueError, TypeError, DataProviderError) as exc:
            return result({"error": str(exc), "warnings": warnings}, error=True)
