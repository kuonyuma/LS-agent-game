import json
from datetime import date, timedelta

import httpx
import pytest

from agent_service.adapters.tools.exchange_rate import GetExchangeRateTool
from agent_service.adapters.tools.maps import (
    MapsService,
    PlanRouteTool,
    SearchPlacesTool,
)
from agent_service.adapters.tools.weather import GetWeatherTool


@pytest.mark.asyncio
async def test_weather_fills_hourly_forecast_after_primary_is_rate_limited(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("QWEATHER_API_HOST", "test.xy.qweatherapi.com")
    monkeypatch.setenv("QWEATHER_API_KEY", "qweather-test-key")
    monkeypatch.setenv("OPEN_METEO_ENABLED", "1")

    def respond(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.url.host == "test.xy.qweatherapi.com":
            assert request.headers["X-QW-Api-Key"] == "qweather-test-key"
            if path == "/geo/v2/city/lookup":
                assert request.url.params["range"] == "cn"
                return httpx.Response(
                    200,
                    json={
                        "code": "200",
                        "location": [
                            {
                                "name": "北京",
                                "lon": "116.40",
                                "lat": "39.90",
                                "tz": "Asia/Shanghai",
                            }
                        ],
                    },
                )
            if path == "/weather/v1/current/39.90/116.40":
                return httpx.Response(
                    200,
                    json={
                        "validTime": "2026-11-28T03:00Z",
                        "temperature": {"value": 8, "unit": "°C"},
                        "humidity": 0.55,
                        "wind": {"speed": {"value": 5, "unit": "m/s"}},
                    },
                )
            if path == "/weather/v1/daily/39.90/116.40":
                assert request.url.params["days"] == "1"
                return httpx.Response(
                    200,
                    json={
                        "days": [
                            {
                                "forecastStartTime": "2026-11-27T16:00Z",
                                "temperatureMax": {"value": 10, "unit": "°C"},
                                "temperatureMin": {"value": 2, "unit": "°C"},
                            }
                        ]
                    },
                )
            if path == "/weather/v1/hourly/39.90/116.40":
                return httpx.Response(429)
            assert path == "/airquality/v1/current/39.90/116.40"
            return httpx.Response(
                200,
                json={"indexes": [{"code": "cn-mee", "name": "中国 AQI", "aqi": 60}]},
            )

        assert "x-qw-api-key" not in request.headers
        if request.url.host == "geocoding-api.open-meteo.com":
            assert request.url.params["name"] == "北京"
            assert request.url.params["countryCode"] == "CN"
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "name": "北京市",
                            "longitude": 116.396,
                            "latitude": 39.895,
                            "country_code": "CN",
                        },
                    ]
                },
            )
        assert request.url.host == "api.open-meteo.com"
        assert request.url.params["longitude"] == "116.396"
        assert request.url.params["latitude"] == "39.895"
        return httpx.Response(
            200,
            json={
                "timezone": "Asia/Shanghai",
                "current": {"time": "2026-11-28T11:15", "temperature_2m": 9},
                "hourly": {
                    "time": [
                        "2026-11-28T10:00",
                        "2026-11-28T11:00",
                        "2026-11-28T12:00",
                    ],
                    "temperature_2m": [7, 8, 9],
                    "precipitation_probability": [10, 35, 40],
                },
            },
        )

    weather = await GetWeatherTool(transport=httpx.MockTransport(respond)).run(
        {"place": "北京", "country_code": "CN", "days": 1, "hours": 2}
    )
    data = json.loads(weather.content)
    assert not weather.is_error
    assert data["current"]["source"] == "和风天气"
    assert data["current"]["temperature_c"] == 8
    assert data["current"]["humidity_percent"] == 55
    assert data["current"]["wind_kmh"] == 18
    assert data["daily"]["items"][0]["date"] == "2026-11-28"
    assert data["location"]["coordinate_system"] == "GCJ-02"
    assert data["hourly"]["source"] == "Open-Meteo"
    assert data["hourly"]["location_coordinates"]["coordinate_system"] == "WGS84"
    assert [
        item["precipitation_probability_percent"] for item in data["hourly"]["items"]
    ] == [35, 40]
    assert data["air_quality"]["indexes"][0]["standard"] == "cn-mee"
    assert any("配额" in warning for warning in data["warnings"])
    assert data["missing"] == []


@pytest.mark.asyncio
async def test_qweather_only_can_resolve_overseas_city(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("QWEATHER_API_HOST", "test.xy.qweatherapi.com")
    monkeypatch.setenv("QWEATHER_API_KEY", "qweather-test-key")
    monkeypatch.setenv("OPEN_METEO_ENABLED", "0")

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "test.xy.qweatherapi.com"
        if request.url.path == "/geo/v2/city/lookup":
            assert request.url.params["range"] == "jp"
            return httpx.Response(
                200,
                json={
                    "code": "200",
                    "location": [
                        {
                            "name": "东京",
                            "lon": "139.69",
                            "lat": "35.68",
                            "country": "日本",
                            "tz": "Asia/Tokyo",
                        }
                    ],
                },
            )
        if request.url.path == "/weather/v1/current/35.68/139.69":
            return httpx.Response(
                200,
                json={
                    "validTime": "2026-11-28T03:00Z",
                    "temperature": {"value": 15, "unit": "°C"},
                },
            )
        assert request.url.path == "/weather/v1/daily/35.68/139.69"
        return httpx.Response(
            200,
            json={
                "days": [
                    {
                        "forecastStartTime": "2026-11-27T15:00Z",
                        "temperatureMax": {"value": 17, "unit": "°C"},
                        "temperatureMin": {"value": 11, "unit": "°C"},
                    }
                ]
            },
        )

    weather = await GetWeatherTool(transport=httpx.MockTransport(respond)).run(
        {
            "place": "东京",
            "country_code": "JP",
            "days": 1,
            "hours": 0,
            "include_air_quality": False,
        }
    )
    assert not weather.is_error
    data = json.loads(weather.content)
    assert data["location"]["coordinate_system"] == "WGS84"
    assert data["current"]["source"] == "和风天气"
    assert data["missing"] == []


@pytest.mark.asyncio
async def test_weather_tries_alternate_place_only_when_original_has_no_match(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.delenv("QWEATHER_API_KEY", raising=False)
    monkeypatch.setenv("OPEN_METEO_ENABLED", "1")
    searched = []

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.host == "geocoding-api.open-meteo.com":
            name = request.url.params["name"]
            searched.append(name)
            if name == "东京":
                assert request.url.params["language"] == "zh"
                return httpx.Response(200, json={})
            assert request.url.params["language"] == "en"
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "name": "Tokyo",
                            "country_code": "JP",
                            "latitude": 35.68,
                            "longitude": 139.69,
                        },
                        {
                            "name": "Tokyo Heliport",
                            "country_code": "JP",
                            "latitude": 35.63,
                            "longitude": 139.85,
                        },
                    ]
                },
            )
        assert request.url.host == "api.open-meteo.com"
        return httpx.Response(
            200,
            json={
                "timezone": "Asia/Tokyo",
                "current": {"time": "2026-11-28T12:00", "temperature_2m": 12},
                "daily": {
                    "time": ["2026-11-28"],
                    "temperature_2m_max": [15],
                    "temperature_2m_min": [7],
                },
            },
        )

    output = await GetWeatherTool(transport=httpx.MockTransport(respond)).run(
        {
            "place": "东京",
            "alternate_place": "Tokyo",
            "country_code": "JP",
            "days": 1,
            "hours": 0,
            "include_air_quality": False,
        }
    )
    assert not output.is_error
    assert searched == ["东京", "Tokyo"]
    assert json.loads(output.content)["location"]["timezone"] == "Asia/Tokyo"


@pytest.mark.asyncio
async def test_weather_lists_ambiguous_place_candidates(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.delenv("QWEATHER_API_KEY", raising=False)
    monkeypatch.setenv("OPEN_METEO_ENABLED", "1")

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "geocoding-api.open-meteo.com"
        assert request.url.params["countryCode"] == "US"
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "name": "Springfield",
                        "longitude": -89.64,
                        "latitude": 39.79,
                        "admin1": "Illinois",
                    },
                    {
                        "name": "Springfield",
                        "longitude": -93.29,
                        "latitude": 37.21,
                        "admin1": "Missouri",
                    },
                ]
            },
        )

    output = await GetWeatherTool(transport=httpx.MockTransport(respond)).run(
        {"place": "Springfield", "country_code": "US"}
    )
    assert output.is_error
    assert len(json.loads(output.content)["candidates"]) == 2


@pytest.mark.asyncio
async def test_weather_shared_mode_requires_opt_in_without_qweather(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.delenv("QWEATHER_API_KEY", raising=False)
    monkeypatch.delenv("OPEN_METEO_ENABLED", raising=False)
    transport = httpx.MockTransport(
        lambda _: pytest.fail("未配置天气源时不应发送外部请求")
    )
    output = await GetWeatherTool(default_open_meteo=False, transport=transport).run(
        {"place": "北京"}
    )
    assert output.is_error
    assert "天气服务未启用" in output.content


@pytest.mark.asyncio
async def test_reference_rate_uses_actual_observation_and_decimal_precision():
    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v2/rate/usd/jpy"
        assert request.url.params["date"] == "2024-05-18"
        return httpx.Response(
            200,
            content=b'{"date":"2024-05-17","base":"USD","quote":"JPY","rate":155.123400}',
        )

    output = await GetExchangeRateTool(httpx.MockTransport(respond)).run(
        {"base": "usd", "quote": "jpy", "date": "2024-05-18"}
    )
    assert not output.is_error
    data = json.loads(output.content)
    assert data["observed_on"] == "2024-05-17"
    assert data["rate"] == "155.123400"
    assert "之前" in data["warnings"][0]


@pytest.mark.asyncio
async def test_reference_series_uses_only_actual_days_inside_requested_window():
    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v2/rates"
        start = date.fromisoformat(request.url.params["from"])
        end = date.fromisoformat(request.url.params["to"])
        assert (end - start).days == 6
        rows = [
            {"date": observed.isoformat(), "base": "USD", "quote": "CNY", "rate": 7.1}
            for observed in (end, start - timedelta(days=1), start + timedelta(days=1))
        ]
        return httpx.Response(200, json=rows)

    output = await GetExchangeRateTool(httpx.MockTransport(respond)).run(
        {"base": "USD", "quote": "CNY", "days": 7}
    )
    assert not output.is_error
    data = json.loads(output.content)
    dates = [item["date"] for item in data["observations"]]
    assert len(dates) == 2
    assert data["start_date"] < dates[0] < dates[-1] == data["end_date"]


@pytest.mark.asyncio
async def test_place_search_falls_back_to_configured_osm_after_amap_limit(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("AMAP_API_KEY", "amap-test-key")
    monkeypatch.setenv("NOMINATIM_BASE_URL", "https://nominatim.test/geocode")

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.host == "restapi.amap.com":
            assert request.url.params["region"] == "北京市"
            return httpx.Response(200, json={"status": "0", "infocode": "10003"})
        assert request.url.path == "/geocode/search"
        assert request.url.params["countrycodes"] == "cn"
        assert "key" not in request.url.params
        return httpx.Response(
            200,
            json=[
                {
                    "name": "故宫",
                    "display_name": "故宫，北京市",
                    "lon": "116.397",
                    "lat": "39.918",
                    "osm_type": "relation",
                    "osm_id": 42,
                }
            ],
        )

    output = await SearchPlacesTool(MapsService(httpx.MockTransport(respond))).run(
        {"keyword": "故宫", "city": "北京市", "country_code": "CN"}
    )
    data = json.loads(output.content)
    assert not output.is_error
    assert data["coordinate_system"] == "WGS84"
    assert data["places"][0]["id"] == "relation/42"
    assert "10003" in data["warnings"][0]
    assert "amap-test-key" not in output.content


@pytest.mark.asyncio
async def test_routes_keep_waypoint_order_and_dont_change_transport_mode(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("OSRM_WALKING_BASE_URL", "https://walking.test/routed-foot")
    monkeypatch.delenv("OSRM_DRIVING_BASE_URL", raising=False)

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "walking.test"
        assert request.url.path.endswith(
            "/route/v1/foot/139.700000,35.680000;139.710000,35.680000;139.700000,35.690000"
        )
        return httpx.Response(
            200,
            json={
                "code": "Ok",
                "routes": [
                    {
                        "legs": [
                            {"distance": 800, "duration": 600},
                            {"distance": 1100, "duration": 780},
                        ]
                    }
                ],
            },
        )

    tool = PlanRouteTool(MapsService(httpx.MockTransport(respond)))
    route = {
        "origin": "139.7,35.68",
        "destination": "139.7,35.69",
        "waypoints": ["139.71,35.68"],
        "coordinate_system": "WGS84",
        "mode": "walking",
    }
    output = await tool.run(route)
    data = json.loads(output.content)
    assert not output.is_error
    assert data["legs"][1]["origin"]["longitude"] == 139.71
    assert data["distance_m"] == 1900
    assert data["duration_s"] == 1380
    driving = await tool.run({**route, "mode": "driving"})
    assert driving.is_error
    assert "OSRM_DRIVING_BASE_URL" in driving.content


@pytest.mark.asyncio
async def test_amap_transit_uses_city_codes_and_route_distance(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("AMAP_API_KEY", "amap-test-key")

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v5/direction/transit/integrated"
        assert request.url.params["city1"] == request.url.params["city2"] == "010"
        assert request.url.params["show_fields"] == "cost"
        return httpx.Response(
            200,
            json={
                "status": "1",
                "route": {
                    "distance": "2300",
                    "transits": [
                        {
                            "distance": "3600",
                            "cost": {"duration": "1200"},
                            "segments": [],
                        }
                    ],
                },
            },
        )

    tool = PlanRouteTool(MapsService(httpx.MockTransport(respond)))
    route = {
        "origin": "116.1,39.9",
        "destination": "116.2,39.9",
        "coordinate_system": "GCJ-02",
        "mode": "transit",
    }
    output = await tool.run({**route, "city_codes": ["010", "010"]})
    assert not output.is_error
    assert json.loads(output.content)["distance_m"] == 3600
    assert (await tool.run(route)).is_error
