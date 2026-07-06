"""Redis 固定ウィンドウ・レート制限ミドルウェア(plans/03 §1.8)。

- 超過時 429 `rate_limited` + `Retry-After`。
- 全レスポンスに `X-RateLimit-Limit` / `X-RateLimit-Remaining` / `X-RateLimit-Reset`(epoch 秒)。
純 ASGI 実装(SSE ストリームを阻害しない)。
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from yakudoku_api.errors import build_problem, problem_json_response
from yakudoku_api.ids import sha256_hex
from yakudoku_api.services.session_service import COOKIE_NAME


@dataclass(frozen=True, slots=True)
class RateRule:
    name: str
    limit: int
    window: int  # 秒
    scope: str  # "ip" | "user" | "ip_email"


DEFAULT_RULE = RateRule(name="default", limit=600, window=60, scope="user")


def match_rule(method: str, path: str) -> RateRule:
    if method == "POST" and path == "/api/auth/email/request":
        return RateRule(name="auth_email_request", limit=5, window=600, scope="ip_email")
    if method == "GET" and path.startswith("/api/auth/oauth/") and path.endswith("/start"):
        return RateRule(name="oauth_start", limit=20, window=600, scope="ip")
    return DEFAULT_RULE


def _client_ip(scope: Scope) -> str:
    client = scope.get("client")
    if client and isinstance(client, tuple | list) and client:
        return str(client[0])
    return "unknown"


def _cookies(scope: Scope) -> dict[str, str]:
    headers = dict(scope.get("headers", []))
    raw = headers.get(b"cookie", b"").decode("latin-1")
    cookies: dict[str, str] = {}
    for part in raw.split(";"):
        if "=" in part:
            key, _, value = part.strip().partition("=")
            cookies[key] = value
    return cookies


async def _read_body(receive: Receive) -> bytes:
    chunks: list[bytes] = []
    while True:
        message = await receive()
        if message["type"] == "http.request":
            chunks.append(message.get("body", b""))
            if not message.get("more_body", False):
                break
        elif message["type"] == "http.disconnect":
            break
    return b"".join(chunks)


def _replay_receive(body: bytes) -> Receive:
    sent = False

    async def receive() -> Message:
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        return {"type": "http.disconnect"}

    return receive


class RateLimitMiddleware:
    def __init__(self, app: ASGIApp, redis_factory: Callable[[], Any]) -> None:
        self.app = app
        self.redis_factory = redis_factory

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "GET")
        path = scope.get("path", "")
        rule = match_rule(method, path)

        # ip_email スコープはボディの email が必要。読み取り後に再生 receive を渡す。
        if rule.scope == "ip_email":
            body = await _read_body(receive)
            receive = _replay_receive(body)
            identity = self._ip_email_identity(scope, body)
        elif rule.scope == "user":
            identity = self._user_identity(scope)
        else:
            identity = _client_ip(scope)

        r = self.redis_factory()
        now = int(time.time())
        window_start = now - (now % rule.window)
        reset = window_start + rule.window
        key = f"rl:{rule.name}:{identity}:{window_start}"
        count = int(await r.incr(key))
        if count == 1:
            await r.expire(key, rule.window)
        remaining = max(0, rule.limit - count)
        limit_headers = {
            "X-RateLimit-Limit": str(rule.limit),
            "X-RateLimit-Remaining": str(remaining),
            "X-RateLimit-Reset": str(reset),
        }

        if count > rule.limit:
            retry_after = max(1, reset - now)
            headers = {**limit_headers, "Retry-After": str(retry_after)}
            problem = build_problem(
                "rate_limited",
                status=429,
                title="リクエストが多すぎます",
                instance=path,
                detail="レート制限を超過しました。しばらくしてから再試行してください。",
            )
            await problem_json_response(problem, headers=headers)(scope, receive, send)
            return

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                response_headers = MutableHeaders(scope=message)
                for key_name, value in limit_headers.items():
                    response_headers[key_name] = value
            await send(message)

        await self.app(scope, receive, send_wrapper)

    def _user_identity(self, scope: Scope) -> str:
        token = _cookies(scope).get(COOKIE_NAME, "")
        if token:
            return f"sess:{sha256_hex(token)}"
        return f"ip:{_client_ip(scope)}"

    def _ip_email_identity(self, scope: Scope, body: bytes) -> str:
        email = ""
        try:
            data = json.loads(body or b"{}")
            if isinstance(data, dict):
                email = str(data.get("email", "")).strip().lower()
        except (ValueError, json.JSONDecodeError):
            email = ""
        return f"{_client_ip(scope)}:{email}"
