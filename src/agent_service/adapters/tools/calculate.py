import ast
import json
import math
import operator
import statistics
from decimal import Decimal, DecimalException, localcontext
from fractions import Fraction
from typing import Any, ClassVar

from agent_service.kernel.dispatch.base import Tool, ToolResult

_BINARY_OPERATIONS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}

# 同组单位统一换到米、克、秒、升、平方米或字节。
_UNIT_GROUPS = (
    {
        "mm": "0.001",
        "cm": "0.01",
        "m": "1",
        "km": "1000",
        "in": "0.0254",
        "ft": "0.3048",
        "mi": "1609.344",
    },
    {"mg": "0.001", "g": "1", "kg": "1000", "lb": "453.59237", "oz": "28.349523125"},
    {"s": "1", "min": "60", "h": "3600", "day": "86400"},
    {"mL": "0.001", "L": "1", "m³": "1000", "US gal": "3.785411784"},
    {"cm²": "0.0001", "m²": "1", "ha": "10000", "km²": "1000000", "ft²": "0.09290304"},
    {
        "B": "1",
        "KB": "1000",
        "MB": "1000000",
        "GB": "1000000000",
        "KiB": "1024",
        "MiB": "1048576",
        "GiB": "1073741824",
    },
)
_TEMPERATURE_UNITS = {"°C", "°F", "K"}


def _number(value: Any, name: str) -> Decimal:
    if type(value) not in (str, int, float) or len(str(value)) > 80:
        raise ValueError(f"{name} 必须是有限十进制数字，最多 80 个字符。")
    number = Decimal(str(value))
    if not number.is_finite() or (number and abs(number.adjusted()) > 100):
        raise ValueError(f"{name} 不是有限数字，或超出支持的数量级。")
    return number if number else Decimal(0)


def _formatted(value: Decimal) -> str:
    if not value:
        return "0"
    if abs(value.adjusted()) > 50:
        return str(value.normalize())
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _currency_code(value: Any) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 3
        or not value.isascii()
        or not value.isalpha()
    ):
        raise ValueError("币种请用三字母代码，例如 USD 或 CNY。")
    return value.upper()


def _expression_value(source: Any) -> Decimal:
    if not isinstance(source, str) or not 0 < len(source.strip()) <= 512:
        raise ValueError("表达式长度需为 1～512 字符。")
    source = source.strip()
    tree = ast.parse(source, mode="eval")
    if sum(1 for _ in ast.walk(tree)) > 120:
        raise ValueError("表达式节点过多。")

    def evaluate(node: ast.expr, depth: int) -> Decimal:
        if depth > 20:
            raise ValueError("表达式嵌套过深。")
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            return _number(ast.get_source_segment(source, node), "数字")
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            operand = evaluate(node.operand, depth + 1)
            return -operand if isinstance(node.op, ast.USub) else operand
        if isinstance(node, ast.BinOp) and (
            operation := _BINARY_OPERATIONS.get(type(node.op))
        ):
            left, right = (
                evaluate(node.left, depth + 1),
                evaluate(node.right, depth + 1),
            )
            if isinstance(node.op, ast.Pow) and (
                right != right.to_integral_value() or abs(right) > 1000
            ):
                raise ValueError("幂运算指数必须是 -1000～1000 的整数。")
            return operation(left, right)
        raise ValueError("表达式只接受数字、括号和 + − * / % ** 运算。")

    return evaluate(tree.body, 0)


def _unit_conversion(parameter: dict[str, Any]) -> dict[str, Any]:
    value = _number(parameter.get("value"), "value")
    source, target = parameter.get("from_unit"), parameter.get("to_unit")
    if not isinstance(source, str) or not isinstance(target, str):
        raise TypeError("请提供 from_unit 和 to_unit。")
    if source in _TEMPERATURE_UNITS and target in _TEMPERATURE_UNITS:
        if source != target:
            if source == "°F":
                value = (value - 32) / Decimal("1.8")
            elif source == "K":
                value -= Decimal("273.15")
            if target == "°F":
                value = value * Decimal("1.8") + 32
            elif target == "K":
                value += Decimal("273.15")
        return {"result": _formatted(value), "unit": target}
    for units in _UNIT_GROUPS:
        if source in units and target in units:
            converted = value * Decimal(units[source]) / Decimal(units[target])
            return {"result": _formatted(converted), "unit": target}
    raise ValueError(
        "单位不受支持或量纲不同。请使用例如 km、mi、kg、lb、L、m²、MiB、°C。"
    )


def _finance(operation: str, parameter: dict[str, Any]) -> dict[str, Any]:
    principal = _number(parameter.get("principal"), "principal")
    annual_rate = _number(parameter.get("annual_rate"), "annual_rate")
    periods = parameter.get("periods")
    periods_per_year = parameter.get("periods_per_year", 12)
    if principal < 0 or not 0 <= annual_rate <= 10:
        raise ValueError("本金须非负，年利率须以 0～10 的小数传入，例如 5% 输入 0.05。")
    if type(periods) is not int or not 1 <= periods <= 1200:
        raise ValueError("periods 必须是 1～1200 的整数。")
    if type(periods_per_year) is not int or not 1 <= periods_per_year <= 366:
        raise ValueError("periods_per_year 必须是 1～366 的整数。")
    rate = annual_rate / periods_per_year
    growth = (1 + rate) ** periods
    if operation == "compound_interest":
        total = principal * growth
        return {"total": _formatted(total), "interest": _formatted(total - principal)}
    payment = (
        principal * rate * growth / (growth - 1) if growth != 1 else principal / periods
    )
    total = payment * periods
    return {
        "payment_per_period": _formatted(payment),
        "total": _formatted(total),
        "interest": _formatted(total - principal),
    }


def _values(raw: Any, limit: int) -> list[Decimal]:
    if not isinstance(raw, list) or not 1 <= len(raw) <= limit:
        raise ValueError(f"values 必须是包含 1～{limit} 个数字的数组。")
    return [_number(value, "values 中的元素") for value in raw]


def _distribution(operation: str, parameter: dict[str, Any]) -> dict[str, Any]:
    mean = _number(parameter.get("mean", "0"), "mean")
    sigma = _number(parameter.get("stddev", "1"), "stddev")
    if sigma <= 0:
        raise ValueError("标准差必须大于零。")
    distribution = statistics.NormalDist(float(mean), float(sigma))
    value = _number(parameter.get("value"), "value")
    if operation == "normal_cdf":
        result = distribution.cdf(float(value))
    else:
        probability = float(value)
        if not 0 < probability < 1:
            raise ValueError("概率必须介于 0 和 1 之间，且能以浮点精度区分端点。")
        result = distribution.inv_cdf(probability)
    if not math.isfinite(result):
        raise ValueError("分布结果超出支持范围。")
    return {"result": str(result), "approximate": True}


class CalculateTool(Tool):
    name = "calculate"
    description = (
        "在本地执行十进制运算、复利、等额本息、描述统计、正态分布、单位和已知汇率换算，"
        "以及单变量多项式求导和积分。金融参数请用十进制字符串。"
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "OBJECT",
        "properties": {
            "operation": {
                "type": "STRING",
                "enum": [
                    "expression",
                    "compound_interest",
                    "loan_payment",
                    "statistics",
                    "normal_cdf",
                    "normal_quantile",
                    "convert_unit",
                    "convert_currency",
                    "differentiate_polynomial",
                    "integrate_polynomial",
                ],
            },
            "expression": {
                "type": "STRING",
                "description": "算式，例如 (0.1 + 0.2) * 3；幂用 **",
            },
            "principal": {"type": "STRING", "description": "本金"},
            "annual_rate": {
                "type": "STRING",
                "description": "名义年利率，例如 0.05 表示 5%",
            },
            "periods": {"type": "INTEGER", "description": "总计息期数或还款期数"},
            "periods_per_year": {"type": "INTEGER", "description": "每年期数，默认 12"},
            "values": {
                "type": "ARRAY",
                "items": {"type": "STRING"},
                "description": "统计样本或多项式系数。系数从常数项到最高次项，例如 x²+2x+1 为 [1,2,1]",
            },
            "mean": {"type": "STRING", "description": "正态分布均值，默认 0"},
            "stddev": {"type": "STRING", "description": "正态分布标准差，默认 1"},
            "value": {
                "type": "STRING",
                "description": "待换算的量、正态 CDF 的横坐标或分位数的概率",
            },
            "from_unit": {
                "type": "STRING",
                "description": "来源单位或 ISO 4217 币种代码",
            },
            "to_unit": {
                "type": "STRING",
                "description": "目标单位或 ISO 4217 币种代码",
            },
            "rate": {
                "type": "STRING",
                "description": "每 1 单位来源币种对应的目标币种数量，由用户或汇率工具提供",
            },
        },
        "required": ["operation"],
    }

    async def run(self, parameter: dict[str, Any]) -> ToolResult:
        try:
            with localcontext(prec=100, Emin=-500, Emax=500):
                result = self._calculate(parameter)
            return ToolResult(json.dumps(result, ensure_ascii=False), is_error=False)
        except (
            ValueError,
            TypeError,
            DecimalException,
            OverflowError,
            SyntaxError,
            RecursionError,
        ) as exc:
            detail = str(exc)
            if isinstance(exc, DecimalException):
                detail = "数值无效、除数为零或结果超出支持范围。"
            elif isinstance(exc, (SyntaxError, RecursionError, OverflowError)):
                detail = "算式格式无效或超出计算上限。"
            return ToolResult(f"计算失败：{detail}", is_error=True)

    @staticmethod
    def _calculate(parameter: dict[str, Any]) -> dict[str, Any]:
        operation = parameter.get("operation")
        match operation:
            case "expression":
                return {
                    "result": _formatted(_expression_value(parameter.get("expression")))
                }
            case "compound_interest" | "loan_payment":
                return _finance(operation, parameter)
            case "statistics":
                values = _values(parameter.get("values"), 10_000)
                return {
                    "count": len(values),
                    "min": _formatted(min(values)),
                    "max": _formatted(max(values)),
                    "mean": _formatted(statistics.mean(values)),
                    "median": _formatted(statistics.median(values)),
                    "population_stddev": _formatted(statistics.pstdev(values)),
                    "sample_stddev": _formatted(statistics.stdev(values))
                    if len(values) > 1
                    else None,
                }
            case "normal_cdf" | "normal_quantile":
                return _distribution(operation, parameter)
            case "convert_unit":
                return _unit_conversion(parameter)
            case "convert_currency":
                source = _currency_code(parameter.get("from_unit"))
                target = _currency_code(parameter.get("to_unit"))
                rate = _number(parameter.get("rate"), "rate")
                if rate <= 0:
                    raise ValueError("请提供大于零的已知汇率。")
                return {
                    "result": _formatted(
                        _number(parameter.get("value"), "value") * rate
                    ),
                    "from_currency": source,
                    "currency": target,
                    "rate": _formatted(rate),
                }
            case "differentiate_polynomial" | "integrate_polynomial":
                coefficients = [
                    Fraction(value) for value in _values(parameter.get("values"), 51)
                ]
                if operation == "differentiate_polynomial":
                    derived = [
                        power * coefficient
                        for power, coefficient in enumerate(coefficients)
                        if power
                    ]
                    return {
                        "coefficients_ascending": [str(value) for value in derived]
                        or ["0"]
                    }
                integrated = [
                    str(coefficient / power)
                    for power, coefficient in enumerate(coefficients, 1)
                ]
                return {
                    "coefficients_ascending": ["0", *integrated],
                    "constant_of_integration": "C",
                }
            case _:
                raise ValueError("请选择支持的计算操作。")
