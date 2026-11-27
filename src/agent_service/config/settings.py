import os
from dataclasses import dataclass
from math import isfinite  # isfinite(value) 用来判断一个数是不是有限数
from pathlib import Path
from typing import Any, Literal, cast

import yaml

_CONFIG_DIR = Path(__file__).parent

ProviderProtocol = Literal["gemini", "openai_compat"]
ThinkingMode = Literal["none", "toggleable", "always_on"]
JsonOutputMode = Literal["json_object", "json_schema"]
ThinkingLevelValue = Literal["off", "low", "medium", "high", "xhigh", "max"]
EmbeddingProviderType = Literal["gemini", "openai_compat", "ollama"]
EffortVisualEffect = Literal["glint", "align", "double_arc"]

VALID_THINKING_LEVELS: tuple[ThinkingLevelValue, ...] = (
    "off",
    "low",
    "medium",
    "high",
    "xhigh",
    "max",
)

_THINKING_FALLBACK_ORDER: dict[str, tuple[str, ...]] = {
    "off": ("off", "low", "medium", "high"),
    "low": ("low", "medium", "off", "high"),
    "medium": ("medium", "high", "low", "xhigh", "max"),
    "high": ("high", "medium", "xhigh", "max", "low"),
    "xhigh": ("xhigh", "max", "high", "medium", "low"),
    "max": ("max", "xhigh", "high", "medium", "low"),
}


@dataclass(frozen=True)
class EffortVisual:
    level: ThinkingLevelValue
    effect: EffortVisualEffect
    duration_ms: int


@dataclass(frozen=True)
class ModelOption:
    id: str
    label: str
    supports_vision: bool = False
    supports_thinking: bool = False
    thinking_levels: tuple[str, ...] = ()
    default_thinking_level: str = ""
    max_output_tokens: int = 65536
    effort_visual: EffortVisual | None = None


@dataclass(frozen=True)
class ProviderConfig:
    id: str
    label: str
    protocol: ProviderProtocol
    env_key: str
    default_model: str
    base_url: str = ""
    key: str = ""
    thinking_mode: ThinkingMode = "none"
    json_mode: JsonOutputMode = "json_object"
    models: tuple[ModelOption, ...] = ()


@dataclass(frozen=True)
class ModelConfig:
    name: str
    max_tokens: int
    key: str
    provider: str = "gemini"


@dataclass(frozen=True)
class SafetyConfig:
    chunk_chars: int = 800  # 定量触发阈值
    interval_seconds: float = 1.5  # 定时触发阈值
    timeout_seconds: float = 15.0  # 单次审核请求超时死线
    context_chars: int = 1200  # 滑动窗口上文长度
    max_buffer_chars: int = 16000  # 未审积压上限

    def __post_init__(self) -> None:
        lengths = (
            self.chunk_chars,
            self.context_chars,
            self.max_buffer_chars,
        )
        invalid_length_type = any(type(value) is not int for value in lengths)

        times = (self.interval_seconds, self.timeout_seconds)
        invalid_time = any(
            type(value) not in (int, float) or not isfinite(value) for value in times
        )

        if invalid_length_type or invalid_time:
            raise ValueError("安全审核的长度或时间类型无效")

        invalid_range = (
            self.chunk_chars <= 0
            or self.interval_seconds <= 0
            or self.timeout_seconds <= 0
            or self.context_chars < 0
            or self.max_buffer_chars < self.chunk_chars
        )

        if invalid_range:
            raise ValueError("安全审核的长度、时间或缓冲区范围无效")


@dataclass(frozen=True)
class EmbeddingConfig:
    provider: EmbeddingProviderType = "gemini"
    model: str = "gemini-embedding-2"
    dimensions: int = 1536
    base_url: str = ""
    env_key: str = "GEMINI_API_KEY"

    def __post_init__(self) -> None:
        if self.provider not in ("gemini", "openai_compat", "ollama"):
            raise ValueError(f"无效的 embedding.provider：{self.provider}")
        if not isinstance(self.model, str) or not self.model.strip():
            raise ValueError("embedding.model 不能为空")
        if (
            isinstance(self.dimensions, bool)
            or not isinstance(self.dimensions, int)
            or self.dimensions < 64
            or self.dimensions > 8192
        ):
            raise ValueError("embedding.dimensions 必须是 64~8192 的整数")


@dataclass(frozen=True)
class AppConfig:
    model_config: ModelConfig
    providers: dict[str, ProviderConfig]
    system_prompt: str
    safety: SafetyConfig
    safety_prompt: str
    embedding: EmbeddingConfig = EmbeddingConfig()


def _default_thinking_levels(
    thinking_mode: ThinkingMode, supports_thinking: bool
) -> tuple[tuple[str, ...], str]:
    if not supports_thinking or thinking_mode == "none":
        return (), ""
    if thinking_mode == "toggleable":
        return ("off", "low", "high", "max"), "high"
    return ("low", "high", "max"), "high"


def _default_providers(model_name: str, model_key: str) -> dict[str, ProviderConfig]:
    gemini_models = (
        ModelOption(
            id="gemini-4-hydrogen",
            label="Gemini 4 Hydrogen",
            supports_vision=True,
            supports_thinking=True,
            thinking_levels=("low", "medium", "high", "xhigh"),
            default_thinking_level="high",
            max_output_tokens=65536,
        ),
        ModelOption(
            id="gemini-4-neon",
            label="Gemini 4 Neon",
            supports_vision=True,
            supports_thinking=True,
            thinking_levels=("low", "medium", "high", "xhigh"),
            default_thinking_level="high",
            max_output_tokens=65536,
        ),
        ModelOption(
            id="gemini-4-carbon",
            label="Gemini 4 Carbon",
            supports_vision=True,
            supports_thinking=True,
            thinking_levels=("low", "medium", "high"),
            default_thinking_level="medium",
            max_output_tokens=65536,
        ),
    )
    if model_name and all(item.id != model_name for item in gemini_models):
        gemini_models = (
            ModelOption(
                id=model_name,
                label=model_name,
                supports_vision=True,
                supports_thinking=True,
                thinking_levels=("low", "medium", "high"),
                default_thinking_level="high",
                max_output_tokens=65536,
            ),
            *gemini_models,
        )
    return {
        "gemini": ProviderConfig(
            id="gemini",
            label="Google Gemini",
            protocol="gemini",
            env_key="GEMINI_API_KEY",
            default_model=model_name or "gemini-4-hydrogen",
            key=model_key,
            thinking_mode="always_on",
            json_mode="json_schema",
            models=gemini_models,
        ),
        "deepseek": ProviderConfig(
            id="deepseek",
            label="DeepSeek",
            protocol="openai_compat",
            base_url="https://api.deepseek.com",
            env_key="DEEPSEEK_API_KEY",
            default_model="deepseek-flash",
            thinking_mode="toggleable",
            json_mode="json_object",
            models=(
                ModelOption(
                    id="deepseek-flash",
                    label="DeepSeek Flash (V4.1)",
                    supports_vision=True,
                    supports_thinking=True,
                    thinking_levels=("off", "low", "high", "max"),
                    default_thinking_level="high",
                    max_output_tokens=393216,
                ),
                ModelOption(
                    id="deepseek-v4-pro",
                    label="DeepSeek V4 Pro",
                    supports_vision=False,
                    supports_thinking=True,
                    thinking_levels=("off", "low", "high", "max"),
                    default_thinking_level="high",
                    max_output_tokens=393216,
                ),
            ),
        ),
        "kimi": ProviderConfig(
            id="kimi",
            label="Kimi (Moonshot)",
            protocol="openai_compat",
            base_url="https://api.moonshot.cn/v1",
            env_key="MOONSHOT_API_KEY",
            default_model="kimi-k3.1-pro",
            thinking_mode="always_on",
            json_mode="json_schema",
            models=(
                ModelOption(
                    id="kimi-k3.1-pro",
                    label="Kimi K3.1 Pro",
                    supports_vision=True,
                    supports_thinking=True,
                    thinking_levels=("low", "high", "max"),
                    default_thinking_level="max",
                    max_output_tokens=131072,
                ),
                ModelOption(
                    id="kimi-k3.1-pro-highspeed",
                    label="Kimi K3.1 Pro Highspeed",
                    supports_vision=True,
                    supports_thinking=True,
                    thinking_levels=("low", "high", "max"),
                    default_thinking_level="max",
                    max_output_tokens=131072,
                ),
            ),
        ),
    }


def _parse_providers(
    raw_providers: Any,
    fallback_model_name: str,
    fallback_model_key: str,
) -> dict[str, ProviderConfig]:
    if raw_providers is None:
        return _default_providers(fallback_model_name, fallback_model_key)
    if not isinstance(raw_providers, dict):
        raise TypeError("配置文件的 providers 必须是对象")

    parsed: dict[str, ProviderConfig] = {}
    for provider_id, item in raw_providers.items():
        if not isinstance(provider_id, str) or not isinstance(item, dict):
            raise TypeError("providers 下的每一项必须为字符串键和对象值")
        protocol = item.get("protocol")
        if protocol not in ("gemini", "openai_compat"):
            raise ValueError(f"厂商 `{provider_id}` 的 protocol 无效：{protocol}")
        thinking_mode = item.get("thinking_mode", "none")
        if thinking_mode not in ("none", "toggleable", "always_on"):
            raise ValueError(
                f"厂商 `{provider_id}` 的 thinking_mode 无效：{thinking_mode}"
            )
        json_mode = item.get("json_mode", "json_object")
        if json_mode not in ("json_object", "json_schema"):
            raise ValueError(f"厂商 `{provider_id}` 的 json_mode 无效：{json_mode}")

        raw_models = item.get("models", [])
        if not isinstance(raw_models, list):
            raise TypeError(f"厂商 `{provider_id}` 的 models 必须是列表")
        models: list[ModelOption] = []
        for raw_model in raw_models:
            if not isinstance(raw_model, dict) or not isinstance(
                raw_model.get("id"), str
            ):
                raise TypeError(f"厂商 `{provider_id}` 的模型项格式无效")
            supports_thinking = bool(raw_model.get("supports_thinking", False))
            raw_levels = raw_model.get("thinking_levels")
            if raw_levels is None:
                levels, fallback_default = _default_thinking_levels(
                    cast(ThinkingMode, thinking_mode), supports_thinking
                )
            else:
                if not isinstance(raw_levels, list):
                    raise TypeError(
                        f"厂商 `{provider_id}` 模型 `{raw_model['id']}` 的 thinking_levels 必须是列表"
                    )
                normalized_levels: list[str] = []
                for lv in raw_levels:
                    if lv is False:
                        lv = "off"
                    if not isinstance(lv, str) or lv not in VALID_THINKING_LEVELS:
                        raise ValueError(
                            f"厂商 `{provider_id}` 模型 `{raw_model['id']}` 包含无效思考档位：{lv}"
                        )
                    if lv not in normalized_levels:
                        normalized_levels.append(lv)
                levels = tuple(normalized_levels)
                fallback_default = levels[-1] if levels else ""

            raw_default = raw_model.get("default_thinking_level")
            if raw_default is False:
                raw_default = "off"
            if raw_default is None or raw_default == "":
                default_level = fallback_default
            else:
                default_level = str(raw_default).strip().lower()
                if levels and default_level not in levels:
                    raise ValueError(
                        f"厂商 `{provider_id}` 模型 `{raw_model['id']}` 的默认思考档位 `{default_level}` 不在允许列表中"
                    )

            effort_visual = None
            raw_visual = raw_model.get("effort_visual")
            if raw_visual is not None:
                model_context = f"厂商 `{provider_id}` 模型 `{raw_model['id']}`"
                if not isinstance(raw_visual, dict):
                    raise TypeError(f"{model_context} 的 effort_visual 必须是对象")
                visual_level = raw_visual.get("level")
                visual_effect = raw_visual.get("effect")
                duration_ms = raw_visual.get("duration_ms")
                if visual_level not in levels:
                    raise ValueError(f"{model_context} 的动效档位必须在允许列表中")
                if visual_effect not in ("glint", "align", "double_arc"):
                    raise ValueError(f"{model_context} 的动效类型无效：{visual_effect}")
                if type(duration_ms) is not int or duration_ms <= 0:
                    raise ValueError(f"{model_context} 的动效时长必须是正整数")
                effort_visual = EffortVisual(
                    level=cast(ThinkingLevelValue, visual_level),
                    effect=cast(EffortVisualEffect, visual_effect),
                    duration_ms=duration_ms,
                )

            raw_max_output = raw_model.get("max_output_tokens", 65536)
            if (
                isinstance(raw_max_output, bool)
                or not isinstance(raw_max_output, int)
                or raw_max_output < 256
            ):
                raise ValueError(
                    f"厂商 `{provider_id}` 模型 `{raw_model['id']}` 的 max_output_tokens 无效：{raw_max_output}"
                )

            models.append(
                ModelOption(
                    id=raw_model["id"],
                    label=str(raw_model.get("label") or raw_model["id"]),
                    supports_vision=bool(raw_model.get("supports_vision", False)),
                    supports_thinking=supports_thinking,
                    thinking_levels=levels,
                    default_thinking_level=default_level,
                    max_output_tokens=raw_max_output,
                    effort_visual=effort_visual,
                )
            )

        default_model = str(
            item.get("default_model")
            or (models[0].id if models else fallback_model_name)
        )
        key = str(
            item.get("key")
            or (fallback_model_key if provider_id == "gemini" else "")
        )
        parsed[provider_id] = ProviderConfig(
            id=provider_id,
            label=str(item.get("label") or provider_id),
            protocol=cast(ProviderProtocol, protocol),
            env_key=str(item.get("env_key") or ""),
            default_model=default_model,
            base_url=str(item.get("base_url") or ""),
            key=key,
            thinking_mode=cast(ThinkingMode, thinking_mode),
            json_mode=cast(JsonOutputMode, json_mode),
            models=tuple(models),
        )
    return parsed


def resolve_model_thinking_level(
    provider: ProviderConfig,
    model_name: str,
    requested: str | None = None,
    *,
    thinking_enabled: bool = True,
) -> str:
    """按具体模型支持的思考档位对请求值进行校验与平滑映射。"""

    option = next((m for m in provider.models if m.id == model_name), None)
    if option is not None:
        levels = option.thinking_levels
        default_level = option.default_thinking_level
    else:
        levels, default_level = _default_thinking_levels(
            provider.thinking_mode, provider.thinking_mode != "none"
        )

    if not levels:
        return "off"

    desired = "off" if not thinking_enabled else (requested or "").strip().lower()
    if desired in levels:
        return desired
    if desired in _THINKING_FALLBACK_ORDER:
        for candidate in _THINKING_FALLBACK_ORDER[desired]:
            if candidate in levels:
                return candidate
    if default_level in levels:
        return default_level
    return levels[0]


def resolve_model_max_tokens(
    provider: ProviderConfig,
    model_name: str,
    requested: int | None = None,
    *,
    thinking_enabled: bool = True,
) -> int:
    """按具体模型官方上限自动顶格解析输出 Token 上限；显式传入时自动钳位防 400。"""

    option = next((m for m in provider.models if m.id == model_name), None)
    cap = option.max_output_tokens if option is not None else 65536
    if requested is None:
        if provider.thinking_mode == "toggleable" and not thinking_enabled:
            return min(cap, 8192)
        return cap
    return max(256, min(requested, cap))


def resolve_api_key(
    provider: ProviderConfig,
    override_key: str | None = None,
) -> str:
    """按优先级解析 API Key：运行时显式传入 > 环境变量 > 配置文件回退。"""

    if override_key and override_key.strip():
        return override_key.strip()
    if provider.env_key:
        env_value = os.getenv(provider.env_key, "").strip()
        if env_value:
            return env_value
    fallback = provider.key.strip()
    if fallback and not fallback.startswith("your"):
        return fallback
    return ""


def _load_config() -> AppConfig:
    try:
        with open(_CONFIG_DIR / "config.yaml", "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
    except (OSError, yaml.YAMLError) as exc:
        raise RuntimeError(f"配置文件解析失败: {exc}") from exc

    if not isinstance(config, dict):
        raise TypeError("配置文件根节点必须是对象")

    if "model" not in config:
        raise ValueError("配置文件缺少 model 对象")

    model = config["model"]
    if not isinstance(model, dict):
        raise TypeError("配置文件的 model 必须是对象")

    required_model_keys = ("name", "max_tokens", "key")
    if any(key not in model for key in required_model_keys):
        raise ValueError("配置文件的 model 缺少 name、max_tokens 或 key")

    name = model["name"]
    max_tokens = model["max_tokens"]
    key = model["key"]
    provider = model.get("provider", "gemini")
    if (
        not isinstance(name, str)
        or not isinstance(max_tokens, int)
        or not isinstance(key, str)
        or not isinstance(provider, str)
    ):
        raise TypeError(
            "model.name、model.max_tokens、model.key 和 model.provider 类型无效"
        )

    providers = _parse_providers(config.get("providers"), name, key)
    if provider not in providers:
        raise ValueError(f"默认模型厂商 `{provider}` 未在 providers 中定义")

    raw_embedding = config.get("embedding")
    if raw_embedding is None:
        embedding_cfg = EmbeddingConfig()
    elif isinstance(raw_embedding, dict):
        embedding_cfg = EmbeddingConfig(
            provider=cast(
                EmbeddingProviderType,
                str(raw_embedding.get("provider") or "gemini").strip().lower(),
            ),
            model=str(raw_embedding.get("model") or "gemini-embedding-2").strip(),
            dimensions=int(raw_embedding.get("dimensions", 1536)),
            base_url=str(raw_embedding.get("base_url") or "").strip(),
            env_key=str(raw_embedding.get("env_key") or "GEMINI_API_KEY").strip(),
        )
    else:
        raise TypeError("配置文件的 embedding 必须是对象")

    return AppConfig(
        model_config=ModelConfig(
            name=name,
            max_tokens=max_tokens,
            key=key,
            provider=provider,
        ),
        providers=providers,
        system_prompt=(_CONFIG_DIR / "system_prompt.md").read_text(encoding="utf-8"),
        safety=SafetyConfig(**config.get("safety", {})),
        safety_prompt=(_CONFIG_DIR / "safety_prompt.md").read_text(encoding="utf-8"),
        embedding=embedding_cfg,
    )


settings = _load_config()
