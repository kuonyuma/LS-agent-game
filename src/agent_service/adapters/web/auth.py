"""工作台登录、账号开通和受保护 API 的当前用户依赖。"""

import time
from collections.abc import Callable, Coroutine
from dataclasses import asdict, dataclass
from threading import BoundedSemaphore
from typing import Annotated, Any
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.routing import APIRoute
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from agent_service.adapters.storage.account_store import (
    LOGIN_MAX_AGE,
    PASSWORD_MIN_LENGTH,
    Account,
    AccountConflictError,
    AccountStore,
    BrowserLogin,
)

_COOKIE_NAME = "agent_service_login"
_LOOPBACK = frozenset({"127.0.0.1", "::1", "localhost"})
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_LOCAL_ACCOUNT = Account("local", "本机", "admin")


@dataclass(frozen=True)
class WebIdentity:
    account: Account
    login: BrowserLogin | None = None

    def to_dict(self) -> dict[str, str]:
        return {**asdict(self.account), "mode": "shared" if self.login else "local"}


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=40)
    password: str = Field(min_length=1, max_length=256)


class CreateUserRequest(BaseModel):
    username: str = Field(min_length=3, max_length=40)
    password: str = Field(min_length=PASSWORD_MIN_LENGTH, max_length=256)


def _is_loopback(request: Request) -> bool:
    return (
        request.url.hostname in _LOOPBACK
        and request.client is not None
        and request.client.host in _LOOPBACK
    )


def _local_origin(request: Request) -> bool:
    origin = request.headers.get("origin", "")
    try:
        return not origin or urlsplit(origin).hostname in _LOOPBACK
    except ValueError:
        return False


def _require_secure_transport(request: Request) -> None:
    if request.url.scheme != "https" and not (_is_loopback(request) and _local_origin(request)):
        raise HTTPException(status_code=403, detail="共享工作台请通过 HTTPS 访问。")


def _require_write_header(request: Request) -> None:
    if request.headers.get("x-agent-request") != "1":
        raise HTTPException(status_code=403, detail="缺少工作台请求标头。")


def _is_setup_request(request: Request) -> bool:
    if request.url.scheme != "http" or not _is_loopback(request) or not _local_origin(request):
        return False
    if request.method == "POST" and not request.headers.get("origin"):
        return False
    return not any(
        name.startswith("x-forwarded-") or name in {"forwarded", "via", "x-real-ip"}
        for name in request.headers
    )


def get_current_user(request: Request) -> WebIdentity:
    accounts: AccountStore | None = request.app.state.accounts
    if accounts is None:
        if not _is_loopback(request) or not _local_origin(request):
            raise HTTPException(status_code=403, detail="本机模式只允许回环地址访问。")
        return WebIdentity(_LOCAL_ACCOUNT)

    _require_secure_transport(request)
    login = accounts.get_login(request.cookies.get(_COOKIE_NAME, ""))
    if login is None:
        raise HTTPException(status_code=401, detail="请先登录工作台。")
    # 浏览器标签页带上它当前显示的账号，避免切换账号后旧页面继续操作。
    expected_user = request.headers.get("x-agent-user")
    if expected_user is not None and expected_user != login.account.id:
        raise HTTPException(status_code=401, detail="工作台账号已切换，请重新载入。")
    if request.method not in _SAFE_METHODS:
        _require_write_header(request)
    return WebIdentity(login.account, login)


class AuthenticatedRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()

        async def authenticate_request(request: Request) -> Response:
            # 在解析上传文件和请求体之前确认身份与写请求标头。
            request.state.workbench_user = await run_in_threadpool(get_current_user, request)
            return await handler(request)

        return authenticate_request


def _route_user(request: Request) -> WebIdentity:
    user = getattr(request.state, "workbench_user", None)
    return user if isinstance(user, WebIdentity) else get_current_user(request)


CurrentUser = Annotated[WebIdentity, Depends(_route_user)]


def create_auth_router(
    accounts: AccountStore | None,
    *,
    on_login_end: Callable[[BrowserLogin], None],
    allow_browser_setup: bool,
) -> APIRouter:
    router = APIRouter(prefix="/api/auth")
    attempts: dict[str, int] = {}
    attempt_minute = -1
    hashing_slots = BoundedSemaphore(4)

    @router.get("/me")
    def read_current_user(user: CurrentUser) -> dict[str, str]:
        return user.to_dict()

    if accounts is None:
        return router

    async def establish_login(account: Account, request: Request, response: Response) -> dict[str, str]:
        previous_token = request.cookies.get(_COOKIE_NAME, "")
        previous = await run_in_threadpool(accounts.get_login, previous_token)
        token = await run_in_threadpool(accounts.start_login, account, previous_token)
        if previous is not None:
            on_login_end(previous)
        response.set_cookie(
            _COOKIE_NAME,
            token,
            max_age=LOGIN_MAX_AGE,
            httponly=True,
            secure=request.url.scheme == "https",
            samesite="lax",
        )
        return {**asdict(account), "mode": "shared"}

    @router.get("/setup")
    def setup_status(request: Request) -> dict[str, bool]:
        return {"can_create_admin": allow_browser_setup and _is_setup_request(request) and not accounts.has_admin()}

    @router.post("/setup", status_code=201)
    async def create_first_admin(
        payload: CreateUserRequest, request: Request, response: Response
    ) -> dict[str, str]:
        if not allow_browser_setup or not _is_setup_request(request):
            raise HTTPException(status_code=403, detail="请在运行工作台的电脑上直接打开本机地址进行初始化。")
        _require_write_header(request)
        if await run_in_threadpool(accounts.has_admin):
            raise HTTPException(status_code=409, detail="管理员账号已创建，请登录。")
        if not hashing_slots.acquire(blocking=False):
            raise HTTPException(status_code=429, detail="登录服务繁忙，请稍后再试。")
        try:
            account = await run_in_threadpool(
                accounts.create_account, payload.username, payload.password, role="admin"
            )
        except AccountConflictError as exc:
            raise HTTPException(status_code=409, detail="管理员账号已创建，请登录。") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            hashing_slots.release()
        return await establish_login(account, request, response)

    @router.post("/login")
    async def login(payload: LoginRequest, request: Request, response: Response) -> dict[str, str]:
        nonlocal attempt_minute
        _require_secure_transport(request)
        _require_write_header(request)
        minute = int(time.monotonic() // 60)
        if minute != attempt_minute:
            attempts.clear()
            attempt_minute = minute
        source = request.client.host if request.client else "unknown"
        if attempts.get(source, 0) >= 10 or (source not in attempts and len(attempts) >= 4096):
            raise HTTPException(status_code=429, detail="登录尝试过于频繁，请稍后再试。")
        if not hashing_slots.acquire(blocking=False):
            raise HTTPException(status_code=429, detail="登录服务繁忙，请稍后再试。")
        attempts[source] = attempts.get(source, 0) + 1
        try:
            account = await run_in_threadpool(accounts.authenticate, payload.username, payload.password)
        finally:
            hashing_slots.release()
        if account is None:
            raise HTTPException(status_code=401, detail="用户名或密码错误。")

        return await establish_login(account, request, response)

    @router.post("/logout")
    async def logout(request: Request, response: Response) -> dict[str, bool]:
        _require_secure_transport(request)
        _require_write_header(request)
        token = request.cookies.get(_COOKIE_NAME, "")
        login = await run_in_threadpool(accounts.get_login, token)
        expected_user = request.headers.get("x-agent-user")
        if login is not None and expected_user is not None and expected_user != login.account.id:
            raise HTTPException(status_code=401, detail="工作台账号已切换，请重新载入。")
        await run_in_threadpool(accounts.revoke_login, token)
        if login is not None:
            on_login_end(login)
        response.delete_cookie(
            _COOKIE_NAME, httponly=True, secure=request.url.scheme == "https", samesite="lax"
        )
        return {"logged_out": True}

    admin_routes = APIRouter(route_class=AuthenticatedRoute)

    @admin_routes.post("/users", status_code=201)
    def create_user(payload: CreateUserRequest, user: CurrentUser) -> dict[str, str]:
        if user.account.role != "admin":
            raise HTTPException(status_code=403, detail="只有管理员可以开通账号。")
        try:
            account = accounts.create_account(payload.username, payload.password, role="user")
        except AccountConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return asdict(account)

    router.include_router(admin_routes)
    return router
