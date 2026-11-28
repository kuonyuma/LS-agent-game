"""验证登录、退出以及共享工作台的账号边界。"""

import asyncio
import sqlite3
from contextlib import closing
from pathlib import Path

import httpx

from agent_service.adapters.storage.account_store import AccountStore
from agent_service.adapters.storage.knowledge_store import KnowledgeStore
from agent_service.adapters.storage.session_store import SessionStore
from agent_service.adapters.web.app import create_web_app

_WRITE_HEADERS = {"X-Agent-Request": "1"}
_LOGIN = {"username": "owner", "password": "test-password-owner"}
_MEMBER = {"username": "member", "password": "test-password-member"}


async def test_first_admin_setup_is_local_and_only_succeeds_once(tmp_path: Path):
    app = create_web_app(workspace=tmp_path, allow_browser_setup=True)
    transport = httpx.ASGITransport(app=app)
    setup = "/api/auth/setup"
    base_url = "http://127.0.0.1"
    local_headers = {**_WRITE_HEADERS, "Origin": base_url}
    async with (
        httpx.AsyncClient(transport=transport, base_url=base_url, headers=local_headers) as local,
        httpx.AsyncClient(transport=transport, base_url=base_url, headers=local_headers) as other,
        httpx.AsyncClient(transport=transport, base_url="https://workbench.test", headers=_WRITE_HEADERS) as remote,
    ):
        assert (await local.get(setup)).json() == {"can_create_admin": True}
        assert (await remote.get(setup)).json() == {"can_create_admin": False}
        assert (await remote.post(setup, json=_LOGIN)).status_code == 403
        assert (await local.post(setup, json=_LOGIN, headers={"X-Forwarded-For": "198.51.100.7"})).status_code == 403
        assert (await local.post(setup, json=_LOGIN, headers={"Origin": "https://workbench.test"})).status_code == 403
        invalid = await local.post(setup, json={**_LOGIN, "password": "short-secret"})
        assert invalid.status_code == 422
        assert "short-secret" not in invalid.text

        first, second = await asyncio.gather(
            local.post(setup, json=_LOGIN), other.post(setup, json=_MEMBER)
        )
        assert sorted([first.status_code, second.status_code]) == [201, 409]
        winner, created = (local, first) if first.status_code == 201 else (other, second)
        assert created.json()["role"] == "admin"
        assert (await winner.get("/api/auth/me")).json() == created.json()
        assert (await local.get(setup)).json() == {"can_create_admin": False}
        assert (await local.post(setup, json=_MEMBER)).status_code == 409


async def test_web_account_lifecycle(tmp_path: Path):
    accounts = AccountStore(tmp_path / ".agent_data")
    accounts.create_account(**_LOGIN, role="admin")
    app = create_web_app(workspace=tmp_path)
    transport = httpx.ASGITransport(app=app)
    async with (
        httpx.AsyncClient(transport=transport, base_url="https://workbench.test") as client,
        httpx.AsyncClient(transport=transport, base_url="https://workbench.test", headers=_WRITE_HEADERS) as member,
    ):
        assert (await client.get("/health")).json() == {"status": "ok"}
        for path in ("/api/auth/me", "/api/providers", "/api/sessions", "/api/knowledge-bases", "/api/health"):
            res = await client.get(path)
            assert res.status_code == 401
            assert res.headers["Cache-Control"] == "no-store"
        unauthenticated = await client.post(
            "/api/sessions", content=b"invalid json", headers={"Content-Type": "application/json", **_WRITE_HEADERS}
        )
        assert unauthenticated.status_code == 401
        assert (await client.post("/api/auth/users", json=_MEMBER, headers=_WRITE_HEADERS)).status_code == 401
        assert (await client.post("/api/auth/login", json=_LOGIN)).status_code == 403
        assert (await client.post("http://workbench.test/api/auth/login", json=_LOGIN, headers=_WRITE_HEADERS)).status_code == 403
        failed = await client.post("/api/auth/login", json={**_LOGIN, "password": "wrong"}, headers=_WRITE_HEADERS)
        assert failed.status_code == 401
        assert failed.json()["detail"] == "用户名或密码错误。"

        login = await client.post("/api/auth/login", json=_LOGIN, headers=_WRITE_HEADERS)
        assert login.status_code == 200
        assert login.json()["role"] == "admin"
        assert login.json()["mode"] == "shared"
        cookie_header = login.headers["Set-Cookie"].lower()
        assert all(flag in cookie_header for flag in ("httponly", "secure", "samesite=lax", "max-age=86400"))
        assert (await client.get("/api/auth/me")).json() == login.json()
        assert (await client.post("/api/sessions", json={})).status_code == 403
        assert (await client.get("/api/sessions", headers={"X-Agent-User": "different-identity"})).status_code == 401

        invalid = await client.post("/api/auth/users", json={**_MEMBER, "password": "short-secret"}, headers=_WRITE_HEADERS)
        assert invalid.status_code == 422
        assert "short-secret" not in invalid.text
        created = await client.post("/api/auth/users", json={**_MEMBER, "role": "admin"}, headers=_WRITE_HEADERS)
        assert created.status_code == 201
        assert created.json()["role"] == "user"
        assert "password" not in created.json()
        assert (await client.post("/api/auth/users", json=_MEMBER, headers=_WRITE_HEADERS)).status_code == 409
        assert (await member.post("/api/auth/login", json=_MEMBER)).status_code == 200
        assert (await member.post("/api/auth/users", json={**_MEMBER, "username": "third"})).status_code == 403

        old_token = client.cookies["agent_service_login"]
        second_login = await client.post("/api/auth/login", json=_LOGIN, headers=_WRITE_HEADERS)
        assert second_login.status_code == 200
        assert not accounts.get_login(old_token)
        token = client.cookies["agent_service_login"]
        assert (await client.post("/api/auth/logout", headers=_WRITE_HEADERS)).status_code == 200
        assert (await client.get("/api/auth/me", headers={"Cookie": f"agent_service_login={token}"})).status_code == 401
        assert (await member.get("/api/auth/me")).status_code == 200

        # 模拟登录过期，服务端要拒绝客户端仍持有的 Cookie。
        with closing(sqlite3.connect(accounts.path)) as conn, conn:
            conn.execute("UPDATE browser_logins SET expires_at = 0")
        assert (await member.get("/api/auth/me")).status_code == 401


async def test_web_shared_data_is_private_and_keeps_legacy_admin_data(tmp_path: Path):
    root = tmp_path / ".agent_data"
    accounts = AccountStore(root)
    accounts.create_account(**_LOGIN, role="admin")
    accounts.create_account(**_MEMBER, role="user")
    existing = SessionStore(root / "sessions").create(title="原来的聊天", workspace=str(tmp_path))
    old_kb = KnowledgeStore(root / "knowledge.db").create_knowledge_base(name="原来的知识库")
    transport = httpx.ASGITransport(app=create_web_app(workspace=tmp_path))
    async with (
        httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1", headers=_WRITE_HEADERS) as owner,
        httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1", headers=_WRITE_HEADERS) as member,
    ):
        assert (await owner.post("/api/auth/login", json=_LOGIN)).status_code == 200
        assert (await member.post("/api/auth/login", json=_MEMBER)).status_code == 200
        sessions = (await owner.get("/api/sessions")).json()["sessions"]
        assert [item["session_id"] for item in sessions] == [existing.session_id]
        assert sessions[0]["workspace"] == ""
        assert (await member.get("/api/sessions")).json() == {"sessions": []}
        assert (await member.get("/api/knowledge-bases")).json()["knowledge_bases"] == []
        assert (await owner.get("/api/knowledge-bases")).json()["knowledge_bases"][0]["kb_id"] == old_kb.kb_id

        new_session = (await member.post("/api/sessions", json={"title": "成员聊天"})).json()
        new_kb = (await member.post("/api/knowledge-bases", json={"name": "成员知识库"})).json()
        foreign_session = f"/api/sessions/{new_session['session_id']}"
        foreign_kb = f"/api/knowledge-bases/{new_kb['kb_id']}"
        for method, subpath, payload in (
            ("GET", "", None), ("PATCH", "", {"title": "拒绝修改"}),
            ("DELETE", "", None), ("POST", "/runs", {"query": "拒绝运行"}),
            ("GET", "/presentations/present_test/pptx", None),
        ):
            assert (await owner.request(method, foreign_session + subpath, json=payload)).status_code == 404
        assert (await owner.get(foreign_kb + "/documents")).status_code == 404
        assert (await owner.post(foreign_kb + "/documents", files={"files": ("demo.txt", b"demo")})).status_code == 404
        assert (await owner.post(foreign_kb + "/search", json={"query": "demo"})).status_code == 404
        assert (await owner.delete(foreign_kb + "/documents/doc_test")).status_code == 404
        assert (await owner.delete(foreign_kb)).status_code == 404
        assert (await owner.patch(f"/api/sessions/{existing.session_id}", json={
            "knowledge_base_ids": [new_kb["kb_id"]],
        })).status_code == 404

        assert (await member.get("/api/workspace/browse")).status_code == 403
        assert (await member.post("/api/sessions", json={"workspace": str(tmp_path)})).status_code == 403
        assert (await member.get("/api/auth/chatgpt/status")).status_code == 403
        assert (await member.post("/api/knowledge-bases", json={
            "name": "自定义地址", "embedding_base_url": "https://embedding.test",
        })).status_code == 403
        assert (await member.delete(foreign_session)).status_code == 200
        assert (await member.get(foreign_session)).status_code == 404
        assert (await member.get("/api/sessions")).json() == {"sessions": []}

        # 重建应用要保留账号和成员资源，且相同登录可继续使用。
        restarted = httpx.ASGITransport(app=create_web_app(workspace=tmp_path))
        async with httpx.AsyncClient(
            transport=restarted, base_url="http://127.0.0.1", cookies=member.cookies,
        ) as restored:
            assert (await restored.get("/api/auth/me")).json()["username"] == _MEMBER["username"]
            assert (await restored.get(foreign_kb + "/documents")).status_code == 200
            assert (await restored.get(foreign_session)).status_code == 404
