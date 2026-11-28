import asyncio
import math
import os
import re
from itertools import pairwise
from typing import Any, ClassVar

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

Point = tuple[float, float]
_AMAP = "高德地图"
_NOMINATIM = "Nominatim / OpenStreetMap"
_OSRM = "OSRM / OpenStreetMap"
_AMAP_HOST = "https://restapi.amap.com"
_AMAP_SEARCH = "https://lbs.amap.com/api/webservice/guide/api-advanced/newpoisearch"
_AMAP_ROUTE = "https://lbs.amap.com/api/webservice/guide/api/newroute"
_OSM_ATTRIBUTION = "© OpenStreetMap contributors"
_OSM_COPYRIGHT = "https://www.openstreetmap.org/copyright"
_OSRM_PROFILES = {"driving": "driving", "walking": "foot", "cycling": "bike"}
_AMAP_MODES = {
    "driving": "driving",
    "walking": "walking",
    "cycling": "bicycling",
    "transit": "transit/integrated",
}


def _endpoint(name: str) -> str:
    raw = os.getenv(name, "").strip()
    if not raw:
        return ""
    try:
        url = httpx.URL(raw)
    except httpx.InvalidURL as exc:
        raise ValueError(f"{name} 的服务地址无效。") from exc
    if (
        url.scheme not in ("http", "https")
        or not url.host
        or url.userinfo
        or url.query
        or url.fragment
    ):
        raise ValueError(f"{name} 需要是不带凭据或查询参数的 HTTP(S) 服务地址。")
    return str(url).rstrip("/")


def _system(value: Any) -> str:
    if value not in ("GCJ-02", "WGS84"):
        raise ValueError("请明确坐标系 coordinate_system 为 GCJ-02 或 WGS84。")
    return value


def _point(value: Any) -> Point:
    if not isinstance(value, str) or len(value) > 50:
        raise ValueError("坐标请使用字符串 经度,纬度。")
    values = value.split(",")
    if len(values) != 2:
        raise ValueError("坐标请使用字符串 经度,纬度。")
    return coordinates(values[0], values[1])


def _point_parameter(point: Point) -> str:
    return f"{point[0]:.6f},{point[1]:.6f}"


def _point_fields(point: Point) -> dict[str, float]:
    return {"longitude": point[0], "latitude": point[1]}


def _distance(first: Point, second: Point) -> float:
    lon_a, lat_a, lon_b, lat_b = map(math.radians, (*first, *second))
    haversine = (
        math.sin((lat_b - lat_a) / 2) ** 2
        + math.cos(lat_a) * math.cos(lat_b) * math.sin((lon_b - lon_a) / 2) ** 2
    )
    return 12_742_000 * math.asin(math.sqrt(min(1, haversine)))


def _nominatim_viewbox(center: Point, radius: int) -> str:
    lon, lat = center
    lat_span = radius / 111_000
    lon_span = lat_span / max(0.001, math.cos(math.radians(abs(lat) + lat_span)))
    if abs(lat) + lat_span >= 89 or abs(lon) + lon_span >= 180:
        raise ValueError("该坐标附近跨越极区或日期变更线，请改用城市关键词搜索。")
    return f"{lon - lon_span:.6f},{lat + lat_span:.6f},{lon + lon_span:.6f},{lat - lat_span:.6f}"


def _route_metrics(path: dict[str, Any]) -> tuple[float, float]:
    distance = number(path.get("distance"))
    duration = number(as_object(path.get("cost")).get("duration"))
    if duration is None:
        duration = number(path.get("duration"))
    if distance is None or duration is None or distance < 0 or duration < 0:
        raise DataProviderError("供应商未返回完整的路线距离与用时。")
    return distance, duration


def _transit_steps(path: dict[str, Any]) -> list[str]:
    instructions = []
    for segment in as_records(path.get("segments")):
        walking = number(as_object(segment.get("walking")).get("distance"))
        if walking is not None and walking > 0:
            instructions.append(f"步行 {walking:g} 米")
        lines = as_records(as_object(segment.get("bus")).get("buslines"))
        if lines:
            line = lines[0]
            name = text(line.get("name"))
            departure = text(as_object(line.get("departure_stop")).get("name"))
            arrival = text(as_object(line.get("arrival_stop")).get("name"))
            if name:
                stops = f"（{departure} → {arrival}）" if departure and arrival else ""
                instructions.append(f"{name}{stops}")
        railway = as_object(segment.get("railway"))
        if name := text(railway.get("name")):
            instructions.append(name)
    return instructions[:24]


def _osrm_steps(leg: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "name": text(step.get("name")),
            "maneuver": text(maneuver.get("type")),
            "direction": text(maneuver.get("modifier")),
            "distance_m": number(step.get("distance")),
        }
        for step in as_records(leg.get("steps"))[:24]
        if (maneuver := as_object(step.get("maneuver")))
    ]


class MapsService:
    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.amap_key = os.getenv("AMAP_API_KEY", "").strip()
        self.transport = transport

    async def _amap(
        self, client: httpx.AsyncClient, path: str, params: dict[str, str | int | float]
    ) -> dict[str, Any]:
        payload = as_object(
            await get_json(
                client,
                _AMAP_HOST + path,
                _AMAP,
                params={**params, "key": self.amap_key},
            )
        )
        if str(payload.get("status")) != "1":
            code = text(payload.get("infocode"))
            suffix = f"（{code}）" if code.isascii() and code.isdecimal() else ""
            raise DataProviderError(
                f"高德地图未能完成请求{suffix}，请检查 Key、配额或查询条件。"
            )
        return payload

    async def places_amap(
        self,
        client: httpx.AsyncClient,
        keyword: str,
        city: str,
        center: Point | None,
        radius: int,
        limit: int,
    ) -> list[dict[str, Any]]:
        path = "/v5/place/around" if center is not None else "/v5/place/text"
        params: dict[str, str | int | float] = {"keywords": keyword, "page_size": limit}
        if city:
            params.update(region=city, city_limit="true")
        if center is not None:
            params.update(location=_point_parameter(center), radius=radius)
        rows = as_records((await self._amap(client, path, params)).get("pois"))
        places = []
        for row in rows[:limit]:
            try:
                point = _point(row.get("location"))
            except ValueError:
                continue
            if name := text(row.get("name")):
                places.append(
                    {
                        "id": text(row.get("id")),
                        "name": name,
                        "address": text(row.get("address")),
                        "province": text(row.get("pname")),
                        "city": text(row.get("cityname")),
                        "district": text(row.get("adname")),
                        "city_code": text(row.get("citycode")),
                        "distance_m": number(row.get("distance"))
                        if center is not None
                        else None,
                        **_point_fields(point),
                    }
                )
        return places

    async def places_osm(
        self,
        client: httpx.AsyncClient,
        endpoint: str,
        keyword: str,
        city: str,
        country: str,
        center: Point | None,
        radius: int,
        limit: int,
    ) -> list[dict[str, Any]]:
        if (
            httpx.URL(endpoint).host.lower().rstrip(".")
            == "nominatim.openstreetmap.org"
        ):
            raise ValueError("请为 NOMINATIM_BASE_URL 配置自托管或已授权的服务端点。")
        params: dict[str, str | int | float] = {
            "q": f"{keyword}, {city}" if city else keyword,
            "format": "jsonv2",
            "addressdetails": 1,
            "limit": limit,
            "accept-language": "zh, en",
        }
        if country:
            params["countrycodes"] = country.lower()
        if center is not None:
            params.update(viewbox=_nominatim_viewbox(center, radius), bounded=1)
        payload = await get_json(
            client,
            f"{endpoint}/search",
            _NOMINATIM,
            params=params,
            headers={"User-Agent": "LS-agent-game/2.0"},
        )
        if not isinstance(payload, list):
            raise DataProviderError("Nominatim 未返回地点列表。")
        places = []
        for row in as_records(payload)[:limit]:
            try:
                point = coordinates(row.get("lon"), row.get("lat"))
            except ValueError:
                continue
            dist = _distance(center, point) if center is not None else None
            if dist is not None and dist > radius:
                continue
            address = text(row.get("display_name"))
            if name := text(row.get("name")) or address:
                osm_type, osm_id = text(row.get("osm_type")), row.get("osm_id")
                place_id = (
                    f"{osm_type}/{osm_id}" if osm_type and type(osm_id) is int else ""
                )
                places.append(
                    {
                        "id": place_id,
                        "name": name,
                        "address": address,
                        "distance_m": round(dist) if dist is not None else None,
                        **_point_fields(point),
                    }
                )
        return places

    async def route_amap(
        self,
        client: httpx.AsyncClient,
        points: list[Point],
        mode: str,
        city_codes: list[str],
    ) -> list[dict[str, Any]]:
        legs = []
        for index, (origin, destination) in enumerate(pairwise(points)):
            params: dict[str, str | int | float] = {
                "origin": _point_parameter(origin),
                "destination": _point_parameter(destination),
                "show_fields": "cost",
            }
            if mode == "transit":
                params.update(city1=city_codes[index], city2=city_codes[index + 1])
            payload = await self._amap(
                client, f"/v5/direction/{_AMAP_MODES[mode]}", params
            )
            path = as_records(
                as_object(payload.get("route")).get(
                    "transits" if mode == "transit" else "paths"
                )
            )
            if not path:
                raise DataProviderError(f"高德地图未找到第 {index + 1} 段的可行路线。")
            best = path[0]
            distance, duration = _route_metrics(best)
            steps = (
                _transit_steps(best)
                if mode == "transit"
                else [
                    instruction
                    for step in as_records(best.get("steps"))[:24]
                    if (instruction := text(step.get("instruction")))
                ]
            )
            legs.append(
                {
                    "origin": _point_fields(origin),
                    "destination": _point_fields(destination),
                    "distance_m": distance,
                    "duration_s": duration,
                    "steps": steps,
                }
            )
        return legs

    async def route_osrm(
        self, client: httpx.AsyncClient, points: list[Point], mode: str
    ) -> list[dict[str, Any]]:
        env_name = f"OSRM_{mode.upper()}"
        endpoint = _endpoint(f"{env_name}_BASE_URL")
        if not endpoint:
            raise ValueError(f"请配置 {env_name}_BASE_URL 后查询此出行方式。")
        profile = os.getenv(f"{env_name}_PROFILE", _OSRM_PROFILES[mode]).strip()
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,24}", profile):
            raise ValueError(f"{env_name}_PROFILE 配置无效。")
        coordinate_list = ";".join(_point_parameter(point) for point in points)
        payload = as_object(
            await get_json(
                client,
                f"{endpoint}/route/v1/{profile}/{coordinate_list}",
                _OSRM,
                params={"alternatives": "false", "steps": "true", "overview": "false"},
            )
        )
        routes = as_records(payload.get("routes"))
        if payload.get("code") != "Ok" or not routes:
            raise DataProviderError("OSRM 未找到这组坐标和出行方式的路线。")
        raw_legs = as_records(routes[0].get("legs"))
        if len(raw_legs) != len(points) - 1:
            raise DataProviderError("OSRM 返回的路线分段与途经点数量不一致。")
        legs = []
        for index, path in enumerate(raw_legs):
            distance, duration = _route_metrics(path)
            legs.append(
                {
                    "origin": _point_fields(points[index]),
                    "destination": _point_fields(points[index + 1]),
                    "distance_m": distance,
                    "duration_s": duration,
                    "steps": _osrm_steps(path),
                }
            )
        return legs


class SearchPlacesTool(Tool):
    name = "search_places"
    description = (
        "按关键词、城市或中心点查找真实地点和 POI。海外地点尽可能给出两位国家代码。"
        "在搜索结果中确认地点后，把该条坐标、坐标系和公交所需的 city_code 交给 plan_route。"
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "OBJECT",
        "properties": {
            "keyword": {
                "type": "STRING",
                "description": "地点名称或关键词，例如 上海博物馆 或 咖啡馆",
            },
            "city": {"type": "STRING", "description": "城市或高德 citycode"},
            "country_code": {
                "type": "STRING",
                "description": "两位国家代码，例如 CN 或 JP",
            },
            "center": {"type": "STRING", "description": "可选周边搜索中心，经度,纬度"},
            "coordinate_system": {
                "type": "STRING",
                "enum": ["GCJ-02", "WGS84"],
                "description": "传 center 时必填",
            },
            "radius_m": {
                "type": "INTEGER",
                "description": "中心点搜索半径，100～10000 米，默认 2000",
            },
            "limit": {"type": "INTEGER", "description": "返回 1～10 条候选，默认 5"},
        },
        "required": ["keyword"],
    }

    def __init__(self, service: MapsService | None = None) -> None:
        self._service = service or MapsService()

    async def run(self, parameter: dict[str, Any]) -> ToolResult:
        warnings: list[str] = []
        try:
            keyword, city = text(parameter.get("keyword")), text(parameter.get("city"))
            if not keyword:
                raise ValueError("请输入地点关键词。")
            country = country_code(parameter.get("country_code"))
            limit = integer(parameter.get("limit", 5), "limit", 1, 10)
            radius = integer(parameter.get("radius_m", 2000), "radius_m", 100, 10000)
            center = (
                _point(parameter["center"])
                if parameter.get("center") is not None
                else None
            )
            raw_system = parameter.get("coordinate_system")
            system = (
                _system(raw_system)
                if center is not None or raw_system is not None
                else None
            )
            if system == "GCJ-02" and country not in ("", "CN"):
                raise ValueError("海外中心点搜索请使用 WGS84 坐标。")
            can_amap = (
                bool(self._service.amap_key)
                and country in ("", "CN")
                and system != "WGS84"
            )
            can_osm = system != "GCJ-02"
            endpoints = [(_AMAP, "GCJ-02", _AMAP_SEARCH)] if can_amap else []
            if can_osm and os.getenv("NOMINATIM_BASE_URL", "").strip():
                endpoints.append((_NOMINATIM, "WGS84", _OSM_COPYRIGHT))
            if not endpoints:
                raise ValueError(
                    "本次地点查询需要适用的地图数据源，请配置 AMAP_API_KEY 或 NOMINATIM_BASE_URL。"
                )
            async with httpx.AsyncClient(
                timeout=8, transport=self._service.transport
            ) as client:
                empty_result: dict[str, Any] | None = None
                for source, coord_system, source_url in endpoints:
                    try:
                        if source == _AMAP:
                            places = await self._service.places_amap(
                                client, keyword, city, center, radius, limit
                            )
                        else:
                            places = await self._service.places_osm(
                                client,
                                _endpoint("NOMINATIM_BASE_URL"),
                                keyword,
                                city,
                                country,
                                center,
                                radius,
                                limit,
                            )
                    except (DataProviderError, ValueError) as exc:
                        warnings.append(str(exc))
                        continue
                    response = {
                        "source": source,
                        "source_url": source_url,
                        "coordinate_system": coord_system,
                        "fetched_at": utc_now(),
                        "places": places,
                        "warnings": warnings,
                    }
                    if source == _NOMINATIM:
                        response["attribution"] = _OSM_ATTRIBUTION
                    if places:
                        return result(response)
                    warnings.append(f"{source} 没有返回匹配地点。")
                    empty_result = response
                if empty_result is not None:
                    return result(empty_result)
            raise ValueError("未能完成地点搜索。")
        except ValueError as exc:
            return result({"error": str(exc), "warnings": warnings}, error=True)


class PlanRouteTool(Tool):
    name = "plan_route"
    description = (
        "按已确认的起终点及途经点顺序查询真实路线，提供分段距离与预计耗时。"
        "使用 search_places 返回的同一坐标系。GCJ-02 使用高德，WGS84 使用该出行方式的 OSRM。"
        "高德公交必须提供每个点的城市代码。"
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "OBJECT",
        "properties": {
            "origin": {"type": "STRING", "description": "起点 经度,纬度"},
            "destination": {"type": "STRING", "description": "终点 经度,纬度"},
            "coordinate_system": {"type": "STRING", "enum": ["GCJ-02", "WGS84"]},
            "mode": {
                "type": "STRING",
                "enum": list(_AMAP_MODES),
                "description": "默认 driving",
            },
            "waypoints": {
                "type": "ARRAY",
                "items": {"type": "STRING"},
                "description": "最多 5 个途经点，经度,纬度",
            },
            "city_codes": {
                "type": "ARRAY",
                "items": {"type": "STRING"},
                "description": "仅高德公交需要，按起点、途经点、终点提供每个地点的 citycode，例如 010",
            },
        },
        "required": ["origin", "destination", "coordinate_system"],
    }

    def __init__(self, service: MapsService | None = None) -> None:
        self._service = service or MapsService()

    async def run(self, parameter: dict[str, Any]) -> ToolResult:
        try:
            system = _system(parameter.get("coordinate_system"))
            mode = parameter.get("mode", "driving")
            if not isinstance(mode, str) or mode not in _AMAP_MODES:
                raise ValueError("请选择 driving、walking、cycling 或 transit。")
            waypoints = parameter.get("waypoints", [])
            if not isinstance(waypoints, list) or len(waypoints) > 5:
                raise ValueError("waypoints 必须是最多 5 个坐标的数组。")
            points = [
                _point(item)
                for item in [
                    parameter.get("origin"),
                    *waypoints,
                    parameter.get("destination"),
                ]
            ]
            if any(origin == destination for origin, destination in pairwise(points)):
                raise ValueError("相邻地点使用了相同坐标，请检查路线。")
            city_codes: list[str] = []
            warnings: list[str] = []
            if mode == "transit":
                if system != "GCJ-02":
                    raise ValueError("当前公交路线需要高德地图及 GCJ-02 坐标。")
                raw_codes = parameter.get("city_codes")
                if (
                    not isinstance(raw_codes, list)
                    or len(raw_codes) != len(points)
                    or any(
                        not isinstance(code, str)
                        or not re.fullmatch(r"[0-9]{2,5}", code)
                        for code in raw_codes
                    )
                ):
                    raise ValueError(
                        "请为路线中的每个地点按顺序提供高德 citycode，例如 010。"
                    )
                city_codes = raw_codes
                if waypoints:
                    warnings.append(
                        "各段公交用时独立估算，中途候车时间以实际班次为准。"
                    )
            async with (
                asyncio.timeout(30),
                httpx.AsyncClient(
                    timeout=8, transport=self._service.transport
                ) as client,
            ):
                if system == "GCJ-02":
                    if not self._service.amap_key:
                        raise ValueError("高德路线需要配置 AMAP_API_KEY。")
                    legs = await self._service.route_amap(
                        client, points, mode, city_codes
                    )
                else:
                    legs = await self._service.route_osrm(client, points, mode)
            output = {
                "source": _AMAP if system == "GCJ-02" else _OSRM,
                "source_url": _AMAP_ROUTE if system == "GCJ-02" else _OSM_COPYRIGHT,
                "coordinate_system": system,
                "mode": mode,
                "fetched_at": utc_now(),
                "legs": legs,
                "distance_m": round(sum(leg["distance_m"] for leg in legs), 2),
                "duration_s": round(sum(leg["duration_s"] for leg in legs), 2),
                "warnings": warnings,
            }
            if system == "WGS84":
                output["attribution"] = _OSM_ATTRIBUTION
            return result(output)
        except (ValueError, DataProviderError) as exc:
            return result({"error": str(exc)}, error=True)
        except TimeoutError:
            return result({"error": "路线服务超时，请减少途经点并重试。"}, error=True)
