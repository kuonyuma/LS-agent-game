"""配置加载的单元测试。"""

import pytest

import agent_service.config.settings as settings_module
from agent_service.config.settings import (
    _default_providers,
    _parse_providers,
    model_supports_reasoning_effort,
    resolve_model_max_tokens,
    resolve_model_thinking_level,
    resolve_model_thinking_mode,
    resolve_thinking_param_style,
    settings,
    sync_missing_windows_env_vars,
)


def test_load_config_rejects_invalid_yaml(monkeypatch, tmp_path):
    (tmp_path / "config.yaml").write_text("model: [", encoding="utf-8")
    monkeypatch.setattr(settings_module, "_CONFIG_DIR", tmp_path)

    with pytest.raises(RuntimeError, match="配置文件解析失败"):
        settings_module._load_config()


def test_default_and_loaded_providers_include_glm_and_qwen():
    defaults = _default_providers("gemini-4-hydrogen", "")
    assert {"gemini", "deepseek", "kimi", "glm", "qwen", "openai"}.issubset(defaults)
    assert {"gemini", "deepseek", "kimi", "glm", "qwen", "openai"}.issubset(
        settings.providers
    )

    glm = settings.providers["glm"]
    assert glm.base_url == "https://open.bigmodel.cn/api/paas/v4"
    assert glm.env_key == "ZHIPU_API_KEY"
    assert glm.default_model == "glm-5.3"
    assert glm.thinking_mode == "always_on"
    assert resolve_thinking_param_style(glm) == "thinking_object"
    assert glm.preserve_thinking is True
    assert glm.include_stream_usage is False
    assert glm.json_mode == "json_object"
    assert [m.id for m in glm.models] == [
        "glm-5.3",
        "glm-5.3-flash",
        "glm-5.3-flashx",
        "glm-5.4",
        "glm-4.7-flash",
    ]
    assert glm.models[0].supports_vision is False
    assert all(m.supports_vision for m in glm.models[1:4])
    assert glm.models[4].supports_vision is False
    assert resolve_model_thinking_mode(glm, "glm-4.7-flash") == "toggleable"
    assert model_supports_reasoning_effort(glm, "glm-4.7-flash") is False
    assert model_supports_reasoning_effort(glm, "glm-5.3") is True

    openai_cfg = settings.providers["openai"]
    assert openai_cfg.protocol == "openai_responses"
    assert openai_cfg.auth_mode == "chatgpt_oauth"
    assert openai_cfg.base_url == "https://api.openai.com/v1"
    assert openai_cfg.env_key == "OPENAI_API_KEY"

    qwen = settings.providers["qwen"]
    assert qwen.base_url == "https://dashscope.aliyuncs.com/compatible-mode/v1"
    assert qwen.env_key == "DASHSCOPE_API_KEY"
    assert qwen.default_model == "qwen4-max"
    assert qwen.thinking_mode == "toggleable"
    assert resolve_thinking_param_style(qwen) == "enable_thinking"
    assert qwen.include_stream_usage is True
    assert qwen.json_mode == "json_schema"
    assert [m.id for m in qwen.models] == [
        "qwen4-max",
        "qwen3.8-max",
        "qwen3.7-plus",
        "qwen3.8-flash",
    ]
    assert all(m.supports_vision for m in qwen.models)


def test_glm_and_qwen_thinking_levels_and_max_tokens_resolution():
    glm = settings.providers["glm"]
    assert resolve_model_thinking_level(glm, "glm-5.3", "off", thinking_enabled=False) == "low"
    assert resolve_model_thinking_level(glm, "glm-5.3", "medium") == "high"
    assert resolve_model_thinking_level(glm, "glm-5.3", "xhigh") == "max"
    assert resolve_model_max_tokens(glm, "glm-5.3", thinking_enabled=True) == 131072
    assert resolve_model_max_tokens(glm, "glm-5.4", requested=200000) == 131072

    qwen = settings.providers["qwen"]
    assert resolve_model_thinking_level(qwen, "qwen4-max", "off", thinking_enabled=False) == "off"
    assert resolve_model_thinking_level(qwen, "qwen4-max", "medium") == "medium"
    assert resolve_model_thinking_level(qwen, "qwen4-max", "max") == "xhigh"
    assert resolve_model_max_tokens(qwen, "qwen4-max", thinking_enabled=True) == 262144
    assert resolve_model_max_tokens(qwen, "qwen4-max", thinking_enabled=False) == 131072
    assert (
        resolve_model_max_tokens(qwen, "qwen4-max", requested=200000, thinking_enabled=False)
        == 131072
    )
    assert resolve_model_max_tokens(qwen, "qwen3.8-max", thinking_enabled=True) == 131072
    assert resolve_model_max_tokens(qwen, "qwen3.7-plus", thinking_enabled=True) == 131072
    assert resolve_model_max_tokens(qwen, "qwen3.8-flash", thinking_enabled=True) == 131072


def test_parse_providers_validates_new_protocol_fields():
    with pytest.raises(ValueError, match="thinking_param_style"):
        _parse_providers(
            {
                "custom": {
                    "protocol": "openai_compat",
                    "thinking_mode": "toggleable",
                    "thinking_param_style": "invalid_style",
                    "models": [{"id": "m1"}],
                }
            },
            "m1",
            "",
        )

    with pytest.raises(ValueError, match="non_thinking_max_output_tokens"):
        _parse_providers(
            {
                "custom": {
                    "protocol": "openai_compat",
                    "thinking_mode": "toggleable",
                    "models": [
                        {
                            "id": "m1",
                            "non_thinking_max_output_tokens": 100,
                        }
                    ],
                }
            },
            "m1",
            "",
        )


def test_sync_missing_windows_env_vars_fills_missing_without_overwriting(monkeypatch):
    monkeypatch.setenv("TEST_EXISTING_KEY", "from-process")
    monkeypatch.delenv("TEST_MISSING_KEY", raising=False)
    monkeypatch.delenv("TEST_EMPTY_KEY", raising=False)

    fake_registry = {
        "TEST_EXISTING_KEY": "from-registry",
        "TEST_MISSING_KEY": "registry-secret",
        "TEST_EMPTY_KEY": "   ",
    }

    synced = sync_missing_windows_env_vars(
        ["TEST_EXISTING_KEY", "TEST_MISSING_KEY", "TEST_MISSING_KEY", "TEST_EMPTY_KEY"],
        reader=lambda name: fake_registry.get(name, ""),
    )

    assert synced == ["TEST_MISSING_KEY"]
    assert settings_module.os.getenv("TEST_EXISTING_KEY") == "from-process"
    assert settings_module.os.getenv("TEST_MISSING_KEY") == "registry-secret"
    assert settings_module.os.getenv("TEST_EMPTY_KEY") is None
