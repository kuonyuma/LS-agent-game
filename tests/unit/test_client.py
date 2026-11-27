"""Gemini 客户端配置与生命周期测试。"""

from types import SimpleNamespace

import pytest

import agent_service.adapters.gemini.client as client_module
from agent_service.errors import ConfigurationError


def test_get_client_raises_configuration_error_for_missing_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(client_module, "client", None)
    monkeypatch.setattr(
        client_module,
        "settings",
        SimpleNamespace(model_config=SimpleNamespace(key="your...")),
    )

    with pytest.raises(ConfigurationError, match="GEMINI_API_KEY"):
        client_module.get_client()


@pytest.mark.asyncio
async def test_close_client_releases_sync_and_async_clients(monkeypatch):
    calls: list[str] = []

    class FakeAsyncClient:
        async def aclose(self):
            calls.append("async")

    class FakeClient:
        aio = FakeAsyncClient()

        def close(self):
            calls.append("sync")

    monkeypatch.setattr(client_module, "client", FakeClient())

    await client_module.close_client()

    assert calls == ["async", "sync"]
    assert client_module.client is None


@pytest.mark.asyncio
async def test_stream_message_extracts_cached_content_token_count(monkeypatch):
    import agent_service.adapters.gemini.stream as stream_module

    async def fake_chunks():
        yield SimpleNamespace(
            usage_metadata=SimpleNamespace(
                prompt_token_count=120,
                candidates_token_count=30,
                cached_content_token_count=96,
            ),
            candidates=[
                SimpleNamespace(
                    finish_reason="STOP",
                    content=SimpleNamespace(
                        parts=[
                            SimpleNamespace(
                                text="命中缓存回复",
                                function_call=None,
                            )
                        ]
                    ),
                )
            ],
        )

    async def fake_stream(**kwargs):
        return fake_chunks()

    fake_client = SimpleNamespace(
        aio=SimpleNamespace(
            models=SimpleNamespace(generate_content_stream=fake_stream)
        )
    )
    monkeypatch.setattr(stream_module, "get_client", lambda: fake_client)

    events = [
        ev
        async for ev in stream_module.stream_message(
            contents=[],
            system_prompt="测试提示词",
        )
    ]

    done_event = events[-1]
    assert done_event.type == "message_done"
    assert done_event.result is not None
    assert done_event.result.usage == {
        "input_len": 120,
        "output_len": 30,
        "cached_len": 96,
    }


@pytest.mark.asyncio
async def test_stream_message_forwards_thinking_level_for_gemini_4(monkeypatch):
    import agent_service.adapters.gemini.stream as stream_module

    captured: list[dict] = []

    async def fake_chunks():
        yield SimpleNamespace(
            usage_metadata=None,
            candidates=[
                SimpleNamespace(
                    finish_reason="STOP",
                    content=SimpleNamespace(
                        parts=[SimpleNamespace(text="ok", function_call=None)]
                    ),
                )
            ],
        )

    async def fake_stream(**kwargs):
        captured.append(kwargs)
        return fake_chunks()

    fake_client = SimpleNamespace(
        aio=SimpleNamespace(
            models=SimpleNamespace(generate_content_stream=fake_stream)
        )
    )
    monkeypatch.setattr(stream_module, "get_client", lambda: fake_client)

    for level in ("high", "xhigh"):
        _ = [
            ev
            async for ev in stream_module.stream_message(
                contents=[],
                system_prompt="测试",
                model_name="gemini-4-hydrogen",
                thinking_level=level,
            )
        ]

    assert len(captured) == 2
    tc_high = captured[0]["config"].thinking_config
    assert tc_high is not None
    assert tc_high.include_thoughts is True
    assert str(tc_high.thinking_level).endswith("HIGH")

    tc_xhigh = captured[1]["config"].thinking_config
    assert tc_xhigh is not None
    assert tc_xhigh.include_thoughts is True
    assert str(tc_xhigh.thinking_level).upper().endswith("XHIGH")
