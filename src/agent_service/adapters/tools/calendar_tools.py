import json
from datetime import UTC, date, datetime, timedelta
from typing import Any, ClassVar
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from chinese_calendar import is_workday
from lunardate import LunarDate

from agent_service.kernel.dispatch.base import Tool, ToolResult

_WEEKDAYS = ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")


def _zone(name: Any) -> ZoneInfo:
    if not isinstance(name, str) or not name or len(name) > 100:
        raise ValueError("时区必须是有效的 IANA 名称，例如 Asia/Shanghai。")
    try:
        return ZoneInfo(name)
    except (ValueError, ZoneInfoNotFoundError) as exc:
        raise ValueError(
            f"无法识别时区 {name}。请使用 IANA 名称，例如 America/New_York。"
        ) from exc


def _date(value: Any) -> date:
    if isinstance(value, str):
        try:
            parsed = date.fromisoformat(value)
            if parsed.isoformat() == value:
                return parsed
        except ValueError:
            pass
    raise ValueError("日期必须采用 YYYY-MM-DD 格式，且在公历中有效。")


def _result(**values: Any) -> ToolResult:
    return ToolResult(json.dumps(values, ensure_ascii=False), is_error=False)


def _lunar_solar_date(year: int, month: int, day: int, leap: bool = False) -> date:
    # Pyright 使用的旧类型声明尚未记录 lunardate 0.3 新增的方法名。
    return LunarDate(year, month, day, leap).to_solar_date()  # pyright: ignore[reportAttributeAccessIssue]


class GetCurrentTimeTool(Tool):
    name = "get_current_time"
    description = "读取当前时间和星期。使用 IANA 时区，例如 America/New_York；省略时使用本轮默认时区。"
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "OBJECT",
        "properties": {
            "timezone": {
                "type": "STRING",
                "description": "IANA 时区名称，例如 Asia/Shanghai",
            },
        },
    }

    def __init__(self, default_timezone: str = "UTC") -> None:
        self._default_timezone = default_timezone

    async def run(self, parameter: dict[str, Any]) -> ToolResult:
        try:
            zone = _zone(parameter.get("timezone", self._default_timezone))
            now = datetime.now(zone)
            return _result(
                datetime=now.isoformat(timespec="seconds"),
                timezone=zone.key,
                date=now.date().isoformat(),
                weekday=_WEEKDAYS[now.weekday()],
            )
        except ValueError as exc:
            return ToolResult(f"读取时间失败：{exc}", is_error=True)


def _convert_timezone(parameter: dict[str, Any]) -> ToolResult:
    raw = parameter.get("datetime")
    if not isinstance(raw, str) or "T" not in raw or len(raw) > 50:
        raise ValueError("时间请使用 ISO 8601 格式，例如 2026-05-01T09:30:00+08:00。")
    parsed = datetime.fromisoformat(raw)
    target = _zone(parameter.get("target_timezone"))
    source_name = parameter.get("source_timezone")
    source = _zone(source_name) if source_name is not None else None

    if parsed.tzinfo is None:
        if source is None:
            raise ValueError(
                "本地时间需要提供 source_timezone，或直接在时间中给出 UTC 偏移。"
            )
        possible: set[datetime] = set()
        for fold in (0, 1):
            utc = parsed.replace(tzinfo=source, fold=fold).astimezone(UTC)
            if utc.astimezone(source).replace(tzinfo=None) == parsed:
                possible.add(utc)
        if not possible:
            raise ValueError("该本地时间在夏令时切换时被跳过，请指定有效时间。")
        if len(possible) > 1:
            raise ValueError(
                "该本地时间在夏令时切换时出现两次，请在时间中指定 UTC 偏移。"
            )
        parsed = possible.pop()
    elif source is not None:
        localized = parsed.astimezone(source)
        if (
            localized.replace(tzinfo=None) != parsed.replace(tzinfo=None)
            or localized.utcoffset() != parsed.utcoffset()
        ):
            raise ValueError("时间中的 UTC 偏移与来源时区在该时刻的偏移不一致。")

    converted = parsed.astimezone(target)
    return _result(
        datetime=converted.isoformat(),
        timezone=target.key,
        weekday=_WEEKDAYS[converted.weekday()],
    )


def _workdays(start: date, end: date) -> ToolResult:
    if end < start:
        raise ValueError("工作日计算要求结束日期不早于起始日期。")
    try:
        is_workday(start)
        is_workday(end)
    except NotImplementedError as exc:
        raise ValueError(
            "日期超出了随应用安装的中国大陆节假日与调休数据范围。"
        ) from exc
    total = sum(
        is_workday(start + timedelta(days=offset))
        for offset in range(1, (end - start).days + 1)
    )
    return _result(
        workdays=total,
        calendar="中国大陆法定节假日及调休",
        start_date=start.isoformat(),
        end_date=end.isoformat(),
        includes_start=False,
        includes_end=True,
    )


def _lunar_to_solar(parameter: dict[str, Any]) -> ToolResult:
    raw = parameter.get("date")
    leap = parameter.get("is_leap_month", False)
    if not isinstance(raw, str) or len(raw) != 10 or not isinstance(leap, bool):
        raise ValueError(
            "农历日期请使用 YYYY-MM-DD 格式，并用 is_leap_month 标记闰月。"
        )
    year, month, day = (int(part) for part in raw.split("-"))
    if raw != f"{year:04d}-{month:02d}-{day:02d}":
        raise ValueError("农历日期请使用 YYYY-MM-DD 格式。")
    try:
        solar = _lunar_solar_date(year, month, day, leap)
    except ValueError as exc:
        raise ValueError("该农历日期无效或超出了支持的范围。") from exc
    return _result(
        date=solar.isoformat(), calendar="公历", weekday=_WEEKDAYS[solar.weekday()]
    )


class DateCalcTool(Tool):
    name = "date_calc"
    description = (
        "计算公历日期加减、日期差、中国大陆工作日（含补班）、时区转换和中国农历。"
        "用户说今天或明天时，可先用 get_current_time 确定其当地日期。"
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "OBJECT",
        "properties": {
            "operation": {
                "type": "STRING",
                "enum": [
                    "add_days",
                    "date_diff",
                    "workdays",
                    "convert_timezone",
                    "solar_to_lunar",
                    "lunar_to_solar",
                ],
            },
            "date": {
                "type": "STRING",
                "description": "日期加减或日历转换的 YYYY-MM-DD 日期",
            },
            "days": {"type": "INTEGER", "description": "add_days 的天数，可传负数"},
            "start_date": {
                "type": "STRING",
                "description": "日期差或工作日的起始公历日期",
            },
            "end_date": {
                "type": "STRING",
                "description": "日期差或工作日的结束公历日期",
            },
            "datetime": {
                "type": "STRING",
                "description": "时区转换的 ISO 8601 时间，可含 UTC 偏移",
            },
            "source_timezone": {
                "type": "STRING",
                "description": "无 UTC 偏移时需指定来源 IANA 时区",
            },
            "target_timezone": {
                "type": "STRING",
                "description": "时区转换的目标 IANA 时区",
            },
            "is_leap_month": {
                "type": "BOOLEAN",
                "description": "农历转公历时是否为闰月，默认否",
            },
        },
        "required": ["operation"],
    }

    async def run(self, parameter: dict[str, Any]) -> ToolResult:
        try:
            operation = parameter.get("operation")
            match operation:
                case "add_days":
                    start = _date(parameter.get("date"))
                    days = parameter.get("days")
                    if type(days) is not int:
                        raise ValueError("days 必须是整数。")
                    end = start + timedelta(days=days)
                    return _result(
                        date=end.isoformat(), weekday=_WEEKDAYS[end.weekday()]
                    )
                case "date_diff" | "workdays":
                    start = _date(parameter.get("start_date"))
                    end = _date(parameter.get("end_date"))
                    if operation == "workdays":
                        return _workdays(start, end)
                    return _result(days=(end - start).days)
                case "convert_timezone":
                    return _convert_timezone(parameter)
                case "solar_to_lunar":
                    solar = _date(parameter.get("date"))
                    # 库的年份以农历年为准，公历 1900 年春节前和 2100 年初需检查边界。
                    first = _lunar_solar_date(1900, 1, 1)
                    try:
                        last = _lunar_solar_date(2099, 12, 30)
                    except ValueError:
                        last = _lunar_solar_date(2099, 12, 29)
                    if not first <= solar <= last:
                        raise ValueError("日期超出了农历转换支持的范围。")
                    lunar = LunarDate.from_solar_date(  # pyright: ignore[reportAttributeAccessIssue]
                        solar.year, solar.month, solar.day
                    )
                    return _result(
                        date=f"{lunar.year:04d}-{lunar.month:02d}-{lunar.day:02d}",
                        is_leap_month=lunar.is_leap_month,
                        calendar="中国农历",
                    )
                case "lunar_to_solar":
                    return _lunar_to_solar(parameter)
                case _:
                    raise ValueError("请选择支持的日期计算操作。")
        except (ValueError, OverflowError) as exc:
            return ToolResult(f"日期计算失败：{exc}", is_error=True)
