"""Web 账号和可撤销的浏览器登录会话。"""

import hashlib
import re
import secrets
import sqlite3
import time
from collections.abc import Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast
from uuid import uuid4

AccountRole = Literal["admin", "user"]
LOGIN_MAX_AGE = 24 * 60 * 60
PASSWORD_MIN_LENGTH = 15
_USERNAME = re.compile(r"[a-z0-9][a-z0-9_.-]{2,39}")


@dataclass(frozen=True)
class Account:
    id: str
    username: str
    role: AccountRole


@dataclass(frozen=True)
class BrowserLogin:
    account: Account
    login_id: str
    expires_at: int


class AccountConflictError(ValueError):
    """用户名或首个管理员已存在。"""


def _password_hash(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=2**15, r=8, p=3, maxmem=64 * 1024 * 1024
    )


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _account(row: sqlite3.Row) -> Account:
    return Account(row["id"], row["username"], cast(AccountRole, row["role"]))


class AccountStore:
    def __init__(self, data_root: Path) -> None:
        self.path = data_root / "auth" / "accounts.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS accounts (
                    id TEXT PRIMARY KEY,
                    username TEXT UNIQUE NOT NULL,
                    role TEXT NOT NULL CHECK (role IN ('admin', 'user')),
                    salt BLOB NOT NULL,
                    password_hash BLOB NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS accounts_one_admin
                    ON accounts (role) WHERE role = 'admin';
                CREATE TABLE IF NOT EXISTS browser_logins (
                    token_hash TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
                    expires_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS browser_logins_expiry ON browser_logins (expires_at);
                """
            )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        with closing(sqlite3.connect(self.path)) as conn, conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            yield conn

    def has_admin(self) -> bool:
        with self._connect() as conn:
            return conn.execute("SELECT 1 FROM accounts WHERE role = 'admin'").fetchone() is not None

    def create_account(self, username: str, password: str, *, role: AccountRole) -> Account:
        username = username.strip().lower()
        if not _USERNAME.fullmatch(username):
            raise ValueError("用户名须为 3–40 个字母、数字、点、下划线或连字符，并以字母或数字开头。")
        if not PASSWORD_MIN_LENGTH <= len(password) <= 256:
            raise ValueError(f"密码长度须为 {PASSWORD_MIN_LENGTH}–256 个字符。")
        user = Account(uuid4().hex, username, role)
        salt = secrets.token_bytes(16)
        digest = _password_hash(password, salt)
        try:
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO accounts (id, username, role, salt, password_hash) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (user.id, username, role, salt, digest),
                )
        except sqlite3.IntegrityError as exc:
            message = "管理员已存在或用户名已被使用。" if role == "admin" else "用户名已被使用。"
            raise AccountConflictError(message) from exc
        return user

    def authenticate(self, username: str, password: str) -> Account | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, username, role, salt, password_hash FROM accounts WHERE username = ?",
                (username.strip().lower(),),
            ).fetchone()
        # 无此账号时也执行相同强度的散列。
        salt = row["salt"] if row else bytes(16)
        expected = row["password_hash"] if row else bytes(64)
        matches = secrets.compare_digest(_password_hash(password, salt), expected)
        return _account(row) if matches and row is not None else None

    def start_login(self, user: Account, previous_token: str) -> str:
        token = secrets.token_urlsafe(32)
        now = int(time.time())
        with self._connect() as conn:
            conn.execute("DELETE FROM browser_logins WHERE expires_at <= ?", (now,))
            if previous_token:
                conn.execute(
                    "DELETE FROM browser_logins WHERE token_hash = ?", (_token_hash(previous_token),)
                )
            conn.execute(
                "INSERT INTO browser_logins (token_hash, account_id, expires_at) VALUES (?, ?, ?)",
                (_token_hash(token), user.id, now + LOGIN_MAX_AGE),
            )
        return token

    def get_login(self, token: str) -> BrowserLogin | None:
        if not token:
            return None
        login_id = _token_hash(token)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT a.id, a.username, a.role, l.expires_at "
                "FROM browser_logins l JOIN accounts a ON a.id = l.account_id "
                "WHERE l.token_hash = ? AND l.expires_at > ?",
                (login_id, int(time.time())),
            ).fetchone()
        return BrowserLogin(_account(row), login_id, row["expires_at"]) if row else None

    def revoke_login(self, token: str) -> None:
        if token:
            with self._connect() as conn:
                conn.execute("DELETE FROM browser_logins WHERE token_hash = ?", (_token_hash(token),))
