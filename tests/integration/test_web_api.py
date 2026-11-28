"""Web 适配器 REST API、会话持久化、SSE 流与指纹审批的集成测试。"""

import asyncio
import base64
import io
import json
import socket
from collections.abc import AsyncGenerator, Sequence
from pathlib import Path
from typing import Any
from zipfile import ZipFile

import httpx
import httpx2
import pymupdf
import pytest
import uvicorn
from docx import Document
from google.genai import types
from openai import AsyncOpenAI
from openpyxl import Workbook
from starlette.types import Message

from agent_service.adapters.dashscope.image_client import (
    DashScopeImageClient,
    DashScopeImageConfig,
)
from agent_service.adapters.openai_compat.model import OpenAICompatibleModel
from agent_service.adapters.security.reviewer import PassThroughTextReviewer
from agent_service.adapters.storage.account_store import AccountStore
from agent_service.adapters.storage.session_store import (
    SessionStore,
    serialize_contents,
)
from agent_service.adapters.tools import index as tool_index
from agent_service.adapters.tools.exchange_rate import GetExchangeRateTool
from agent_service.adapters.tools.generate_pptx import (
    PPTX_MIME,
    PresentonClient,
    PresentonConfig,
)
from agent_service.adapters.tools.maps import MapsService
from agent_service.adapters.tools.weather import GetWeatherTool
from agent_service.adapters.web.app import create_web_app
from agent_service.adapters.web.media_proxy import ImageProxyService
from agent_service.config.settings import settings
from agent_service.kernel.model.port import StreamEvent, StreamResult


def _parse_sse_frames(body: str) -> list[dict[str, Any]]:
    frames: list[dict[str, Any]] = []
    for block in body.split("\n\n"):
        data_lines = [
            line[5:].strip()
            for line in block.splitlines()
            if line.startswith("data:")
        ]
        if data_lines:
            frames.append(json.loads("\n".join(data_lines)))
    return frames


class ScriptedModel:
    """可编程模拟模型，记录每次请求可见的工具声明列表与 max_tokens。"""

    def __init__(self, turns: list[list[StreamEvent]]) -> None:
        self._turns = list(turns)
        self.seen_tool_names: list[set[str]] = []
        self.seen_max_tokens: list[int] = []
        self.seen_system_prompts: list[str] = []

    async def stream(
        self,
        *,
        contents: list[types.Content],
        system_prompt: str,
        max_tokens: int,
        tools: list[types.Tool] | None = None,
    ) -> AsyncGenerator[StreamEvent]:
        _ = contents
        self.seen_system_prompts.append(system_prompt)
        self.seen_max_tokens.append(max_tokens)
        names = {
            decl.name
            for group in (tools or [])
            for decl in (group.function_declarations or [])
            if decl.name
        }
        self.seen_tool_names.append(names)
        events = self._turns.pop(0)
        for ev in events:
            yield ev

    async def close(self) -> None:
        return None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("preview_available", "local_mode", "logout_during_approval"),
    [(True, True, False), (False, True, False), (True, False, False), (True, False, True)],
)
async def test_web_presenton_pptx_delivery(
    tmp_path: Path, preview_available: bool, local_mode: bool, logout_during_approval: bool
):
    slides = ["# 函数单调性", "# 练习\n判断函数在给定区间内的单调性。"]
    pptx_buffer = io.BytesIO()
    with ZipFile(pptx_buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr(
            "ppt/presentation.xml",
            '<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">'
            '<p:sldIdLst><p:sldId id="256"/><p:sldId id="257"/></p:sldIdLst></p:presentation>',
        )
    pptx = pptx_buffer.getvalue()
    with pymupdf.open() as document:
        document.new_page()
        document.new_page()
        pdf = document.tobytes()

    deck_id = "2f590ceb-e8ce-4e90-aea4-fe47134441af"
    upstream_calls: list[str] = []

    def fake_presenton(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer mock-key"
        path = request.url.path
        upstream_calls.append(path)
        if path == "/api/v1/ppt/presentation/generate":
            assert request.method == "POST"
            assert json.loads(request.content)["slides_markdown"] == slides
            return httpx.Response(200, json={"presentation_id": deck_id, "path": "/exports/deck.pptx"})
        if path == "/exports/deck.pptx":
            return httpx.Response(200, content=pptx)
        if path == f"/api/v1/ppt/presentation/{deck_id}/export":
            assert json.loads(request.content) == {"export_as": "pdf"}
            if not preview_available:
                return httpx.Response(404)
            return httpx.Response(200, json={"path": "/exports/deck.pdf"})
        assert path == "/exports/deck.pdf"
        return httpx.Response(200, content=pdf)

    call = types.FunctionCall(
        id="export_deck", name="generate_pptx", args={"title": "函数单调性", "slides_markdown": slides}
    )
    model = ScriptedModel(
        [
            [
                StreamEvent(type="message_start"),
                StreamEvent(
                    type="message_done",
                    result=StreamResult(
                        contents="",
                        stop_reason="tool_use",
                        usage={},
                        function_calls=[call],
                        raw_parts=[types.Part(function_call=call)],
                    ),
                ),
            ],
            [
                StreamEvent(type="message_start"),
                StreamEvent(type="text", text="课件已生成。"),
                StreamEvent(
                    type="message_done",
                    result=StreamResult(
                        contents="课件已生成。",
                        stop_reason="STOP",
                        usage={},
                        raw_parts=[types.Part.from_text(text="课件已生成。")],
                    ),
                ),
            ],
        ]
    )
    app = create_web_app(
        local_mode=local_mode,
        workspace=tmp_path,
        model_factory=lambda **_: model,
        reviewer_factory=lambda **_: PassThroughTextReviewer(),
        presenton_client=PresentonClient(
            PresentonConfig(httpx.URL("https://presenton.test"), "mock-key"),
            transport=httpx.MockTransport(fake_presenton),
        ),
    )

    write_header = {"X-Agent-Request": "1"}
    async with (
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1", headers=write_header) as client,
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1", headers=write_header) as member,
    ):
        stream_headers = [(b"content-type", b"application/json"), (b"x-agent-request", b"1")]
        if not local_mode:
            accounts = AccountStore(tmp_path / ".agent_data")
            accounts.create_account("owner", "test-password-owner", role="admin")
            accounts.create_account("member", "test-password-member", role="user")
            for actor, username in ((client, "owner"), (member, "member")):
                login = await actor.post("/api/auth/login", json={
                    "username": username, "password": f"test-password-{username}",
                })
                assert login.status_code == 200
            stream_headers.append((b"cookie", f"agent_service_login={client.cookies['agent_service_login']}".encode()))
        tools_payload = (await client.get("/api/tools")).json()["tools"]
        assert any(t["name"] == "generate_pptx" and t["requires_approval"] for t in tools_payload)
        session = (await client.post("/api/sessions", json={})).json()["session_id"]
        other = (await client.post("/api/sessions", json={})).json()["session_id"]
        path = f"/api/sessions/{session}/runs"
        body = json.dumps({"query": "做 2 页课件"}).encode()
        completed: dict[str, Any] = {}

        async def receive() -> dict[str, Any]:
            return {"type": "http.request", "body": body, "more_body": False}

        async def send(message: Message) -> None:
            for event in _parse_sse_frames(message.get("body", b"").decode()):
                if event["type"] == "tool.approval_required":
                    assert upstream_calls == []
                    if not local_mode:
                        assert (await member.post(f"/api/sessions/{session}/stop")).status_code == 404
                        foreign = await member.post(
                            f"/api/sessions/{session}/approvals/{event['approval_id']}",
                            json={"approved": True, "request_hash": event["request_hash"]},
                        )
                        assert foreign.status_code == 404
                    if logout_during_approval:
                        assert (await client.post("/api/auth/logout")).status_code == 200
                        assert (await client.get("/api/auth/me")).status_code == 401
                        return
                    approval = await client.post(
                        f"/api/sessions/{session}/approvals/{event['approval_id']}",
                        json={"approved": True, "request_hash": event["request_hash"]},
                    )
                    assert approval.status_code == 200
                if event["type"] == "run.completed":
                    completed.update(event)
                    download = await client.get(event["presentations"][0]["download_url"])
                    assert download.content == pptx
                    assert download.headers["content-type"] == PPTX_MIME
                    assert "attachment" in download.headers["content-disposition"]
                    assert download.headers["Cache-Control"] == "no-store"
                    if not local_mode:
                        assert (await member.get(event["presentations"][0]["download_url"])).status_code == 404

        async with asyncio.timeout(10):
            await app(
                {
                    "type": "http",
                    "asgi": {"version": "3.0", "spec_version": "2.4"},
                    "http_version": "1.1",
                    "method": "POST",
                    "scheme": "http",
                    "server": ("127.0.0.1", 80),
                    "client": ("127.0.0.1", 12345),
                    "path": path,
                    "query_string": b"",
                    "headers": stream_headers,
                },
                receive,
                send,
            )

        assert "generate_pptx" in model.seen_tool_names[0]
        if not local_mode:
            assert {"run_command", "read_file"}.isdisjoint(model.seen_tool_names[0])
            assert {"fetch_web_page", "get_current_time", "date_calc", "calculate"} <= set(
                model.seen_tool_names[0]
            )
        assert "# 制作 PowerPoint" in model.seen_system_prompts[0]
        if logout_during_approval:
            assert completed == {}
            assert upstream_calls == []
            login = await client.post("/api/auth/login", json={"username": "owner", "password": "test-password-owner"})
            assert login.status_code == 200
            assert not (await client.get(path.removesuffix("/runs"))).json()["running"]
            return
        artifact = completed["presentations"][0]
        assert artifact["filename"] == "函数单调性.pptx"
        assert artifact["slide_count"] == 2
        detail = (await client.get(f"/api/sessions/{session}")).json()
        assert detail["messages"][-1]["presentations"] == [artifact]
        assert (await client.get(artifact["download_url"].replace(session, other))).status_code == 404
        if preview_available:
            assert (await client.get(artifact["preview_url"])).content == pdf
            assert (await client.get(artifact["thumbnail_url"])).content.startswith(b"\x89PNG")
        else:
            assert artifact["preview_url"] is None and artifact["thumbnail_url"] is None
        assert upstream_calls.count("/api/v1/ppt/presentation/generate") == 1


@pytest.mark.parametrize(
    ("image_options", "model_ratio", "expected_size", "summary_stop"),
    [
        ({"aspect_ratio": "16:9"}, "1:1", "1536*864", "STOP"),
        (None, "16:9", "1536*864", "STOP"),
        ({}, None, "1024*1024", "STOP"),
        ({"aspect_ratio": "9:16"}, "1:1", "864*1536", "length"),
    ],
)
async def test_web_image_generation_delivery(
    tmp_path: Path,
    image_options: dict[str, str] | None,
    model_ratio: str | None,
    expected_size: str,
    summary_stop: str,
):
    with pymupdf.open() as document:
        page = document.new_page(width=32, height=32)
        png = page.get_pixmap().tobytes("png")
    upstream_calls: list[dict[str, Any]] = []
    prompt = "画一张校园科技节海报"

    def generate(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/services/aigc/multimodal-generation/generation"
        assert request.headers["Authorization"] == "Bearer mock-key"
        upstream_calls.append(json.loads(request.content))
        image = f"data:image/png;base64,{base64.b64encode(png).decode()}"
        return httpx.Response(200, json={
            "output": {"choices": [{"message": {"content": [{"image": image}]}}]},
        })

    model_args = {"prompt": prompt}
    if model_ratio is not None:
        model_args["aspect_ratio"] = model_ratio
    call = types.FunctionCall(id="generate", name="generate_image", args=model_args)
    model = ScriptedModel([
        [StreamEvent(type="message_done", result=StreamResult(
            contents="", stop_reason="tool_use", usage={},
            function_calls=[call], raw_parts=[types.Part(function_call=call)],
        ))],
        [StreamEvent(type="message_done", result=StreamResult(
            contents="已处理生图请求。", stop_reason=summary_stop, usage={},
        ))],
    ])
    data_dir = tmp_path / "sessions"
    app = create_web_app(
        workspace=tmp_path,
        data_dir=data_dir,
        model_factory=lambda **_: model,
        reviewer_factory=lambda **_: PassThroughTextReviewer(),
        image_client=DashScopeImageClient(
            DashScopeImageConfig(httpx.URL("https://bailian.test"), "mock-key", settings.image_generation_model),
            transport=httpx.MockTransport(generate),
        ),
    )
    accounts = AccountStore(tmp_path)
    accounts.create_account("owner", "test-password-owner", role="admin")
    accounts.create_account("member", "test-password-member", role="user")
    client_options = {
        "transport": httpx.ASGITransport(app=app),
        "base_url": "http://127.0.0.1",
        "headers": {"X-Agent-Request": "1"},
    }
    async with (
        httpx.AsyncClient(**client_options) as client,
        httpx.AsyncClient(**client_options) as member,
    ):
        for actor, name in ((client, "owner"), (member, "member")):
            login = await actor.post("/api/auth/login", json={
                "username": name, "password": f"test-password-{name}",
            })
            assert login.status_code == 200
        tools = (await client.get("/api/tools")).json()["tools"]
        image_tool = next(tool for tool in tools if tool["name"] == "generate_image")
        assert image_tool["requires_approval"] is False
        assert image_tool["read_only"] is False
        session_id = (await client.post("/api/sessions", json={})).json()["session_id"]
        other_id = (await client.post("/api/sessions", json={})).json()["session_id"]
        session_url = f"/api/sessions/{session_id}"
        body = json.dumps({"query": prompt, "image_options": image_options}).encode()
        completed: dict[str, Any] = {}
        headers = [
            (b"content-type", b"application/json"),
            (b"x-agent-request", b"1"),
            (b"cookie", f"agent_service_login={client.cookies['agent_service_login']}".encode()),
        ]

        async def receive() -> Message:
            return {"type": "http.request", "body": body, "more_body": False}

        async def send(message: Message) -> None:
            for event in _parse_sse_frames(message.get("body", b"").decode()):
                assert event["type"] != "tool.approval_required"
                if event["type"] == "tool.requested":
                    assert event["requires_approval"] is False
                    assert event["tool_arguments"].get("aspect_ratio") == (
                        (image_options or {}).get("aspect_ratio", model_ratio)
                    )
                if event["type"] in {"run.completed", "run.failed"}:
                    completed.update(event)
                    image = event["images"][0]
                    response = await client.get(image["preview_url"])
                    assert response.content == png
                    assert response.headers["content-type"] == "image/png"
                    assert response.headers["cache-control"] == "no-store"

        async with asyncio.timeout(10):
            await app(
                {
                    "type": "http",
                    "asgi": {"version": "3.0", "spec_version": "2.4"},
                    "http_version": "1.1",
                    "method": "POST",
                    "scheme": "http",
                    "server": ("127.0.0.1", 80),
                    "client": ("127.0.0.1", 12345),
                    "path": f"{session_url}/runs",
                    "query_string": b"",
                    "headers": headers,
                },
                receive,
                send,
            )
        assert "generate_image" in model.seen_tool_names[0]
        assert completed["reason"] == ("error" if summary_stop == "length" else "completed")
        assert upstream_calls == [{
            "model": settings.image_generation_model,
            "input": {"messages": [{"role": "user", "content": [{"text": prompt}]}]},
            "parameters": {"n": 1, "response_format": "b64_json", "size": expected_size},
        }]
        image = completed["images"][0]
        assert image["width"] == image["height"] == 32
        detail = (await client.get(session_url)).json()
        assert detail["messages"][-1]["images"] == [image]
        download = await client.get(image["download_url"])
        assert download.content == png and "attachment" in download.headers["content-disposition"]
        assert (await client.get(image["preview_url"].replace(session_id, other_id))).status_code == 404
        assert (await member.get(image["preview_url"])).status_code == 404


@pytest.mark.asyncio
async def test_web_reports_missing_presenton_configuration(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("PRESENTON_BASE_URL", raising=False)
    monkeypatch.setenv("PRESENTON_API_KEY", "test-secret-presenton")
    reply = "请在后端配置 Presenton 地址后重启。"
    model = ScriptedModel(
        [[
            StreamEvent(type="message_start"),
            StreamEvent(type="text", text=reply),
            StreamEvent(
                type="message_done",
                result=StreamResult(
                    contents=reply, stop_reason="STOP", usage={},
                    raw_parts=[types.Part.from_text(text=reply)],
                ),
            ),
        ]]
    )
    app = create_web_app(local_mode=True, workspace=tmp_path, model_factory=lambda **_: model)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as client:
        health = await client.get("/api/health")
        assert health.status_code == 200
        assert (await client.get("/health")).json() == health.json()
        presentation = health.json()["presentation_generation"]
        assert presentation["enabled"] is False
        assert presentation["missing_env"] == ["PRESENTON_BASE_URL"]
        assert "test-secret-presenton" not in health.text

        session = (await client.post("/api/sessions", json={})).json()["session_id"]
        response = await client.post(
            f"/api/sessions/{session}/runs",
            json={"query": "帮我生成一份 PPT", "safety_enabled": False},
        )
        assert any(event["type"] == "run.completed" for event in _parse_sse_frames(response.text))
    assert "generate_pptx" not in model.seen_tool_names[0]
    prompt = model.seen_system_prompts[0]
    assert "PRESENTON_BASE_URL" in prompt
    assert "请勿为替代这项内置功能而调用 shell" in prompt
    assert "test-secret-presenton" not in prompt


@pytest.mark.asyncio
async def test_providers_expose_model_effort_visuals(tmp_path: Path):
    app = create_web_app(local_mode=True, workspace=tmp_path)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
        response = await client.get("/api/providers")
    assert response.status_code == 200
    models = {
        model["id"]: model
        for provider in response.json()["providers"]
        for model in provider["models"]
    }
    assert models["gemini-4-hydrogen"]["effort_visual"] == {
        "level": "xhigh",
        "effect": "glint",
        "duration_ms": 940,
    }
    assert models["gemini-4-neon"]["effort_visual"] == models["gemini-4-hydrogen"]["effort_visual"]
    assert models["gemini-4-carbon"]["effort_visual"] is None
    assert models["deepseek-flash"]["effort_visual"] == {
        "level": "max",
        "effect": "align",
        "duration_ms": 900,
    }
    assert models["deepseek-v4-pro"]["effort_visual"] == models["deepseek-flash"]["effort_visual"]
    assert models["kimi-k3.1-pro"]["effort_visual"] == {
        "level": "max",
        "effect": "double_arc",
        "duration_ms": 880,
    }
    assert models["kimi-k3.1-pro"]["default_thinking_level"] == "max"
    assert models["kimi-k3.1-pro-highspeed"]["effort_visual"] == models["kimi-k3.1-pro"]["effort_visual"]
    assert models["kimi-k3.1-pro-highspeed"]["default_thinking_level"] == "max"
    assert models["glm-5.3"]["effort_visual"] == {
        "level": "max",
        "effect": "circuit",
        "duration_ms": 920,
    }
    assert models["glm-5.4"]["effort_visual"] == models["glm-5.3"]["effort_visual"]
    assert models["qwen4-max"]["effort_visual"] == {
        "level": "xhigh",
        "effect": "lattice",
        "duration_ms": 920,
    }
    assert models["qwen3.8-flash"]["effort_visual"] == models["qwen4-max"]["effort_visual"]
    assert models["gpt-5.4"]["effort_visual"] == {
        "level": "xhigh",
        "effect": "bloom",
        "duration_ms": 920,
    }


@pytest.mark.asyncio
async def test_web_chatgpt_oauth_login_dynamic_models_and_logout(tmp_path: Path):
    import base64
    from urllib.parse import parse_qs, urlparse

    from agent_service.adapters.openai_responses.auth import ChatGPTAuthManager

    jwt_body = (
        base64.urlsafe_b64encode(
            json.dumps({"email": "plus@example.com", "name": "Plus User"}).encode("utf-8")
        )
        .decode("ascii")
        .rstrip("=")
    )
    fake_id_token = f"eyJhbGciOiJSUzI1NiJ9.{jwt_body}.sig"

    def fake_oauth_transport(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/accounts/oauth/token":
            return httpx.Response(
                200,
                json={
                    "access_token": "live_oauth_token",
                    "refresh_token": "live_refresh_token",
                    "id_token": fake_id_token,
                    "expires_in": 3600,
                    "client_id": "oaiapp_web_999",
                },
            )
        if request.url.path == "/v1/models":
            return httpx.Response(
                200,
                json={
                    "models": [
                        {
                            "slug": "gpt-6.0-plus",
                            "display_name": "GPT-6.0 Plus",
                            "visibility": "list",
                            "input_modalities": ["text", "image"],
                            "supported_reasoning_levels": [
                                {"effort": "low"},
                                {"effort": "high"},
                                {"effort": "xhigh"},
                            ],
                            "default_reasoning_level": "high",
                        }
                    ]
                },
            )
        raise AssertionError(f"unexpected URL: {request.url}")

    chatgpt_auth = ChatGPTAuthManager(
        tmp_path / "auth" / "chatgpt.json",
        transport=httpx.MockTransport(fake_oauth_transport),
    )
    captured_factory_kwargs: list[dict[str, Any]] = []

    def fake_model_factory(**kwargs: Any) -> ScriptedModel:
        captured_factory_kwargs.append(dict(kwargs))
        return ScriptedModel(
            [
                [
                    StreamEvent(type="message_start"),
                    StreamEvent(type="text", text="来自 ChatGPT 套餐额度的回复"),
                    StreamEvent(
                        type="message_done",
                        result=StreamResult(
                            contents="来自 ChatGPT 套餐额度的回复",
                            stop_reason="stop",
                            usage={"input_len": 15, "output_len": 8},
                            raw_parts=[
                                types.Part.from_text(text="来自 ChatGPT 套餐额度的回复")
                            ],
                        ),
                    ),
                ]
            ]
        )

    app = create_web_app(
        local_mode=True,
        workspace=tmp_path,
        data_dir=tmp_path / "sessions",
        chatgpt_auth=chatgpt_auth,
        model_factory=fake_model_factory,
        reviewer_factory=lambda **_: PassThroughTextReviewer(),
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://127.0.0.1:8000"
    ) as client:
        status0 = (await client.get("/api/auth/chatgpt/status")).json()
        assert status0["connected"] is False

        start = (await client.post("/api/auth/chatgpt/start")).json()
        qs = parse_qs(urlparse(start["authorize_url"]).query)
        state = qs["state"][0]

        cb = await client.get(
            "/api/auth/chatgpt/callback",
            params={
                "code": "cb_code_1",
                "state": state,
                "client_id": "oaiapp_web_999",
            },
        )
        assert cb.status_code == 200
        assert "chatgpt-oauth-complete" in cb.text
        assert "plus@example.com" in cb.text

        status1 = (await client.get("/api/auth/chatgpt/status")).json()
        assert status1["connected"] is True
        assert status1["account_email"] == "plus@example.com"
        assert "access_token" not in status1

        prov = (await client.get("/api/providers", params={"refresh_models": "true"})).json()
        openai_p = next(p for p in prov["providers"] if p["id"] == "openai")
        assert openai_p["oauth_connected"] is True
        assert openai_p["oauth_account"] == "plus@example.com"
        assert [m["id"] for m in openai_p["models"]] == ["gpt-6.0-plus"]
        assert openai_p["default_model"] == "gpt-6.0-plus"

        sess = (
            await client.post(
                "/api/sessions",
                json={"provider": "openai", "model_name": "gpt-6.0-plus"},
            )
        ).json()
        run_resp = await client.post(
            f"/api/sessions/{sess['session_id']}/runs",
            json={
                "query": "你好 GPT",
                "provider": "openai",
                "model_name": "gpt-6.0-plus",
                "reasoning_effort": "xhigh",
                "safety_enabled": False,
            },
        )
        assert run_resp.status_code == 200
        assert captured_factory_kwargs[0]["provider"] == "openai"
        assert captured_factory_kwargs[0]["model_name"] == "gpt-6.0-plus"
        assert captured_factory_kwargs[0]["chatgpt_auth"] is chatgpt_auth

        logged_out = (await client.post("/api/auth/chatgpt/logout")).json()
        assert logged_out["logged_out"] is True
        assert logged_out["connected"] is False


@pytest.mark.asyncio
async def test_providers_models_thinking_levels_and_unified_manager_tools(tmp_path: Path):
    created_models: list[ScriptedModel] = []
    factory_kwargs: list[dict[str, Any]] = []

    def fake_model_factory(**kwargs: Any) -> ScriptedModel:
        factory_kwargs.append(dict(kwargs))
        m = ScriptedModel(
            [
                [
                    StreamEvent(type="message_start"),
                    StreamEvent(type="thinking", text="先思考一下"),
                    StreamEvent(type="text", text="你好，这是回复"),
                    StreamEvent(
                        type="message_done",
                        result=StreamResult(
                            contents="你好，这是回复",
                            stop_reason="STOP",
                            usage={"input_len": 10, "output_len": 5},
                            raw_parts=[
                                types.Part(text="先思考一下", thought=True),
                                types.Part.from_text(text="你好，这是回复"),
                            ],
                        ),
                    ),
                ]
            ]
        )
        created_models.append(m)
        return m

    data_dir = tmp_path / "sessions"
    data_dir.mkdir()
    # 放入一份损坏的会话文件，验证不会影响正常会话列表
    (data_dir / "broken_session.json").write_text("{corrupted", encoding="utf-8")

    app = create_web_app(
        local_mode=True,
        workspace=tmp_path,
        data_dir=data_dir,
        model_factory=fake_model_factory,
        reviewer_factory=lambda **_: PassThroughTextReviewer(),
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://127.0.0.1"
    ) as client:
        prov_resp = await client.get("/api/providers")
        assert prov_resp.status_code == 200
        providers_by_id = {p["id"]: p for p in prov_resp.json()["providers"]}
        assert {"gemini", "deepseek", "kimi", "glm", "qwen", "openai"}.issubset(
            providers_by_id
        )

        gemini_models = {m["id"]: m for m in providers_by_id["gemini"]["models"]}
        assert list(gemini_models.keys()) == [
            "gemini-4-hydrogen",
            "gemini-4-neon",
            "gemini-4-carbon",
        ]
        assert providers_by_id["gemini"]["default_model"] == "gemini-4-hydrogen"
        assert gemini_models["gemini-4-hydrogen"]["thinking_levels"] == [
            "low",
            "medium",
            "high",
            "xhigh",
        ]
        assert gemini_models["gemini-4-carbon"]["thinking_levels"] == [
            "low",
            "medium",
            "high",
        ]
        assert gemini_models["gemini-4-hydrogen"]["max_output_tokens"] == 65536
        deepseek_models = {m["id"]: m for m in providers_by_id["deepseek"]["models"]}
        assert deepseek_models["deepseek-flash"]["thinking_levels"] == [
            "off",
            "low",
            "high",
            "max",
        ]
        assert deepseek_models["deepseek-flash"]["max_output_tokens"] == 393216
        kimi_models = {m["id"]: m for m in providers_by_id["kimi"]["models"]}
        assert kimi_models["kimi-k3.1-pro"]["thinking_levels"] == [
            "low",
            "high",
            "max",
        ]
        assert kimi_models["kimi-k3.1-pro"]["max_output_tokens"] == 131072
        glm_models = {m["id"]: m for m in providers_by_id["glm"]["models"]}
        assert list(glm_models.keys()) == [
            "glm-5.3",
            "glm-5.3-flash",
            "glm-5.3-flashx",
            "glm-5.4",
            "glm-4.7-flash",
        ]
        assert glm_models["glm-5.3"]["thinking_levels"] == ["low", "high", "max"]
        assert glm_models["glm-5.3"]["max_output_tokens"] == 131072
        assert glm_models["glm-5.3"]["supports_vision"] is False
        assert glm_models["glm-5.3-flash"]["supports_vision"] is True
        assert glm_models["glm-4.7-flash"]["thinking_levels"] == ["off", "high"]
        qwen_models = {m["id"]: m for m in providers_by_id["qwen"]["models"]}
        assert list(qwen_models.keys()) == [
            "qwen4-max",
            "qwen3.8-max",
            "qwen3.7-plus",
            "qwen3.8-flash",
        ]
        assert qwen_models["qwen4-max"]["thinking_levels"] == [
            "off",
            "low",
            "medium",
            "high",
            "xhigh",
        ]
        assert qwen_models["qwen4-max"]["max_output_tokens"] == 262144
        assert qwen_models["qwen3.8-max"]["max_output_tokens"] == 131072
        assert qwen_models["qwen3.7-plus"]["max_output_tokens"] == 131072
        assert qwen_models["qwen3.8-flash"]["max_output_tokens"] == 131072

        # 1. 创建会话并运行：Manager 默认统一开放 subagent 及所有工作区工具，不再人为隔离单/多模式
        s1 = (
            await client.post(
                "/api/sessions",
                json={
                    "title": "统一 Manager 测试",
                    "provider": "gemini",
                    "model_name": "gemini-4-carbon",
                },
            )
        ).json()
        sid1 = s1["session_id"]

        run_resp = await client.post(
            f"/api/sessions/{sid1}/runs",
            json={
                "query": "你好",
                "provider": "gemini",
                "model_name": "gemini-4-carbon",
                "reasoning_effort": "xhigh",
                "safety_enabled": False,
            },
        )
        assert run_resp.status_code == 200
        frames = _parse_sse_frames(run_resp.text)
        types_seen = [f["type"] for f in frames]
        assert "thinking.delta" in types_seen
        assert "text.delta" in types_seen
        assert "run.completed" in types_seen
        assert "subagent" in created_models[0].seen_tool_names[0]
        assert "update_plan" in created_models[0].seen_tool_names[0]
        # gemini-4-carbon 不支持 xhigh，后端自动平滑校准为 high，并按模型上限 65536 自动顶格
        assert factory_kwargs[0]["reasoning_effort"] == "high"
        assert factory_kwargs[0]["thinking_enabled"] is True
        assert created_models[0].seen_max_tokens[0] == 65536

        # 刷新会话详情验证持久化完整恢复
        detail = (await client.get(f"/api/sessions/{sid1}")).json()
        assert len(detail["messages"]) == 2
        assert detail["messages"][1]["thinking"] == "先思考一下"
        assert detail["messages"][1]["text"] == "你好，这是回复"

        # 2. DeepSeek 开启 max 档位时自动顶格 393216；关闭 off 时回退安全 8192
        run_resp2 = await client.post(
            f"/api/sessions/{sid1}/runs",
            json={
                "query": "深度推理",
                "provider": "deepseek",
                "model_name": "deepseek-flash",
                "reasoning_effort": "max",
                "safety_enabled": False,
            },
        )
        assert run_resp2.status_code == 200
        assert "subagent" in created_models[1].seen_tool_names[0]
        assert factory_kwargs[1]["thinking_enabled"] is True
        assert factory_kwargs[1]["reasoning_effort"] == "max"
        assert created_models[1].seen_max_tokens[0] == 393216

        run_resp3 = await client.post(
            f"/api/sessions/{sid1}/runs",
            json={
                "query": "再聊一句",
                "provider": "deepseek",
                "model_name": "deepseek-flash",
                "reasoning_effort": "off",
                "safety_enabled": False,
            },
        )
        assert run_resp3.status_code == 200
        assert factory_kwargs[2]["thinking_enabled"] is False
        assert factory_kwargs[2]["reasoning_effort"] == "off"
        assert created_models[2].seen_max_tokens[0] == 8192

        # 3. GLM 强制思考：传入 off 自动校准为 low，输出上限顶格 131072
        run_resp4 = await client.post(
            f"/api/sessions/{sid1}/runs",
            json={
                "query": "智谱测试",
                "provider": "glm",
                "model_name": "glm-5.3",
                "thinking_enabled": False,
                "reasoning_effort": "off",
                "safety_enabled": False,
            },
        )
        assert run_resp4.status_code == 200
        assert factory_kwargs[3]["provider"] == "glm"
        assert factory_kwargs[3]["thinking_enabled"] is True
        assert factory_kwargs[3]["reasoning_effort"] == "low"
        assert created_models[3].seen_max_tokens[0] == 131072

        # 4. Qwen 混合思考：xhigh 顶格 262144，关闭思考回退非思考上限 131072
        run_resp5 = await client.post(
            f"/api/sessions/{sid1}/runs",
            json={
                "query": "千问深度思考",
                "provider": "qwen",
                "model_name": "qwen4-max",
                "reasoning_effort": "xhigh",
                "safety_enabled": False,
            },
        )
        assert run_resp5.status_code == 200
        assert factory_kwargs[4]["provider"] == "qwen"
        assert factory_kwargs[4]["thinking_enabled"] is True
        assert factory_kwargs[4]["reasoning_effort"] == "xhigh"
        assert created_models[4].seen_max_tokens[0] == 262144

        run_resp6 = await client.post(
            f"/api/sessions/{sid1}/runs",
            json={
                "query": "千问快速直出",
                "provider": "qwen",
                "model_name": "qwen4-max",
                "thinking_enabled": False,
                "reasoning_effort": "off",
                "safety_enabled": False,
            },
        )
        assert run_resp6.status_code == 200
        assert factory_kwargs[5]["provider"] == "qwen"
        assert factory_kwargs[5]["thinking_enabled"] is False
        assert factory_kwargs[5]["reasoning_effort"] == "off"
        assert created_models[5].seen_max_tokens[0] == 131072


@pytest.mark.asyncio
async def test_workspace_switching_browse_validate_and_file_attachments(tmp_path: Path):
    default_ws = tmp_path / "default_ws"
    alt_ws = tmp_path / "alt_project"
    default_ws.mkdir()
    alt_ws.mkdir()
    (alt_ws / "hello.txt").write_text("from alt workspace", encoding="utf-8")
    document = Document()
    document.add_paragraph("表格前的正文")
    document.add_table(rows=1, cols=1).cell(0, 0).text = "表格中的采购编号"
    document.add_paragraph("表格后的正文")
    word_bytes = io.BytesIO()
    document.save(word_bytes)

    workbook = Workbook()
    sheet = workbook.create_sheet("课程", 0)
    sheet["B3"] = "数据库"
    sheet["C3"] = "=1+3"
    summary_sheet = workbook.create_sheet("汇总")
    summary_sheet["A1"] = 42
    hidden = workbook.create_sheet("隐藏工作表")
    hidden.sheet_state = "hidden"
    hidden["A1"] = "内部字段不应进入上下文"
    excel_bytes = io.BytesIO()
    workbook.save(excel_bytes)
    workbook.close()

    captured_contents: list[list[types.Content]] = []

    class InspectingModel:
        def __init__(self) -> None:
            self._step = 0

        async def stream(
            self,
            *,
            contents: list[types.Content],
            system_prompt: str,
            max_tokens: int,
            tools: list[types.Tool] | None = None,
        ) -> AsyncGenerator[StreamEvent]:
            _ = (system_prompt, max_tokens, tools)
            captured_contents.append(list(contents))
            self._step += 1
            if self._step == 1:
                fc = types.FunctionCall(
                    id="call_read_1",
                    name="read_file",
                    args={"path": "hello.txt"},
                )
                yield StreamEvent(type="message_start")
                yield StreamEvent(type="tool_use_start", id="call_read_1", name="read_file")
                yield StreamEvent(
                    type="message_done",
                    result=StreamResult(
                        contents="",
                        stop_reason="tool_use",
                        usage={"input_len": 15, "output_len": 6},
                        function_calls=[fc],
                        raw_parts=[types.Part(function_call=fc)],
                    ),
                )
            else:
                yield StreamEvent(type="message_start")
                yield StreamEvent(type="text", text="已读取 alt_project 中的文件")
                yield StreamEvent(
                    type="message_done",
                    result=StreamResult(
                        contents="已读取 alt_project 中的文件",
                        stop_reason="STOP",
                        usage={"input_len": 25, "output_len": 12},
                        raw_parts=[types.Part.from_text(text="已读取 alt_project 中的文件")],
                    ),
                )

        async def close(self) -> None:
            return None

    app = create_web_app(
        local_mode=True,
        workspace=default_ws,
        data_dir=tmp_path / "sessions",
        model_factory=lambda **_: InspectingModel(),
        reviewer_factory=lambda **_: PassThroughTextReviewer(),
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://127.0.0.1"
    ) as client:
        browse_resp = await client.get(
            "/api/workspace/browse", params={"path": str(tmp_path)}
        )
        assert browse_resp.status_code == 200
        dir_names = [d["name"] for d in browse_resp.json()["directories"]]
        assert "alt_project" in dir_names

        val_resp = await client.post(
            "/api/workspace/validate", json={"path": str(alt_ws)}
        )
        assert val_resp.status_code == 200
        assert val_resp.json()["name"] == "alt_project"

        bad_resp = await client.post(
            "/api/workspace/validate", json={"path": str(tmp_path / "missing_dir")}
        )
        assert bad_resp.status_code == 400

        sess = (
            await client.post(
                "/api/sessions",
                json={
                    "title": "切换工作区测试",
                    "provider": "gemini",
                    "model_name": "gemini-4-hydrogen",
                    "workspace": str(alt_ws),
                },
            )
        ).json()
        sid = sess["session_id"]
        assert Path(sess["workspace"]) == alt_ws.resolve()

        run_resp = await client.post(
            f"/api/sessions/{sid}/runs",
            json={
                "query": "读取 hello.txt 并结合附件分析",
                "provider": "gemini",
                "model_name": "gemini-4-hydrogen",
                "workspace": str(alt_ws),
                "safety_enabled": False,
                "attachments": [
                    {
                        "name": "spec.md",
                        "mime_type": "text/markdown",
                        "kind": "file",
                        "text_content": "# 需求说明\n支持工作区切换",
                    },
                    {
                        "name": "采购.DOCX",
                        "mime_type": "application/octet-stream",
                        "kind": "file",
                        "data_base64": base64.b64encode(word_bytes.getvalue()).decode("ascii"),
                    },
                    {
                        "name": "课程.xlsx",
                        "kind": "file",
                        "data_base64": base64.b64encode(excel_bytes.getvalue()).decode("ascii"),
                    },
                ],
            },
        )
        assert run_resp.status_code == 200
        frames = _parse_sse_frames(run_resp.text)
        tool_done = [f for f in frames if f.get("type") == "tool.completed"]
        assert len(tool_done) == 1
        assert "from alt workspace" in tool_done[0]["result"]
        attachment_text = next(
            p.text
            for c in captured_contents[0]
            for p in (c.parts or [])
            if p.text and '<attached-file name="spec.md"' in p.text
        )
        assert attachment_text.index("表格前的正文") < attachment_text.index("表格中的采购编号")
        assert attachment_text.index("表格中的采购编号") < attachment_text.index("表格后的正文")
        assert "[工作表：课程]" in attachment_text
        assert "B3 = 数据库" in attachment_text
        assert "C3 = =1+3（无缓存值）" in attachment_text
        assert "[工作表：汇总]" in attachment_text
        assert "A1 = 42" in attachment_text
        assert "内部字段不应进入上下文" not in attachment_text


@pytest.mark.asyncio
async def test_deepseek_receives_word_attachment_with_current_question_after_compression(tmp_path: Path):
    data_dir = tmp_path / "sessions"
    provider = settings.providers["deepseek"]
    model_name = provider.default_model
    store = SessionStore(data_dir)
    session = store.create(provider=provider.id, model_name=model_name)
    session.history = serialize_contents(
        [
            types.Content(role=role, parts=[types.Part.from_text(text=f"旧对话 {index} {role}")])
            for index in range(5)
            for role in ("user", "model")
        ]
    )
    store.save(session)

    question = "这个文件是什么"
    document_text = "本实验报告记录了稀疏矩阵转置和栈的实现。"
    document = Document()
    document.add_paragraph(document_text)
    document_bytes = io.BytesIO()
    document.save(document_bytes)

    upstream_requests: list[list[dict[str, Any]]] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        messages = json.loads(request.content)["messages"]
        upstream_requests.append(messages)
        answer = "旧对话已压缩。" if len(upstream_requests) == 1 else "这是数据结构实验报告。"
        event = json.dumps({
            "id": "chatcmpl-test",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": model_name,
            "choices": [{"index": 0, "delta": {"content": answer}, "finish_reason": "stop"}],
        })
        return httpx2.Response(
            200, headers={"content-type": "text/event-stream"}, text=f"data: {event}\n\ndata: [DONE]\n\n"
        )

    async with AsyncOpenAI(
        api_key="test-key",
        base_url="https://deepseek.test/v1",
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(respond)),
    ) as upstream:
        app = create_web_app(
            local_mode=True,
            workspace=tmp_path,
            data_dir=data_dir,
            model_factory=lambda **_: OpenAICompatibleModel(provider, client=upstream),
            reviewer_factory=lambda **_: PassThroughTextReviewer(),
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
        ) as client:
            response = await client.post(
                f"/api/sessions/{session.session_id}/runs",
                json={
                    "query": question,
                    "safety_enabled": False,
                    "attachments": [{
                        "name": "矩阵报告.docx",
                        "kind": "file",
                        "data_base64": base64.b64encode(document_bytes.getvalue()).decode("ascii"),
                    }],
                },
            )

    assert response.status_code == 200
    assert any(event["type"] == "run.completed" for event in _parse_sse_frames(response.text))
    summary_request, answer_request = upstream_requests
    current_message = answer_request[-1]
    assert current_message["role"] == "user"
    assert question in current_message["content"]
    assert document_text in current_message["content"]
    assert 'name="矩阵报告.docx"' in current_message["content"]
    assert document_text not in summary_request[-1]["content"]


@pytest.mark.asyncio
async def test_shared_workbench_executes_weather_currency_and_route_in_one_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.delenv("QWEATHER_API_KEY", raising=False)
    monkeypatch.setenv("OPEN_METEO_ENABLED", "1")
    monkeypatch.setenv("AMAP_API_KEY", "amap-test-key")

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.open-meteo.com":
            return httpx.Response(200, json={
                "timezone": "Asia/Shanghai",
                "current": {"time": "2026-11-28T11:00", "temperature_2m": 8, "weather_code": 0},
                "daily": {"time": ["2026-11-28"], "temperature_2m_max": [10], "temperature_2m_min": [2]},
            })
        if request.url.host == "api.frankfurter.dev":
            return httpx.Response(200, json={"date": "2026-11-27", "base": "USD", "quote": "CNY", "rate": 7.1})
        assert request.url.host == "restapi.amap.com"
        if request.url.path == "/v5/place/text":
            name = request.url.params["keywords"]
            coordinate = "116.397,39.918" if name == "故宫" else "116.389,39.925"
            return httpx.Response(200, json={"status": "1", "pois": [{"name": name, "location": coordinate}]})
        assert request.url.path == "/v5/direction/walking"
        assert request.url.params["origin"] == "116.397000,39.918000"
        assert request.url.params["destination"] == "116.389000,39.925000"
        return httpx.Response(200, json={"status": "1", "route": {
            "paths": [{"distance": "1400", "cost": {"duration": "1140"}}],
        }})

    transport = httpx.MockTransport(respond)
    monkeypatch.setattr(tool_index, "GetWeatherTool", lambda **kw: GetWeatherTool(transport=transport, **kw))
    monkeypatch.setattr(tool_index, "GetExchangeRateTool", lambda: GetExchangeRateTool(transport))
    monkeypatch.setattr(tool_index, "MapsService", lambda: MapsService(transport))

    def call_turn(*calls: types.FunctionCall) -> list[StreamEvent]:
        return [
            StreamEvent(type="message_start"),
            StreamEvent(type="message_done", result=StreamResult(
                contents="", stop_reason="tool_use", usage={}, function_calls=list(calls),
                raw_parts=[types.Part(function_call=call) for call in calls],
            )),
        ]

    model = ScriptedModel([
        call_turn(
            types.FunctionCall(id="weather", name="get_weather", args={
                "latitude": 39.9, "longitude": 116.4, "days": 1, "hours": 0, "include_air_quality": False,
            }),
            types.FunctionCall(id="rate", name="get_exchange_rate", args={"base": "USD", "quote": "CNY"}),
            types.FunctionCall(id="first-place", name="search_places", args={"keyword": "故宫", "city": "北京市"}),
            types.FunctionCall(id="second-place", name="search_places", args={"keyword": "北海公园", "city": "北京市"}),
        ),
        call_turn(
            types.FunctionCall(id="conversion", name="calculate", args={
                "operation": "convert_currency", "from_unit": "USD", "to_unit": "CNY", "rate": "7.1", "value": "100",
            }),
            types.FunctionCall(id="route", name="plan_route", args={
                "mode": "walking", "coordinate_system": "GCJ-02", "origin": "116.397,39.918",
                "destination": "116.389,39.925",
            }),
        ),
        [
            StreamEvent(type="message_start"),
            StreamEvent(type="message_done", result=StreamResult(
                contents="已查询旅行信息", stop_reason="STOP", usage={},
                raw_parts=[types.Part.from_text(text="已查询旅行信息")],
            )),
        ],
    ])
    data_dir = tmp_path / "sessions"
    AccountStore(tmp_path).create_account("traveler", "test-password-traveler", role="admin")
    app = create_web_app(
        local_mode=False, workspace=tmp_path, data_dir=data_dir, model_factory=lambda **_: model,
        reviewer_factory=lambda **_: PassThroughTextReviewer(),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1", headers={"X-Agent-Request": "1"},
    ) as client:
        login = await client.post(
            "/api/auth/login", json={"username": "traveler", "password": "test-password-traveler"},
        )
        assert login.status_code == 200
        tools = (await client.get("/api/tools")).json()["tools"]
        travel_tools = {"get_weather", "get_exchange_rate", "search_places", "plan_route"}
        assert travel_tools <= {tool["name"] for tool in tools if not tool["requires_approval"]}
        session = (await client.post("/api/sessions", json={})).json()["session_id"]
        response = await client.post(
            f"/api/sessions/{session}/runs", json={
                "query": "查北京天气，算100美元可换多少人民币，再查故宫到北海公园的步行路线。",
                "safety_enabled": False,
            },
        )

    assert response.status_code == 200
    events = _parse_sse_frames(response.text)
    assert travel_tools <= model.seen_tool_names[0]
    assert any(event["type"] == "run.completed" for event in events)
    completed = {item["tool_call_id"]: json.loads(item["result"]) for item in events if item["type"] == "tool.completed"}
    assert completed["weather"]["current"]["source"] == "Open-Meteo"
    assert completed["rate"]["observed_on"] == "2026-11-27"
    assert completed["first-place"]["places"][0]["name"] == "故宫"
    assert completed["second-place"]["places"][0]["name"] == "北海公园"
    assert completed["conversion"]["result"] == "710"
    assert completed["route"]["duration_s"] == 1140


@pytest.mark.asyncio
async def test_web_sse_high_risk_tool_approval_and_hash_guard(tmp_path: Path):
    def fake_model_factory(**kwargs: Any) -> ScriptedModel:
        _ = kwargs
        fc = types.FunctionCall(
            id="call_write_1",
            name="write_file",
            args={"path": "demo.txt", "content": "hello from web"},
        )
        return ScriptedModel(
            [
                [
                    StreamEvent(type="message_start"),
                    StreamEvent(type="tool_use_start", id="call_write_1", name="write_file"),
                    StreamEvent(
                        type="message_done",
                        result=StreamResult(
                            contents="",
                            stop_reason="tool_use",
                            usage={"input_len": 20, "output_len": 10},
                            function_calls=[fc],
                            raw_parts=[types.Part(function_call=fc)],
                        ),
                    ),
                ],
                [
                    StreamEvent(type="message_start"),
                    StreamEvent(type="text", text="文件已写入。"),
                    StreamEvent(
                        type="message_done",
                        result=StreamResult(
                            contents="文件已写入。",
                            stop_reason="STOP",
                            usage={"input_len": 30, "output_len": 10},
                            raw_parts=[types.Part.from_text(text="文件已写入。")],
                        ),
                    ),
                ],
            ]
        )

    app = create_web_app(
        local_mode=True,
        workspace=tmp_path,
        data_dir=tmp_path / "sessions",
        model_factory=fake_model_factory,
        reviewer_factory=lambda **_: PassThroughTextReviewer(),
    )

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    server_task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.02)

    try:
        async with httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{port}", timeout=10.0
        ) as client:
            sess = (
                await client.post(
                    "/api/sessions",
                    json={"title": "审批测试", "mode": "single", "provider": "deepseek"},
                )
            ).json()
            sid = sess["session_id"]

            seen_events: list[dict[str, Any]] = []

            async def run_stream():
                async with client.stream(
                    "POST",
                    f"/api/sessions/{sid}/runs",
                    json={
                        "query": "写入 demo.txt",
                        "provider": "deepseek",
                        "mode": "single",
                        "safety_enabled": False,
                        "plan_and_execute": False,
                    },
                ) as response:
                    assert response.status_code == 200
                    buffer = ""
                    async for chunk in response.aiter_text():
                        buffer += chunk
                        while "\n\n" in buffer:
                            block, buffer = buffer.split("\n\n", 1)
                            for frame in _parse_sse_frames(block):
                                seen_events.append(frame)
                                if frame["type"] == "tool.approval_required":
                                    approval_id = frame["approval_id"]
                                    req_hash = frame["request_hash"]
                                    # 篡改指纹必须被拒绝 (409)
                                    bad = await client.post(
                                        f"/api/sessions/{sid}/approvals/{approval_id}",
                                        json={
                                            "approved": True,
                                            "request_hash": "tampered_hash",
                                        },
                                    )
                                    assert bad.status_code == 409
                                    # 正确指纹放行执行
                                    ok = await client.post(
                                        f"/api/sessions/{sid}/approvals/{approval_id}",
                                        json={
                                            "approved": True,
                                            "request_hash": req_hash,
                                        },
                                    )
                                    assert ok.status_code == 200

            await asyncio.wait_for(run_stream(), timeout=10.0)

            assert (tmp_path / "demo.txt").read_text(encoding="utf-8") == "hello from web"
            event_types = [ev["type"] for ev in seen_events]
            assert "tool.approval_required" in event_types
            assert "tool.completed" in event_types
            assert "run.completed" in event_types
    finally:
        server.should_exit = True
        await server_task


@pytest.mark.asyncio
async def test_web_stop_run_releases_session_immediately_for_next_message(
    tmp_path: Path,
):
    call_count = 0
    first_started = asyncio.Event()
    never_finishes = asyncio.Event()

    class BlockingThenFastModel:
        def __init__(self, index: int) -> None:
            self.index = index

        async def stream(
            self,
            *,
            contents: list[types.Content],
            system_prompt: str,
            max_tokens: int,
            tools: list[types.Tool] | None = None,
        ) -> AsyncGenerator[StreamEvent]:
            _ = (contents, system_prompt, max_tokens, tools)
            yield StreamEvent(type="message_start")
            if self.index == 1:
                first_started.set()
                await never_finishes.wait()
            yield StreamEvent(type="text", text="第二条消息正常回复")
            yield StreamEvent(
                type="message_done",
                result=StreamResult(
                    contents="第二条消息正常回复",
                    stop_reason="STOP",
                    usage={"input_len": 12, "output_len": 8},
                    raw_parts=[types.Part.from_text(text="第二条消息正常回复")],
                ),
            )

        async def close(self) -> None:
            return None

    def fake_model_factory(**kwargs: Any) -> BlockingThenFastModel:
        nonlocal call_count
        _ = kwargs
        call_count += 1
        return BlockingThenFastModel(call_count)

    app = create_web_app(
        local_mode=True,
        workspace=tmp_path,
        data_dir=tmp_path / "sessions",
        model_factory=fake_model_factory,
        reviewer_factory=lambda **_: PassThroughTextReviewer(),
    )

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    server_task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.02)

    try:
        async with httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{port}", timeout=10.0
        ) as client:
            sess = (
                await client.post(
                    "/api/sessions",
                    json={"title": "停止回归测试", "mode": "single", "provider": "deepseek"},
                )
            ).json()
            sid = sess["session_id"]

            async def run_first():
                async with client.stream(
                    "POST",
                    f"/api/sessions/{sid}/runs",
                    json={
                        "query": "我叫做留些",
                        "provider": "deepseek",
                        "mode": "single",
                        "safety_enabled": False,
                        "plan_and_execute": False,
                    },
                ) as resp:
                    async for _ in resp.aiter_text():
                        pass

            first_task = asyncio.create_task(run_first())
            await asyncio.wait_for(first_started.wait(), timeout=3.0)

            # 模拟前端点击终止：请求 /stop，必须立刻取消后台任务并释放会话锁
            stop_resp = await asyncio.wait_for(
                client.post(f"/api/sessions/{sid}/stop"), timeout=3.0
            )
            assert stop_resp.status_code == 200
            assert stop_resp.json() == {"stopped": True, "status": "stopped"}
            await asyncio.wait_for(first_task, timeout=3.0)

            # 终止后立刻发送第二条消息，绝不能报“当前会话已有任务正在运行”
            second_resp = await client.post(
                f"/api/sessions/{sid}/runs",
                json={
                    "query": "我的名字是ryuke",
                    "provider": "deepseek",
                    "mode": "single",
                    "safety_enabled": False,
                    "plan_and_execute": False,
                },
            )
            assert second_resp.status_code == 200
            frames = _parse_sse_frames(second_resp.text)
            assert any(f.get("type") == "run.completed" for f in frames)
            assert any(
                f.get("type") == "text.delta" and "第二条消息正常回复" in str(f.get("text"))
                for f in frames
            )
    finally:
        server.should_exit = True
        await server_task


@pytest.mark.asyncio
async def test_length_truncated_response_surfaces_explicit_error(tmp_path: Path):
    def fake_model_factory(**kwargs: Any) -> ScriptedModel:
        _ = kwargs
        return ScriptedModel(
            [
                [
                    StreamEvent(type="message_start"),
                    StreamEvent(type="thinking", text="非常长的思考过程..."),
                    StreamEvent(
                        type="message_done",
                        result=StreamResult(
                            contents="",
                            stop_reason="length",
                            usage={"input_len": 1000, "output_len": 4096},
                            raw_parts=[types.Part(text="非常长的思考过程...", thought=True)],
                        ),
                    ),
                ]
            ]
        )

    app = create_web_app(
        local_mode=True,
        workspace=tmp_path,
        data_dir=tmp_path / "sessions",
        model_factory=fake_model_factory,
        reviewer_factory=lambda **_: PassThroughTextReviewer(),
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://127.0.0.1"
    ) as client:
        sess = (
            await client.post(
                "/api/sessions",
                json={"title": "截断告警测试", "provider": "deepseek"},
            )
        ).json()
        resp = await client.post(
            f"/api/sessions/{sess['session_id']}/runs",
            json={
                "query": "写一个贪吃蛇",
                "provider": "deepseek",
                "max_tokens": 4096,
                "safety_enabled": False,
            },
        )
        assert resp.status_code == 200
        frames = _parse_sse_frames(resp.text)
        failed = [f for f in frames if f.get("type") == "run.failed"]
        assert len(failed) == 1
        assert "最大 Token 上限" in str(failed[0].get("error", ""))


@pytest.mark.asyncio
async def test_knowledge_base_pdf_upload_search_and_opt_in_agent_reference(
    tmp_path: Path,
):
    import pymupdf

    class FakeEmbeddingProvider:
        provider_id = "fake"
        model_name = "fake-embed"
        dimensions: int = 4

        def _vec(self, text: str) -> list[float]:
            lowered = text.lower()
            return [
                1.0 if ("cs302" in lowered or "credit" in lowered) else 0.05,
                1.0 if ("exam" in lowered or "deferred" in lowered) else 0.05,
                1.0 if ("library" in lowered or "hours" in lowered) else 0.05,
                0.1,
            ]

        async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
            return [self._vec(t) for t in texts]

        async def embed_query(self, text: str) -> list[float]:
            return self._vec(text)

        async def close(self) -> None:
            return None

    def make_pdf() -> bytes:
        doc = pymupdf.open()
        try:
            p1 = doc.new_page()
            p1.insert_text(
                (72, 72),
                "Library Hours: Open 07:30 to 22:30 every day.",
                fontsize=12,
            )
            p2 = doc.new_page()
            p2.insert_text(
                (72, 72),
                "Course CS302 Operating Systems carries 4 credits and requires final project.",
                fontsize=12,
            )
            return bytes(doc.tobytes())
        finally:
            doc.close()

    seen_tool_sets: list[set[str]] = []
    seen_contents: list[list[types.Content]] = []

    class KnowledgeAwareModel:
        def __init__(self, call_kb_tool: bool) -> None:
            self._call_kb_tool = call_kb_tool
            self._turn = 0

        async def stream(
            self,
            *,
            contents: list[types.Content],
            system_prompt: str,
            max_tokens: int,
            tools: list[types.Tool] | None = None,
        ) -> AsyncGenerator[StreamEvent]:
            _ = (system_prompt, max_tokens)
            names = {
                decl.name
                for group in (tools or [])
                for decl in (group.function_declarations or [])
                if decl.name
            }
            seen_tool_sets.append(names)
            seen_contents.append(list(contents))
            self._turn += 1

            if self._call_kb_tool and self._turn == 1:
                fc = types.FunctionCall(
                    id="call_kb_1",
                    name="search_knowledge_base",
                    args={"query": "CS302 credits", "top_k": 3},
                )
                yield StreamEvent(type="message_start")
                yield StreamEvent(
                    type="tool_use_start",
                    id="call_kb_1",
                    name="search_knowledge_base",
                )
                yield StreamEvent(
                    type="message_done",
                    result=StreamResult(
                        contents="",
                        stop_reason="tool_use",
                        usage={"input_len": 20, "output_len": 8},
                        function_calls=[fc],
                        raw_parts=[types.Part(function_call=fc)],
                    ),
                )
            else:
                reply = (
                    "根据《school_guide.pdf》第 2 页，CS302 为 4 学分。"
                    if self._call_kb_tool
                    else "普通对话模式回复。"
                )
                yield StreamEvent(type="message_start")
                yield StreamEvent(type="text", text=reply)
                yield StreamEvent(
                    type="message_done",
                    result=StreamResult(
                        contents=reply,
                        stop_reason="STOP",
                        usage={"input_len": 30, "output_len": 15},
                        raw_parts=[types.Part.from_text(text=reply)],
                    ),
                )

        async def close(self) -> None:
            return None

    run_counter = {"n": 0}

    def fake_model_factory(**kwargs: Any) -> KnowledgeAwareModel:
        _ = kwargs
        run_counter["n"] += 1
        # 第 1 次对话未勾选知识库；第 2 次对话勾选了知识库并触发检索工具
        return KnowledgeAwareModel(call_kb_tool=run_counter["n"] >= 2)

    app = create_web_app(
        local_mode=True,
        workspace=tmp_path,
        data_dir=tmp_path / "sessions",
        knowledge_db_path=tmp_path / "knowledge.db",
        model_factory=fake_model_factory,
        reviewer_factory=lambda **_: PassThroughTextReviewer(),
        embedding_factory=lambda **_: FakeEmbeddingProvider(),
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://127.0.0.1"
    ) as client:
        # 1. 创建知识库并一步上传 PDF 转为向量库
        kb_resp = await client.post(
            "/api/knowledge-bases",
            json={
                "name": "学校本科培养方案",
                "description": "课程与图书馆规定",
                "embedding_provider": "gemini",
                "embedding_dim": 4,
            },
        )
        assert kb_resp.status_code == 200
        kb = kb_resp.json()
        kb_id = kb["kb_id"]

        upload_resp = await client.post(
            f"/api/knowledge-bases/{kb_id}/documents",
            files=[("files", ("school_guide.pdf", make_pdf(), "application/pdf"))],
            data={"enable_ocr": "false"},
        )
        assert upload_resp.status_code == 200
        upload_body = upload_resp.json()
        assert len(upload_body["documents"]) == 1
        assert upload_body["documents"][0]["page_count"] == 2
        assert upload_body["knowledge_base"]["document_count"] == 1
        doc_id = upload_body["documents"][0]["doc_id"]

        # 2. 即时检索验证接口返回带页码的命中结果
        search_resp = await client.post(
            f"/api/knowledge-bases/{kb_id}/search",
            json={"query": "CS302 credits", "top_k": 3},
        )
        assert search_resp.status_code == 200
        hits = search_resp.json()["hits"]
        assert len(hits) >= 1
        assert hits[0]["filename"] == "school_guide.pdf"
        assert hits[0]["page_number"] == 2

        # 3. 创建会话：默认未勾选知识库时，绝不向模型暴露 search_knowledge_base 工具
        sess = (
            await client.post(
                "/api/sessions",
                json={"title": "学生问答", "provider": "gemini"},
            )
        ).json()
        sid = sess["session_id"]

        run1 = await client.post(
            f"/api/sessions/{sid}/runs",
            json={
                "query": "今天天气怎么样",
                "provider": "gemini",
                "knowledge_base_ids": [],
                "safety_enabled": False,
            },
        )
        assert run1.status_code == 200
        assert "search_knowledge_base" not in seen_tool_sets[0]

        # 4. 勾选知识库后，自动开放 search_knowledge_base、注入引用指令并返回带页码片段
        run2 = await client.post(
            f"/api/sessions/{sid}/runs",
            json={
                "query": "CS302 有几个学分？",
                "provider": "gemini",
                "knowledge_base_ids": [kb_id],
                "safety_enabled": False,
            },
        )
        assert run2.status_code == 200
        frames2 = _parse_sse_frames(run2.text)
        assert "search_knowledge_base" in seen_tool_sets[1]
        assert any(
            "学校本科培养方案" in (p.text or "")
            for c in seen_contents[1]
            for p in (c.parts or [])
        )
        kb_tool_done = [
            f
            for f in frames2
            if f.get("type") == "tool.completed"
            and f.get("tool_name") == "search_knowledge_base"
        ]
        assert len(kb_tool_done) == 1
        assert "《school_guide.pdf》第 2 页" in kb_tool_done[0]["result"]
        assert "4 credits" in kb_tool_done[0]["result"]

        # 验证会话已持久化勾选的知识库列表
        detail = (await client.get(f"/api/sessions/{sid}")).json()
        assert detail["knowledge_base_ids"] == [kb_id]

        # 5. 删除文档与知识库清理
        del_doc = await client.delete(
            f"/api/knowledge-bases/{kb_id}/documents/{doc_id}"
        )
        assert del_doc.status_code == 200
        assert del_doc.json()["knowledge_base"]["document_count"] == 0

        del_kb = await client.delete(f"/api/knowledge-bases/{kb_id}")
        assert del_kb.status_code == 200
        assert del_kb.json()["deleted"] is True


@pytest.mark.asyncio
async def test_web_rich_output_skill_and_verified_media_proxy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """验证富媒体技能提示词注入、search_images 已验证 URL 白名单与安全图片代理。"""
    verified_url = "https://upload.wikimedia.org/commons/thumb/germany-oil.png"
    unverified_url = "https://evil.example.org/hallucinated-map.png"
    png_bytes = b"\x89PNG\r\n\x1a\nverified-image-bytes"

    async def fake_serper(query: str, max_results: int, api_key: str) -> dict[str, Any]:
        assert query == "Germany oil pipelines map"
        assert max_results == 2
        assert api_key == "sk-serper-test"
        return {
            "images": [
                {
                    "title": "Germany Oil Pipelines",
                    "imageUrl": verified_url,
                    "source": "Wikimedia Commons",
                    "link": "https://commons.wikimedia.org/wiki/File:Germany_oil.png",
                    "imageWidth": 1200,
                    "imageHeight": 800,
                }
            ]
        }

    monkeypatch.setattr(
        "agent_service.adapters.tools.search_images._default_serper_fetcher",
        fake_serper,
    )

    def proxy_handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == verified_url
        return httpx.Response(200, content=png_bytes, headers={"Content-Type": "image/png"})

    async def public_dns(host: str, port: int) -> list[str]:
        del host, port
        return ["93.184.216.34"]

    proxy = ImageProxyService(
        transport=httpx.MockTransport(proxy_handler),
        resolver=public_dns,
    )

    img_call = types.FunctionCall(
        id="call_img_1",
        name="search_images",
        args={"query": "Germany oil pipelines map", "max_results": 2},
    )
    rich_reply = (
        "## 德国原油进口与运输\n\n"
        "```chart\n"
        '{"type":"horizontal_bar","title":"德国原油进口来源","unit":"%","items":[{"badge":"NO","label":"挪威","value":16.7,"display":"16.7%"}]}\n'
        "```\n\n"
        "```gallery\n"
        f'{{"title":"德国管网图","items":[{{"url":"{verified_url}","caption":"德国炼油厂管网"}}]}}\n'
        "```\n"
    )
    model = ScriptedModel(
        [
            [
                StreamEvent(type="message_start"),
                StreamEvent(
                    type="message_done",
                    result=StreamResult(
                        contents="",
                        stop_reason="tool_use",
                        usage={},
                        function_calls=[img_call],
                        raw_parts=[types.Part(function_call=img_call)],
                    ),
                ),
            ],
            [
                StreamEvent(type="message_start"),
                StreamEvent(type="text", text=rich_reply),
                StreamEvent(
                    type="message_done",
                    result=StreamResult(
                        contents=rich_reply,
                        stop_reason="STOP",
                        usage={},
                        raw_parts=[types.Part.from_text(text=rich_reply)],
                    ),
                ),
            ],
        ]
    )

    app = create_web_app(
        workspace=tmp_path,
        data_dir=tmp_path / "sessions",
        knowledge_db_path=tmp_path / "knowledge.db",
        model_factory=lambda **_: model,
        reviewer_factory=lambda **_: PassThroughTextReviewer(),
        image_proxy=proxy,
        local_mode=True,
    )

    transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 50000))
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
        health = (await client.get("/api/health")).json()
        assert "image_search" in health
        assert health["image_search"]["fallback"] == "wikimedia"

        tools_payload = (await client.get("/api/tools")).json()
        search_img_tool = next(t for t in tools_payload["tools"] if t["name"] == "search_images")
        assert search_img_tool["read_only"] is True
        assert search_img_tool["requires_approval"] is False

        created = (await client.post("/api/sessions", json={"title": "富媒体测试"})).json()
        sid = created["session_id"]

        run_resp = await client.post(
            f"/api/sessions/{sid}/runs",
            json={
                "query": "德国的石油是从哪里获得的？",
                "provider": "gemini",
                "serper_api_key": "sk-serper-test",
                "safety_enabled": False,
            },
        )
        assert run_resp.status_code == 200
        frames = _parse_sse_frames(run_resp.text)

        # 验证富媒体呈现规范已注入系统提示词
        assert model.seen_system_prompts
        assert "富媒体综合呈现规范" in model.seen_system_prompts[0]
        assert "search_images" in model.seen_tool_names[0]

        # 验证 SSE 事件携带已验证图片 URL 列表
        done_frame = next(f for f in frames if f.get("type") == "run.completed")
        assert verified_url in done_frame["verified_media_urls"]

        # 验证会话详情持久化了 verified_media_urls
        detail = (await client.get(f"/api/sessions/{sid}")).json()
        assistant_msg = next(m for m in detail["messages"] if m["role"] == "assistant")
        assert verified_url in assistant_msg["verified_media_urls"]

        # 已验证图片 URL 允许代理并正确返回图片内容
        ok_img = await client.get(
            f"/api/sessions/{sid}/media-proxy",
            params={"url": verified_url},
        )
        assert ok_img.status_code == 200
        assert ok_img.content == png_bytes
        assert ok_img.headers["content-type"].startswith("image/png")
        assert ok_img.headers["x-content-type-options"] == "nosniff"

        # 未在本会话工具返回过的图片 URL 被严格拒绝（防开放代理与模型臆造 URL）
        blocked_img = await client.get(
            f"/api/sessions/{sid}/media-proxy",
            params={"url": unverified_url},
        )
        assert blocked_img.status_code == 403
