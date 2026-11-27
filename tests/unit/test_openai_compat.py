"""DeepSeek / Kimi OpenAI 兼容协议适配器的单元测试。"""

from types import SimpleNamespace
from typing import Any, cast

import pytest
from google.genai import types

from agent_service.adapters.openai_compat.model import OpenAICompatibleModel
from agent_service.adapters.openai_compat.reviewer import OpenAICompatibleTextReviewer
from agent_service.adapters.openai_compat.translator import (
    contents_to_openai_messages,
    parse_tool_arguments,
    tools_to_openai,
)
from agent_service.config.settings import ProviderConfig, settings
from agent_service.errors import ConfigurationError, InvalidToolCallError


def _deepseek_provider() -> ProviderConfig:
    return settings.providers["deepseek"]


def _kimi_provider() -> ProviderConfig:
    return settings.providers["kimi"]


def test_tools_to_openai_normalizes_nested_schema_types():
    tool = types.Tool(
        function_declarations=[
            types.FunctionDeclaration(
                name="update_plan",
                description="更新计划",
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "action": types.Schema(
                            type=types.Type.STRING,
                            enum=["create", "replan"],
                        ),
                        "steps": types.Schema(
                            type=types.Type.ARRAY,
                            items=types.Schema(
                                type=types.Type.OBJECT,
                                properties={
                                    "id": types.Schema(type=types.Type.STRING),
                                    "done": types.Schema(type=types.Type.BOOLEAN),
                                },
                                required=["id"],
                            ),
                        ),
                    },
                    required=["action"],
                ),
            )
        ]
    )

    converted = tools_to_openai([tool])
    assert len(converted) == 1
    fn = converted[0]["function"]
    assert fn["name"] == "update_plan"
    assert fn["parameters"]["type"] == "object"
    assert fn["parameters"]["properties"]["action"]["type"] == "string"
    assert fn["parameters"]["properties"]["action"]["enum"] == ["create", "replan"]
    assert fn["parameters"]["properties"]["steps"]["type"] == "array"
    assert fn["parameters"]["properties"]["steps"]["items"]["type"] == "object"
    assert (
        fn["parameters"]["properties"]["steps"]["items"]["properties"]["done"]["type"]
        == "boolean"
    )


def test_contents_to_openai_preserves_reasoning_and_splits_tool_and_notice():
    history = [
        types.Content(
            role="user",
            parts=[
                types.Part.from_bytes(data=b"fake-png", mime_type="image/png"),
                types.Part.from_text(text="看看这张图并读一下配置"),
            ],
        ),
        types.Content(
            role="model",
            parts=[
                types.Part(text="先读文件确认配置。", thought=True),
                types.Part(
                    function_call=types.FunctionCall(
                        id="call_abc",
                        name="read_file",
                        args={"path": "pyproject.toml"},
                    )
                ),
            ],
        ),
        types.Content(
            role="user",
            parts=[
                types.Part(
                    function_response=types.FunctionResponse(
                        id="call_abc",
                        name="read_file",
                        response={"result": "name = 'agent-service'", "is_error": False},
                    )
                ),
                types.Part.from_text(text="<runtime-notice>\n阶段已更新\n</runtime-notice>"),
            ],
        ),
    ]

    messages = contents_to_openai_messages(history, "系统提示")

    assert messages[0] == {"role": "system", "content": "系统提示"}
    # 第一条用户消息为多模态数组
    assert messages[1]["role"] == "user"
    assert isinstance(messages[1]["content"], list)
    assert messages[1]["content"][0]["type"] == "image_url"
    assert messages[1]["content"][0]["image_url"]["url"].startswith(
        "data:image/png;base64,"
    )
    assert messages[1]["content"][1] == {"type": "text", "text": "看看这张图并读一下配置"}

    # assistant 消息必须完整保留 reasoning_content 和 tool_calls
    assert messages[2]["role"] == "assistant"
    assert messages[2]["reasoning_content"] == "先读文件确认配置。"
    assert messages[2]["tool_calls"][0]["id"] == "call_abc"
    assert messages[2]["tool_calls"][0]["function"]["name"] == "read_file"

    # tool 响应必须紧跟 assistant，runtime-notice 拆成独立 user 消息跟在后面
    assert messages[3]["role"] == "tool"
    assert messages[3]["tool_call_id"] == "call_abc"
    assert messages[4] == {
        "role": "user",
        "content": "<runtime-notice>\n阶段已更新\n</runtime-notice>",
    }


def test_parse_tool_arguments_rejects_malformed_json():
    assert parse_tool_arguments("", "demo") == {}
    assert parse_tool_arguments('{"a": 1}', "demo") == {"a": 1}
    with pytest.raises(InvalidToolCallError):
        parse_tool_arguments("{broken", "demo")


@pytest.mark.asyncio
async def test_deepseek_stream_handles_thinking_tool_calls_and_cache_tokens():
    captured_kwargs: dict[str, Any] = {}

    class FakeStream:
        def __init__(self):
            self.closed = False
            self._items = [
                SimpleNamespace(
                    usage=None,
                    choices=[
                        SimpleNamespace(
                            finish_reason=None,
                            delta=SimpleNamespace(
                                reasoning_content="分析需求...",
                                content=None,
                                tool_calls=None,
                            ),
                        )
                    ],
                ),
                SimpleNamespace(
                    usage=None,
                    choices=[
                        SimpleNamespace(
                            finish_reason=None,
                            delta=SimpleNamespace(
                                reasoning_content=None,
                                content="正在查询",
                                tool_calls=[
                                    SimpleNamespace(
                                        index=0,
                                        id="call_ds_1",
                                        function=SimpleNamespace(
                                            name="read_file",
                                            arguments='{"path":',
                                        ),
                                    )
                                ],
                            ),
                        )
                    ],
                ),
                SimpleNamespace(
                    usage=SimpleNamespace(
                        prompt_tokens=200,
                        completion_tokens=45,
                        prompt_cache_hit_tokens=160,
                    ),
                    choices=[
                        SimpleNamespace(
                            finish_reason="tool_calls",
                            delta=SimpleNamespace(
                                reasoning_content=None,
                                content=None,
                                tool_calls=[
                                    SimpleNamespace(
                                        index=0,
                                        id=None,
                                        function=SimpleNamespace(
                                            name=None,
                                            arguments=' "README.md"}',
                                        ),
                                    )
                                ],
                            ),
                        )
                    ],
                ),
            ]

        def __aiter__(self):
            async def _gen():
                for item in self._items:
                    yield item

            return _gen()

        async def close(self):
            self.closed = True

    fake_stream = FakeStream()

    async def fake_create(**kwargs: Any):
        captured_kwargs.update(kwargs)
        return fake_stream

    fake_client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=fake_create))
    )

    model = OpenAICompatibleModel(
        _deepseek_provider(),
        model_name="deepseek-flash",
        thinking_enabled=True,
        reasoning_effort="high",
        client=cast(Any, fake_client),
    )

    events = [
        ev
        async for ev in model.stream(
            contents=[
                types.Content(
                    role="user", parts=[types.Part.from_text(text="读一下 README")]
                )
            ],
            system_prompt="测试系统提示",
            max_tokens=1024,
            tools=None,
        )
    ]

    assert fake_stream.closed
    assert captured_kwargs["model"] == "deepseek-flash"
    assert captured_kwargs["extra_body"] == {
        "thinking": {"type": "enabled"},
        "reasoning_effort": "high",
    }
    assert [ev.type for ev in events] == [
        "message_start",
        "thinking",
        "text",
        "tool_use_start",
        "message_done",
    ]
    done = events[-1].result
    assert done is not None
    assert done.contents == "正在查询"
    assert done.stop_reason == "tool_use"
    assert done.usage == {"input_len": 200, "output_len": 45, "cached_len": 160}
    assert len(done.function_calls) == 1
    assert done.function_calls[0].id == "call_ds_1"
    assert done.function_calls[0].args == {"path": "README.md"}
    # raw_parts 中应保留 thought=True，供下一轮工具调用原样回传 reasoning_content
    assert any(p.thought is True and p.text == "分析需求..." for p in done.raw_parts)


@pytest.mark.asyncio
async def test_kimi_stream_uses_reasoning_effort_and_extracts_cached_details():
    captured_kwargs: dict[str, Any] = {}

    class FakeStream:
        def __aiter__(self):
            async def _gen():
                yield SimpleNamespace(
                    usage=SimpleNamespace(
                        prompt_tokens=300,
                        completion_tokens=60,
                        prompt_tokens_details=SimpleNamespace(cached_tokens=128),
                    ),
                    choices=[
                        SimpleNamespace(
                            finish_reason="stop",
                            delta=SimpleNamespace(
                                reasoning_content="推理步骤",
                                content="最终结论",
                                tool_calls=None,
                            ),
                        )
                    ],
                )

            return _gen()

    async def fake_create(**kwargs: Any):
        captured_kwargs.update(kwargs)
        return FakeStream()

    fake_client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=fake_create))
    )

    model = OpenAICompatibleModel(
        _kimi_provider(),
        model_name="kimi-k3.1-pro",
        reasoning_effort="max",
        client=cast(Any, fake_client),
    )

    events = [
        ev
        async for ev in model.stream(
            contents=[
                types.Content(role="user", parts=[types.Part.from_text(text="你好")])
            ],
            system_prompt="系统",
            max_tokens=512,
        )
    ]

    assert captured_kwargs["model"] == "kimi-k3.1-pro"
    assert captured_kwargs["extra_body"] == {"reasoning_effort": "max"}
    done = events[-1].result
    assert done is not None
    assert done.contents == "最终结论"
    assert done.usage == {"input_len": 300, "output_len": 60, "cached_len": 128}


@pytest.mark.asyncio
async def test_openai_compat_missing_key_raises_configuration_error(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    model = OpenAICompatibleModel(_deepseek_provider())
    with pytest.raises(ConfigurationError, match="DEEPSEEK_API_KEY"):
        async for _ in model.stream(
            contents=[], system_prompt="sys", max_tokens=256
        ):
            pass


@pytest.mark.asyncio
async def test_openai_compat_reviewers_match_provider_json_modes():
    ds_kwargs: dict[str, Any] = {}
    kimi_kwargs: dict[str, Any] = {}

    async def fake_ds_create(**kwargs: Any):
        ds_kwargs.update(kwargs)
        return SimpleNamespace(
            usage=SimpleNamespace(prompt_tokens=20, completion_tokens=8),
            choices=[
                SimpleNamespace(
                    finish_reason="stop",
                    message=SimpleNamespace(
                        content='{"verdict": "allow", "category": "none"}'
                    ),
                )
            ],
        )

    async def fake_kimi_create(**kwargs: Any):
        kimi_kwargs.update(kwargs)
        return SimpleNamespace(
            usage=SimpleNamespace(prompt_tokens=30, completion_tokens=10),
            choices=[
                SimpleNamespace(
                    finish_reason="stop",
                    message=SimpleNamespace(
                        content='{"verdict": "block", "category": "violence"}'
                    ),
                )
            ],
        )

    ds_reviewer = OpenAICompatibleTextReviewer(
        _deepseek_provider(),
        client=cast(
            Any,
            SimpleNamespace(
                chat=SimpleNamespace(completions=SimpleNamespace(create=fake_ds_create))
            ),
        ),
    )
    ds_res = await ds_reviewer.check("正常技术讨论")
    assert ds_res.verdict == "allow"
    assert ds_res.category == "none"
    assert ds_kwargs["response_format"] == {"type": "json_object"}
    assert ds_kwargs["extra_body"] == {"thinking": {"type": "disabled"}}

    kimi_reviewer = OpenAICompatibleTextReviewer(
        _kimi_provider(),
        client=cast(
            Any,
            SimpleNamespace(
                chat=SimpleNamespace(
                    completions=SimpleNamespace(create=fake_kimi_create)
                )
            ),
        ),
    )
    kimi_res = await kimi_reviewer.check("测试违规文本")
    assert kimi_res.verdict == "block"
    assert kimi_res.category == "violence"
    assert kimi_kwargs["response_format"]["type"] == "json_schema"
    assert kimi_kwargs["extra_body"] == {"reasoning_effort": "low"}
