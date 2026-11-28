import json

import pytest

from agent_service.adapters.tools.calculate import CalculateTool
from agent_service.adapters.tools.calendar_tools import DateCalcTool, GetCurrentTimeTool


@pytest.mark.asyncio
async def test_clock_uses_configured_default_and_accepts_explicit_timezone():
    clock = GetCurrentTimeTool("Asia/Singapore")
    local = json.loads((await clock.run({})).content)
    assert local["timezone"] == "Asia/Singapore"
    assert local["datetime"].endswith("+08:00")
    utc = json.loads((await clock.run({"timezone": "UTC"})).content)
    assert utc["datetime"].endswith("+00:00")


@pytest.mark.asyncio
async def test_workdays_account_for_weekend_makeup_and_calendar_coverage():
    tool = DateCalcTool()
    result = await tool.run(
        {"operation": "workdays", "start_date": "2024-02-03", "end_date": "2024-02-05"}
    )
    assert not result.is_error
    assert json.loads(result.content)["workdays"] == 2  # 2 月 4 日周日补班
    result = await tool.run(
        {"operation": "workdays", "start_date": "2100-01-01", "end_date": "2100-01-02"}
    )
    assert result.is_error
    assert "数据范围" in result.content


@pytest.mark.asyncio
async def test_calendar_local_time_requires_offset_at_dst_overlap():
    tool = DateCalcTool()
    base = {
        "operation": "convert_timezone",
        "source_timezone": "America/New_York",
        "target_timezone": "UTC",
    }
    overlap = await tool.run({**base, "datetime": "2024-11-03T01:30:00"})
    gap = await tool.run({**base, "datetime": "2024-03-10T02:30:00"})
    explicit = await tool.run({**base, "datetime": "2024-11-03T01:30:00-05:00"})
    assert overlap.is_error and "两次" in overlap.content
    assert gap.is_error and "跳过" in gap.content
    assert json.loads(explicit.content)["datetime"] == "2024-11-03T06:30:00+00:00"


@pytest.mark.asyncio
async def test_lunar_conversion_preserves_leap_month():
    tool = DateCalcTool()
    lunar = await tool.run({"operation": "solar_to_lunar", "date": "1976-10-01"})
    lunar_date = json.loads(lunar.content)
    assert lunar_date["date"] == "1976-08-08"
    assert lunar_date["is_leap_month"] is True
    solar = await tool.run(
        {
            "operation": "lunar_to_solar",
            "date": lunar_date["date"],
            "is_leap_month": True,
        }
    )
    assert json.loads(solar.content)["date"] == "1976-10-01"


@pytest.mark.parametrize(
    ("parameters", "field", "expected"),
    [
        ({"operation": "expression", "expression": "0.1 + 0.2"}, "result", "0.3"),
        (
            {
                "operation": "loan_payment",
                "principal": "1200",
                "annual_rate": "0",
                "periods": 12,
            },
            "payment_per_period",
            "100",
        ),
        ({"operation": "statistics", "values": ["10", "20", "30"]}, "mean", "20"),
        ({"operation": "normal_cdf", "value": "0"}, "result", "0.5"),
        (
            {
                "operation": "convert_unit",
                "value": "32",
                "from_unit": "°F",
                "to_unit": "°C",
            },
            "result",
            "0",
        ),
        (
            {
                "operation": "convert_currency",
                "value": "10",
                "rate": "7.1",
                "from_unit": "USD",
                "to_unit": "CNY",
            },
            "result",
            "71",
        ),
        (
            {"operation": "integrate_polynomial", "values": ["0", "0", "2"]},
            "coefficients_ascending",
            ["0", "0", "0", "2/3"],
        ),
    ],
)
@pytest.mark.asyncio
async def test_calculation_returns_bounded_decimal_or_exact_fraction(
    parameters, field, expected
):
    outcome = await CalculateTool().run(parameters)
    assert not outcome.is_error
    assert json.loads(outcome.content)[field] == expected


@pytest.mark.parametrize("expression", ["sqrt(4)", "2 ** 1001", "1 / 0"])
@pytest.mark.asyncio
async def test_calculation_reports_unsupported_or_invalid_expression(expression: str):
    assert (
        await CalculateTool().run({"operation": "expression", "expression": expression})
    ).is_error
