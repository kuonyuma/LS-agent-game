"""Web 适配器 REST API、会话持久化、SSE 流与指纹审批的集成测试。"""

import asyncio
import json
import socket
from collections.abc import AsyncGenerator, Sequence
from pathlib import Path
from typing import Any

import httpx
import pytest
import uvicorn
from google.genai import types

from agent_service.adapters.security.reviewer import PassThroughTextReviewer
from agent_service.adapters.web.app import create_web_app
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

    async def stream(
        self,
        *,
        contents: list[types.Content],
        system_prompt: str,
        max_tokens: int,
        tools: list[types.Tool] | None = None,
    ) -> AsyncGenerator[StreamEvent]:
        _ = (contents, system_prompt)
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
async def test_providers_expose_model_effort_visuals(tmp_path: Path):
    app = create_web_app(workspace=tmp_path)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
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
        workspace=tmp_path,
        data_dir=data_dir,
        model_factory=fake_model_factory,
        reviewer_factory=lambda **_: PassThroughTextReviewer(),
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        prov_resp = await client.get("/api/providers")
        assert prov_resp.status_code == 200
        providers_by_id = {p["id"]: p for p in prov_resp.json()["providers"]}
        assert {"gemini", "deepseek", "kimi"}.issubset(providers_by_id)

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


@pytest.mark.asyncio
async def test_workspace_switching_browse_validate_and_file_attachments(tmp_path: Path):
    default_ws = tmp_path / "default_ws"
    alt_ws = tmp_path / "alt_project"
    default_ws.mkdir()
    alt_ws.mkdir()
    (alt_ws / "hello.txt").write_text("from alt workspace", encoding="utf-8")

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
        workspace=default_ws,
        data_dir=tmp_path / "sessions",
        model_factory=lambda **_: InspectingModel(),
        reviewer_factory=lambda **_: PassThroughTextReviewer(),
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
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
                    }
                ],
            },
        )
        assert run_resp.status_code == 200
        frames = _parse_sse_frames(run_resp.text)
        tool_done = [f for f in frames if f.get("type") == "tool.completed"]
        assert len(tool_done) == 1
        assert "from alt workspace" in tool_done[0]["result"]
        assert any(
            '<attached-file name="spec.md"' in (p.text or "")
            for c in captured_contents[0]
            for p in (c.parts or [])
        )


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
        workspace=tmp_path,
        data_dir=tmp_path / "sessions",
        model_factory=fake_model_factory,
        reviewer_factory=lambda **_: PassThroughTextReviewer(),
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
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
        workspace=tmp_path,
        data_dir=tmp_path / "sessions",
        knowledge_db_path=tmp_path / "knowledge.db",
        model_factory=fake_model_factory,
        reviewer_factory=lambda **_: PassThroughTextReviewer(),
        embedding_factory=lambda **_: FakeEmbeddingProvider(),
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
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
