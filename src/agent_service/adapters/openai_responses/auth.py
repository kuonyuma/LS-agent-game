"""Sign in with ChatGPT（OAuth 2.0 + OIDC + PKCE 动态注册）与账号模型目录管理。"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlencode, urlparse
from uuid import uuid4

import httpx

from agent_service.config.settings import (
    VALID_THINKING_LEVELS,
    EffortVisual,
    ModelOption,
    ThinkingLevelValue,
)
from agent_service.errors import AgentRuntimeError, ConfigurationError

OPENAI_AUTH_ISSUER = "https://auth.openai.com"
OPENAI_AUTHORIZE_URL = f"{OPENAI_AUTH_ISSUER}/api/accounts/authorize"
OPENAI_TOKEN_URL = f"{OPENAI_AUTH_ISSUER}/api/accounts/oauth/token"
OPENAI_API_BASE_URL = "https://api.openai.com/v1"
DYNAMIC_AGENT_CLIENT_ID = "dynamic_agent_client"
CHATGPT_OAUTH_SCOPES = (
    "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"
)
CHATGPT_USAGE_SETTINGS_URL = "https://chatgpt.com/settings/usage"

_PENDING_STATE_TTL_SECONDS = 600.0
_TOKEN_REFRESH_MARGIN_SECONDS = 60.0
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})

_REASONING_LEVEL_ALIAS: dict[str, ThinkingLevelValue] = {
    "none": "off",
    "off": "off",
    "minimal": "low",
    "low": "low",
    "medium": "medium",
    "high": "high",
    "xhigh": "xhigh",
    "max": "max",
}


def _b64url_no_pad(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _pkce_s256_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return _b64url_no_pad(digest)


def _decode_jwt_payload(token: str) -> dict[str, Any]:
    """仅解码 JWT payload 用于展示用户邮箱与名称，不用于信任验证之外的敏感用途。"""

    parts = token.split(".")
    if len(parts) < 2:
        return {}
    segment = parts[1]
    padded = segment + "=" * (-len(segment) % 4)
    try:
        decoded = base64.urlsafe_b64decode(padded.encode("ascii"))
        parsed = json.loads(decoded.decode("utf-8"))
        return parsed if isinstance(parsed, dict) else {}
    except (ValueError, UnicodeDecodeError):
        return {}


def _validate_loopback_redirect_uri(redirect_uri: str) -> str:
    parsed = urlparse(redirect_uri)
    if parsed.scheme != "http" or (parsed.hostname or "").lower() not in _LOOPBACK_HOSTS:
        raise ConfigurationError(
            "本地 ChatGPT OAuth 回调地址仅允许使用本机 loopback（http://127.0.0.1 或 http://localhost）。"
        )
    return redirect_uri


def _parse_models_catalog(payload: dict[str, Any]) -> list[ModelOption]:
    """解析 GET /v1/models 返回的账号可用模型目录（兼容 ChatGPT 套餐与标准平台格式）。"""

    raw_models = payload.get("models")
    if isinstance(raw_models, list):
        parsed: list[ModelOption] = []
        seen: set[str] = set()
        for item in raw_models:
            if not isinstance(item, dict):
                continue
            visibility = str(item.get("visibility") or "list").strip().lower()
            if visibility != "list":
                continue
            slug = str(item.get("slug") or item.get("id") or "").strip()
            if not slug or slug in seen:
                continue
            seen.add(slug)
            label = str(item.get("display_name") or item.get("name") or slug).strip()

            raw_modalities = item.get("input_modalities")
            if isinstance(raw_modalities, list):
                supports_vision = any(
                    str(m).strip().lower() in {"image", "vision", "multimodal"}
                    for m in raw_modalities
                )
            else:
                supports_vision = True

            levels: list[str] = []
            raw_levels = item.get("supported_reasoning_levels")
            if isinstance(raw_levels, list):
                for entry in raw_levels:
                    raw_lv = (
                        entry.get("effort")
                        if isinstance(entry, dict)
                        else entry
                    )
                    if not isinstance(raw_lv, str):
                        continue
                    mapped = _REASONING_LEVEL_ALIAS.get(raw_lv.strip().lower())
                    if mapped and mapped in VALID_THINKING_LEVELS and mapped not in levels:
                        levels.append(mapped)

            supports_thinking = bool(levels)
            default_raw = str(item.get("default_reasoning_level") or "").strip().lower()
            default_mapped = _REASONING_LEVEL_ALIAS.get(default_raw, "")
            if levels:
                default_level = (
                    default_mapped
                    if default_mapped in levels
                    else ("high" if "high" in levels else levels[-1])
                )
            else:
                default_level = ""

            effort_visual: EffortVisual | None = None
            if "xhigh" in levels:
                effort_visual = EffortVisual(
                    level="xhigh", effect="bloom", duration_ms=920
                )
            elif "max" in levels:
                effort_visual = EffortVisual(
                    level="max", effect="bloom", duration_ms=920
                )

            raw_max_out = item.get("max_output_tokens")
            max_out = (
                raw_max_out
                if isinstance(raw_max_out, int) and not isinstance(raw_max_out, bool) and raw_max_out >= 256
                else 128000
            )
            thinking_mode = (
                "toggleable"
                if ("off" in levels)
                else ("always_on" if levels else "none")
            )
            parsed.append(
                ModelOption(
                    id=slug,
                    label=label,
                    supports_vision=supports_vision,
                    supports_thinking=supports_thinking,
                    thinking_mode=cast(Any, thinking_mode),
                    thinking_levels=tuple(levels),
                    default_thinking_level=default_level,
                    max_output_tokens=max_out,
                    non_thinking_max_output_tokens=max_out,
                    effort_visual=effort_visual,
                )
            )
        return parsed

    raw_data = payload.get("data")
    if isinstance(raw_data, list):
        fallback: list[ModelOption] = []
        seen_ids: set[str] = set()
        for item in raw_data:
            if not isinstance(item, dict):
                continue
            mid = str(item.get("id") or "").strip()
            if not mid or mid in seen_ids:
                continue
            seen_ids.add(mid)
            fallback.append(
                ModelOption(
                    id=mid,
                    label=mid,
                    supports_vision=True,
                    supports_thinking=True,
                    thinking_levels=("low", "medium", "high", "xhigh"),
                    default_thinking_level="high",
                    max_output_tokens=128000,
                    non_thinking_max_output_tokens=128000,
                    effort_visual=EffortVisual(
                        level="xhigh", effect="bloom", duration_ms=920
                    ),
                )
            )
        return fallback
    return []


@dataclass(frozen=True)
class ChatGPTAuthStatus:
    connected: bool
    account_email: str = ""
    account_name: str = ""
    has_client_id: bool = False
    expires_at: float = 0.0
    usage_url: str = CHATGPT_USAGE_SETTINGS_URL

    def to_dict(self) -> dict[str, Any]:
        return {
            "connected": self.connected,
            "account_email": self.account_email,
            "account_name": self.account_name,
            "has_client_id": self.has_client_id,
            "expires_at": self.expires_at,
            "usage_url": self.usage_url,
        }


@dataclass
class _PendingAuthState:
    state: str
    nonce: str
    code_verifier: str
    redirect_uri: str
    client_id: str
    created_at: float


class ChatGPTAuthManager:
    """管理本机 ChatGPT OAuth 2.0 (PKCE) 登录态、令牌刷新与账号可用模型目录。"""

    def __init__(
        self,
        storage_path: Path,
        *,
        api_base_url: str = OPENAI_API_BASE_URL,
        authorize_url: str = OPENAI_AUTHORIZE_URL,
        token_url: str = OPENAI_TOKEN_URL,
        transport: httpx.AsyncBaseTransport | None = None,
        model_cache_ttl: float = 300.0,
    ) -> None:
        self.storage_path = storage_path
        self._api_base_url = api_base_url.rstrip("/")
        self._authorize_url = authorize_url
        self._token_url = token_url
        self._transport = transport
        self._model_cache_ttl = model_cache_ttl
        self._refresh_lock = asyncio.Lock()
        self._pending: dict[str, _PendingAuthState] = {}
        self._cached_models: list[ModelOption] | None = None
        self._cached_models_at: float = 0.0
        self._state: dict[str, Any] = self._load_state()

    def _load_state(self) -> dict[str, Any]:
        if not self.storage_path.exists():
            return {}
        try:
            raw = json.loads(self.storage_path.read_text(encoding="utf-8"))
            return raw if isinstance(raw, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save_state(self) -> None:
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.storage_path.with_suffix(".json.tmp")
        payload = json.dumps(self._state, ensure_ascii=False, indent=2)
        tmp_path.write_text(payload, encoding="utf-8")
        if os.name == "posix":
            try:
                os.chmod(tmp_path, 0o600)
            except OSError:
                pass
        os.replace(tmp_path, self.storage_path)

    def _ensure_host_id(self) -> str:
        host_id = str(self._state.get("ext_agent_host_id") or "").strip()
        if not host_id:
            host_id = f"urn:uuid:{uuid4()}"
            self._state["ext_agent_host_id"] = host_id
            self._save_state()
        return host_id

    def _saved_client_id(self) -> str:
        cid = str(self._state.get("client_id") or "").strip()
        return cid if cid and cid != DYNAMIC_AGENT_CLIENT_ID else ""

    def _prune_pending(self) -> None:
        now = time.time()
        expired = [
            key
            for key, item in self._pending.items()
            if now - item.created_at > _PENDING_STATE_TTL_SECONDS
        ]
        for key in expired:
            self._pending.pop(key, None)

    def _create_http_client(self) -> httpx.AsyncClient:
        kwargs: dict[str, Any] = {"timeout": 20.0}
        if self._transport is not None:
            kwargs["transport"] = self._transport
        return httpx.AsyncClient(**kwargs)

    def is_connected(self) -> bool:
        return bool(
            str(self._state.get("access_token") or "").strip()
            or str(self._state.get("refresh_token") or "").strip()
        )

    def get_status(self) -> ChatGPTAuthStatus:
        return ChatGPTAuthStatus(
            connected=self.is_connected(),
            account_email=str(self._state.get("account_email") or ""),
            account_name=str(self._state.get("account_name") or ""),
            has_client_id=bool(self._saved_client_id()),
            expires_at=float(self._state.get("expires_at") or 0.0),
        )

    def start_login(
        self,
        redirect_uri: str,
        *,
        agent_name_hint: str = "智启 Agent",
    ) -> dict[str, str]:
        """构造 Sign in with ChatGPT 授权跳转链接并保存一次性 PKCE/state。"""

        safe_redirect = _validate_loopback_redirect_uri(redirect_uri)
        self._prune_pending()

        host_id = self._ensure_host_id()
        saved_cid = self._saved_client_id()
        active_client_id = saved_cid or DYNAMIC_AGENT_CLIENT_ID

        code_verifier = _b64url_no_pad(secrets.token_bytes(48))
        code_challenge = _pkce_s256_challenge(code_verifier)
        state = _b64url_no_pad(secrets.token_bytes(24))
        nonce = _b64url_no_pad(secrets.token_bytes(24))

        self._pending[state] = _PendingAuthState(
            state=state,
            nonce=nonce,
            code_verifier=code_verifier,
            redirect_uri=safe_redirect,
            client_id=active_client_id,
            created_at=time.time(),
        )

        params: dict[str, str] = {
            "response_type": "code",
            "client_id": active_client_id,
            "redirect_uri": safe_redirect,
            "scope": CHATGPT_OAUTH_SCOPES,
            "resource": self._api_base_url,
            "state": state,
            "nonce": nonce,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        if active_client_id == DYNAMIC_AGENT_CLIENT_ID:
            params["ext_agent_host_id"] = host_id
            params["agent_name_hint"] = agent_name_hint

        return {
            "authorize_url": f"{self._authorize_url}?{urlencode(params)}",
            "state": state,
            "redirect_uri": safe_redirect,
        }

    async def complete_callback(
        self,
        *,
        code: str,
        state: str,
        callback_client_id: str | None = None,
        scope: str | None = None,
    ) -> ChatGPTAuthStatus:
        """校验回调 state，用授权码换取令牌并落盘保存签发的 client_id。"""

        self._prune_pending()
        pending = self._pending.pop(state, None)
        if pending is None:
            raise ConfigurationError("ChatGPT 授权 state 无效或已过期，请重新点击登录。")

        cb_cid = (callback_client_id or "").strip()
        exchange_client_id = (
            cb_cid
            if cb_cid and cb_cid != DYNAMIC_AGENT_CLIENT_ID
            else pending.client_id
        )

        form_data = {
            "grant_type": "authorization_code",
            "client_id": exchange_client_id,
            "code": code.strip(),
            "code_verifier": pending.code_verifier,
            "redirect_uri": pending.redirect_uri,
            "resource": self._api_base_url,
        }

        async with self._create_http_client() as client:
            try:
                resp = await client.post(
                    self._token_url,
                    data=form_data,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
            except httpx.HTTPError as exc:
                raise AgentRuntimeError(f"无法连接 OpenAI 授权服务：{exc}") from exc

        if resp.status_code != 200:
            detail = self._extract_oauth_error(resp)
            raise ConfigurationError(f"ChatGPT 授权换取令牌失败（HTTP {resp.status_code}）：{detail}")

        token_data = resp.json()
        access_token = str(token_data.get("access_token") or "").strip()
        if not access_token:
            raise ConfigurationError("ChatGPT 授权返回中缺少 access_token。")

        refresh_token = str(token_data.get("refresh_token") or "").strip()
        id_token = str(token_data.get("id_token") or "").strip()
        expires_in_raw = token_data.get("expires_in", 3600)
        expires_in = (
            float(expires_in_raw)
            if isinstance(expires_in_raw, (int, float)) and expires_in_raw > 0
            else 3600.0
        )

        issued_cid = str(token_data.get("client_id") or cb_cid or self._saved_client_id()).strip()
        if issued_cid and issued_cid != DYNAMIC_AGENT_CLIENT_ID:
            self._state["client_id"] = issued_cid

        claims = _decode_jwt_payload(id_token) if id_token else {}
        self._state.update(
            {
                "access_token": access_token,
                "refresh_token": refresh_token or str(self._state.get("refresh_token") or ""),
                "id_token": id_token,
                "scope": str(token_data.get("scope") or scope or CHATGPT_OAUTH_SCOPES),
                "expires_at": time.time() + expires_in,
                "account_email": str(claims.get("email") or self._state.get("account_email") or ""),
                "account_name": str(claims.get("name") or self._state.get("account_name") or ""),
            }
        )
        self._save_state()
        self.clear_model_cache()
        return self.get_status()

    async def get_valid_access_token(self) -> str:
        """返回可用的 access_token，过期前自动加锁刷新。"""

        async with self._refresh_lock:
            access_token = str(self._state.get("access_token") or "").strip()
            expires_at = float(self._state.get("expires_at") or 0.0)
            now = time.time()
            if access_token and (expires_at <= 0 or expires_at - now > _TOKEN_REFRESH_MARGIN_SECONDS):
                return access_token

            refresh_token = str(self._state.get("refresh_token") or "").strip()
            client_id = self._saved_client_id()
            if not refresh_token or not client_id:
                self.invalidate_tokens()
                raise ConfigurationError(
                    "ChatGPT 登录态已失效，请在设置中重新点击「使用 ChatGPT 登录」。"
                )

            form_data = {
                "grant_type": "refresh_token",
                "client_id": client_id,
                "refresh_token": refresh_token,
                "resource": self._api_base_url,
            }
            async with self._create_http_client() as client:
                try:
                    resp = await client.post(
                        self._token_url,
                        data=form_data,
                        headers={"Content-Type": "application/x-www-form-urlencoded"},
                    )
                except httpx.HTTPError as exc:
                    raise AgentRuntimeError(f"刷新 ChatGPT 令牌失败：{exc}") from exc

            if resp.status_code in (400, 401):
                detail = self._extract_oauth_error(resp)
                self.invalidate_tokens()
                raise ConfigurationError(
                    f"ChatGPT 登录已失效或被撤销，请重新登录（{detail}）。"
                )
            if resp.status_code != 200:
                detail = self._extract_oauth_error(resp)
                raise AgentRuntimeError(
                    f"刷新 ChatGPT 令牌返回异常（HTTP {resp.status_code}）：{detail}"
                )

            token_data = resp.json()
            new_access = str(token_data.get("access_token") or "").strip()
            if not new_access:
                raise AgentRuntimeError("刷新 ChatGPT 令牌未返回有效 access_token。")

            new_refresh = str(token_data.get("refresh_token") or "").strip()
            new_id = str(token_data.get("id_token") or "").strip()
            expires_in_raw = token_data.get("expires_in", 3600)
            expires_in = (
                float(expires_in_raw)
                if isinstance(expires_in_raw, (int, float)) and expires_in_raw > 0
                else 3600.0
            )
            self._state["access_token"] = new_access
            if new_refresh:
                self._state["refresh_token"] = new_refresh
            if new_id:
                self._state["id_token"] = new_id
                claims = _decode_jwt_payload(new_id)
                if claims.get("email"):
                    self._state["account_email"] = str(claims["email"])
                if claims.get("name"):
                    self._state["account_name"] = str(claims["name"])
            self._state["expires_at"] = time.time() + expires_in
            self._save_state()
            return new_access

    def invalidate_tokens(self) -> None:
        """清除失效令牌但保留 ext_agent_host_id 与签发的 client_id 便于下次复用。"""

        for key in ("access_token", "refresh_token", "id_token", "expires_at", "account_email", "account_name"):
            self._state.pop(key, None)
        self._save_state()
        self.clear_model_cache()

    def logout(self, *, reset_client: bool = False) -> None:
        self._pending.clear()
        if reset_client:
            self._state.pop("client_id", None)
        self.invalidate_tokens()

    def clear_model_cache(self) -> None:
        self._cached_models = None
        self._cached_models_at = 0.0

    def get_cached_models(self) -> list[ModelOption]:
        return list(self._cached_models) if self._cached_models else []

    async def fetch_models(self, *, force_refresh: bool = False) -> list[ModelOption]:
        """用当前 ChatGPT 登录态动态拉取账号可用的 GPT 模型列表。"""

        now = time.time()
        if (
            not force_refresh
            and self._cached_models is not None
            and (now - self._cached_models_at) < self._model_cache_ttl
        ):
            return list(self._cached_models)

        token = await self.get_valid_access_token()
        async with self._create_http_client() as client:
            try:
                resp = await client.get(
                    f"{self._api_base_url}/models",
                    headers={"Authorization": f"Bearer {token}"},
                )
            except httpx.HTTPError as exc:
                raise AgentRuntimeError(f"拉取 OpenAI 模型目录失败：{exc}") from exc

        if resp.status_code == 401:
            self.invalidate_tokens()
            raise ConfigurationError("ChatGPT 登录态已失效，请重新登录。")
        if resp.status_code != 200:
            raise AgentRuntimeError(
                f"拉取 OpenAI 模型目录失败（HTTP {resp.status_code}）：{resp.text[:240]}"
            )

        payload = resp.json()
        models = _parse_models_catalog(payload if isinstance(payload, dict) else {})
        self._cached_models = models
        self._cached_models_at = now
        return list(models)

    @staticmethod
    def _extract_oauth_error(resp: httpx.Response) -> str:
        try:
            body = resp.json()
            if isinstance(body, dict):
                err = body.get("error_description") or body.get("error") or body.get("message")
                if err:
                    return str(err)
        except ValueError:
            pass
        return resp.text[:200] or "未知错误"
