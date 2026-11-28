"""OpenAI Responses API 与 Sign in with ChatGPT (OAuth 2.0 + PKCE) 单元测试。"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from google.genai import types

from agent_service.adapters.gemini.stream import _sanitize_contents_for_gemini
from agent_service.adapters.openai_responses.auth import (
    DYNAMIC_AGENT_CLIENT_ID,
    ChatGPTAuthManager,
)
from agent_service.adapters.openai_responses.model import OpenAIResponsesModel
from agent_service.adapters.openai_responses.reviewer import OpenAIResponsesTextReviewer
from agent_service.adapters.openai_responses.translator import (
    contents_to_responses_input,
    encode_openai_reasoning_signature,
    tools_to_responses,
)
from agent_service.config.settings import ProviderConfig, settings
from agent_service.errors import AgentRuntimeError, ConfigurationError


def _openai_provider() -> ProviderConfig:
    return settings.providers["openai"]


def _make_fake_jwt(payload: dict[str, Any]) -> str:
    header = base64.urlsafe_b64encode(b'{"alg":"RS256"}').decode("ascii").rstrip("=")
    body = (
        base64.urlsafe_b64encode(json.dumps(payload).encode("utf-8"))
        .decode("ascii")
        .rstrip("=")
    )
    return f"{header}.{body}.sig"


@pytest.mark.asyncio
async def test_chatgpt_oauth_dynamic_registration_reuses_client_id_refreshes_and_fetches_models(
    tmp_path: Path,
):
    token_requests: list[dict[str, list[str]]] = []
    model_auth_headers: list[str] = []

    def fake_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/accounts/oauth/token":
            parsed_form = parse_qs(request.content.decode("utf-8"))
            token_requests.append(parsed_form)
            grant = parsed_form["grant_type"][0]
            if grant == "authorization_code":
                return httpx.Response(
                    200,
                    json={
                        "access_token": "tok_access_1",
                        "refresh_token": "tok_refresh_1",
                        "id_token": _make_fake_jwt(
                            {"email": "dev@example.com", "name": "Dev User"}
                        ),
                        "expires_in": 3600,
                        "client_id": "oaiapp_issued_123",
                    },
                )
            assert grant == "refresh_token"
            assert parsed_form["client_id"] == ["oaiapp_issued_123"]
            assert parsed_form["refresh_token"] == ["tok_refresh_1"]
            return httpx.Response(
                200,
                json={
                    "access_token": "tok_access_2",
                    "refresh_token": "tok_refresh_2",
                    "expires_in": 3600,
                },
            )
        if request.url.path == "/v1/models":
            model_auth_headers.append(request.headers["Authorization"])
            return httpx.Response(
                200,
                json={
                    "models": [
                        {
                            "slug": "gpt-hidden",
                            "display_name": "Hidden",
                            "visibility": "hide",
                        },
                        {
                            "slug": "gpt-5.4-codex",
                            "display_name": "GPT-5.4 Codex",
                            "visibility": "list",
                            "input_modalities": ["text", "image"],
                            "supported_reasoning_levels": [
                                {"effort": "low"},
                                {"effort": "medium"},
                                {"effort": "high"},
                                {"effort": "xhigh"},
                            ],
                            "default_reasoning_level": "high",
                        },
                    ]
                },
            )
        raise AssertionError(f"unexpected request: {request.url}")

    auth_file = tmp_path / "auth" / "chatgpt.json"
    manager = ChatGPTAuthManager(
        auth_file,
        transport=httpx.MockTransport(fake_handler),
    )

    # 1. 首次登录使用 dynamic_agent_client 与持久化 ext_agent_host_id
    started = manager.start_login("http://127.0.0.1:8000/api/auth/chatgpt/callback")
    qs = parse_qs(urlparse(started["authorize_url"]).query)
    assert qs["client_id"] == [DYNAMIC_AGENT_CLIENT_ID]
    assert qs["code_challenge_method"] == ["S256"]
    assert qs["resource"] == ["https://api.openai.com/v1"]
    assert "chatgpt.tokens.use.direct" in qs["scope"][0]
    assert qs["ext_agent_host_id"][0].startswith("urn:uuid:")
    first_host_id = qs["ext_agent_host_id"][0]

    # 非 loopback 回调必须拒绝
    with pytest.raises(ConfigurationError, match="loopback"):
        manager.start_login("https://evil.example.com/callback")

    # 2. 完成回调换取令牌并落盘保存签发 client_id
    status = await manager.complete_callback(
        code="auth_code_xyz",
        state=started["state"],
        callback_client_id="oaiapp_issued_123",
    )
    assert status.connected is True
    assert status.account_email == "dev@example.com"
    assert status.has_client_id is True
    assert token_requests[0]["client_id"] == ["oaiapp_issued_123"]
    assert token_requests[0]["code"] == ["auth_code_xyz"]

    # 3. 动态拉取账号可用模型并按 visibility == list 过滤
    models = await manager.fetch_models()
    assert [m.id for m in models] == ["gpt-5.4-codex"]
    assert models[0].label == "GPT-5.4 Codex"
    assert models[0].supports_vision is True
    assert models[0].thinking_levels == ("low", "medium", "high", "xhigh")
    assert models[0].effort_visual is not None
    assert models[0].effort_visual.effect == "bloom"
    assert model_auth_headers == ["Bearer tok_access_1"]

    # 4. 令牌临近过期时自动刷新
    manager._state["expires_at"] = 1.0
    refreshed_token = await manager.get_valid_access_token()
    assert refreshed_token == "tok_access_2"

    # 5. 退出并重新发起登录时复用签发的 oaiapp_issued_123 而非 dynamic_agent_client
    manager.logout()
    assert manager.is_connected() is False
    relogin = manager.start_login("http://127.0.0.1:8000/api/auth/chatgpt/callback")
    relogin_qs = parse_qs(urlparse(relogin["authorize_url"]).query)
    assert relogin_qs["client_id"] == ["oaiapp_issued_123"]
    assert "ext_agent_host_id" not in relogin_qs
    assert manager._ensure_host_id() == first_host_id


def test_responses_translator_and_gemini_cross_provider_sanitizer():
    tool = types.Tool(
        function_declarations=[
            types.FunctionDeclaration(
                name="read_file",
                description="读取文件",
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={"path": types.Schema(type=types.Type.STRING)},
                    required=["path"],
                ),
            )
        ]
    )
    resp_tools = tools_to_responses([tool])
    assert resp_tools == [
        {
            "type": "function",
            "name": "read_file",
            "description": "读取文件",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        }
    ]

    sig = encode_openai_reasoning_signature(
        item_id="rs_1", encrypted_content="enc_blob_abc"
    )
    history = [
        types.Content(
            role="user",
            parts=[
                types.Part.from_bytes(data=b"png", mime_type="image/png"),
                types.Part.from_text(text="看图读文件"),
            ],
        ),
        types.Content(
            role="model",
            parts=[
                types.Part(text="思考摘要", thought=True, thought_signature=sig),
                types.Part(
                    function_call=types.FunctionCall(
                        id="call_resp_1",
                        name="read_file",
                        args={"path": "README.md"},
                    )
                ),
            ],
        ),
        types.Content(
            role="user",
            parts=[
                types.Part(
                    function_response=types.FunctionResponse(
                        id="call_resp_1",
                        name="read_file",
                        response={"result": "ok", "is_error": False},
                    )
                ),
                types.Part.from_text(text="<runtime-notice>继续</runtime-notice>"),
            ],
        ),
    ]

    items = contents_to_responses_input(history)
    assert items[0]["type"] == "message"
    assert items[0]["content"][0]["type"] == "input_image"
    assert items[0]["content"][1] == {"type": "input_text", "text": "看图读文件"}
    assert items[1] == {
        "type": "reasoning",
        "id": "rs_1",
        "summary": [{"type": "summary_text", "text": "思考摘要"}],
        "encrypted_content": "enc_blob_abc",
    }
    assert items[2]["type"] == "function_call"
    assert items[2]["call_id"] == "call_resp_1"
    assert items[3]["type"] == "function_call_output"
    assert items[3]["call_id"] == "call_resp_1"
    assert items[4] == {
        "type": "message",
        "role": "user",
        "content": [{"type": "input_text", "text": "<runtime-notice>继续</runtime-notice>"}],
    }

    # 切回 Gemini 时必须剥离 OpenAI 专属 thought_signature
    sanitized = _sanitize_contents_for_gemini(history)
    assert sanitized[1].parts[0].thought is True
    assert sanitized[1].parts[0].thought_signature is None


@pytest.mark.asyncio
async def test_openai_responses_model_oauth_mode_omits_max_tokens_and_streams_tools(
    tmp_path: Path,
):
    captured_kwargs: dict[str, Any] = {}

    class FakeStream:
        def __init__(self):
            self.closed = False
            self._events = [
                SimpleNamespace(
                    type="response.reasoning_summary_text.delta",
                    delta="先检查文件",
                ),
                SimpleNamespace(
                    type="response.output_item.done",
                    item=SimpleNamespace(
                        type="reasoning",
                        id="rs_99",
                        encrypted_content="enc_xyz",
                        summary=[SimpleNamespace(text="先检查文件")],
                    ),
                ),
                SimpleNamespace(
                    type="response.output_text.delta",
                    delta="好的，正在读取。",
                ),
                SimpleNamespace(
                    type="response.output_item.added",
                    item=SimpleNamespace(
                        type="function_call",
                        id="fc_item_1",
                        call_id="call_oai_1",
                        name="read_file",
                    ),
                ),
                SimpleNamespace(
                    type="response.function_call_arguments.delta",
                    item_id="fc_item_1",
                    delta='{"path": "AGENTS.md"}',
                ),
                SimpleNamespace(
                    type="response.completed",
                    response=SimpleNamespace(
                        status="completed",
                        usage=SimpleNamespace(
                            input_tokens=120,
                            output_tokens=30,
                            input_tokens_details=SimpleNamespace(cached_tokens=80),
                        ),
                    ),
                ),
            ]

        def __aiter__(self):
            async def _gen():
                for ev in self._events:
                    yield ev

            return _gen()

        async def close(self):
            self.closed = True

    fake_stream = FakeStream()

    async def fake_create(**kwargs: Any):
        captured_kwargs.update(kwargs)
        return fake_stream

    fake_client = SimpleNamespace(responses=SimpleNamespace(create=fake_create))
    auth = ChatGPTAuthManager(tmp_path / "chatgpt.json")
    auth._state = {"access_token": "oauth_tok", "expires_at": 9999999999.0}

    model = OpenAIResponsesModel(
        _openai_provider(),
        model_name="gpt-5.4",
        auth_manager=auth,
        thinking_enabled=True,
        reasoning_effort="xhigh",
        client=cast(Any, fake_client),
    )

    events = [
        ev
        async for ev in model.stream(
            contents=[
                types.Content(role="user", parts=[types.Part.from_text(text="读 AGENTS.md")])
            ],
            system_prompt="系统指令",
            max_tokens=128000,
        )
    ]

    assert fake_stream.closed
    assert captured_kwargs["model"] == "gpt-5.4"
    assert captured_kwargs["store"] is False
    assert captured_kwargs["stream"] is True
    assert captured_kwargs["instructions"] == "系统指令"
    assert captured_kwargs["reasoning"] == {"effort": "xhigh", "summary": "auto"}
    assert captured_kwargs["include"] == ["reasoning.encrypted_content"]
    # ChatGPT 套餐直连模式下不得传入受限参数 max_output_tokens
    assert "max_output_tokens" not in captured_kwargs
    assert [ev.type for ev in events] == [
        "message_start",
        "thinking",
        "text",
        "tool_use_start",
        "message_done",
    ]
    done = events[-1].result
    assert done is not None
    assert done.contents == "好的，正在读取。"
    assert done.stop_reason == "tool_use"
    assert done.usage == {"input_len": 120, "output_len": 30, "cached_len": 80}
    assert done.function_calls[0].id == "call_oai_1"
    assert done.function_calls[0].args == {"path": "AGENTS.md"}
    assert any(
        p.thought is True and p.thought_signature is not None for p in done.raw_parts
    )


@pytest.mark.asyncio
async def test_openai_responses_model_handles_subscription_limit_and_api_key_fallback(
    tmp_path: Path,
):
    captured_api_key_kwargs: dict[str, Any] = {}

    class LimitFailedStream:
        def __aiter__(self):
            async def _gen():
                yield SimpleNamespace(
                    type="response.failed",
                    response=SimpleNamespace(
                        error=SimpleNamespace(
                            code="subscription_sharing_usage_limit_exceeded",
                            message="Weekly limit reached",
                        )
                    ),
                )

            return _gen()

    async def fake_limit_create(**kwargs: Any):
        _ = kwargs
        return LimitFailedStream()

    auth = ChatGPTAuthManager(tmp_path / "chatgpt.json")
    auth._state = {"access_token": "oauth_tok", "expires_at": 9999999999.0}

    oauth_model = OpenAIResponsesModel(
        _openai_provider(),
        model_name="gpt-5.4",
        auth_manager=auth,
        client=cast(Any, SimpleNamespace(responses=SimpleNamespace(create=fake_limit_create))),
    )
    with pytest.raises(AgentRuntimeError, match="chatgpt.com/settings/usage"):
        async for _ in oauth_model.stream(
            contents=[types.Content(role="user", parts=[types.Part.from_text(text="hi")])],
            system_prompt="sys",
            max_tokens=4096,
        ):
            pass

    class OkStream:
        def __aiter__(self):
            async def _gen():
                yield SimpleNamespace(type="response.output_text.delta", delta="ok")
                yield SimpleNamespace(
                    type="response.completed",
                    response=SimpleNamespace(
                        status="completed",
                        usage=SimpleNamespace(input_tokens=5, output_tokens=2),
                    ),
                )

            return _gen()

    async def fake_apikey_create(**kwargs: Any):
        captured_api_key_kwargs.update(kwargs)
        return OkStream()

    apikey_model = OpenAIResponsesModel(
        _openai_provider(),
        model_name="gpt-5.4",
        api_key="sk-direct-key",
        client=cast(Any, SimpleNamespace(responses=SimpleNamespace(create=fake_apikey_create))),
    )
    events = [
        ev
        async for ev in apikey_model.stream(
            contents=[types.Content(role="user", parts=[types.Part.from_text(text="hi")])],
            system_prompt="sys",
            max_tokens=4096,
        )
    ]
    assert captured_api_key_kwargs["max_output_tokens"] == 4096
    assert events[-1].result is not None
    assert events[-1].result.contents == "ok"


@pytest.mark.asyncio
async def test_openai_responses_reviewer_streams_json_schema(tmp_path: Path):
    captured: dict[str, Any] = {}

    class ReviewStream:
        def __aiter__(self):
            async def _gen():
                yield SimpleNamespace(
                    type="response.output_text.delta",
                    delta='{"verdict": "allow", "category": "none"}',
                )
                yield SimpleNamespace(
                    type="response.completed",
                    response=SimpleNamespace(
                        status="completed",
                        usage=SimpleNamespace(input_tokens=20, output_tokens=8),
                    ),
                )

            return _gen()

    async def fake_create(**kwargs: Any):
        captured.update(kwargs)
        return ReviewStream()

    auth = ChatGPTAuthManager(tmp_path / "chatgpt.json")
    auth._state = {"access_token": "oauth_tok", "expires_at": 9999999999.0}
    reviewer = OpenAIResponsesTextReviewer(
        _openai_provider(),
        auth_manager=auth,
        client=cast(Any, SimpleNamespace(responses=SimpleNamespace(create=fake_create))),
    )
    res = await reviewer.check("正常技术内容")
    assert res.verdict == "allow"
    assert res.category == "none"
    assert captured["store"] is False
    assert captured["stream"] is True
    assert captured["reasoning"] == {"effort": "low"}
    assert captured["text"]["format"]["type"] == "json_schema"
    assert "max_output_tokens" not in captured
