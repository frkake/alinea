"""ASGI ミドルウェア: X-Request-Id 付与と CSRF Origin 検証(plans/03 §1.1・§1.3・plans/01 §6.2)。

BaseHTTPMiddleware は長寿命 SSE ストリームでハングし得るため、純 ASGI ミドルウェアとして実装する。
"""

from __future__ import annotations

from urllib.parse import urlsplit

import structlog
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from alinea_api.errors import build_problem, problem_json_response
from alinea_api.ids import new_ulid
from alinea_api.settings import ApiSettings

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

# anonymous 区分の非 GET エンドポイント(Origin 検証を免除)。plans/01 §6.2。
CSRF_EXEMPT_PATHS = frozenset({"/api/auth/email/request"})
CSRF_EXEMPT_PREFIXES = ("/api/share/",)


class RequestIdMiddleware:
    """全レスポンスに `X-Request-Id`(ULID)を付与し、structlog contextvars に束ねる。"""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = new_ulid()
        structlog.contextvars.bind_contextvars(
            request_id=request_id,
            path=scope.get("path", ""),
            method=scope.get("method", ""),
        )

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["X-Request-Id"] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            structlog.contextvars.unbind_contextvars("request_id", "path", "method")


class OriginCsrfMiddleware:
    """非 GET リクエストの `Origin` を許可リストと照合する(専用 CSRF トークンは持たない)。"""

    def __init__(self, app: ASGIApp, settings: ApiSettings) -> None:
        self.app = app
        self.settings = settings

    def _is_allowed_origin(self, origin: str) -> bool:
        if origin in self.settings.allowed_origins:
            return True
        # dev では chrome-extension:// スキームを一律許可(plans/10 §15-2)。
        if not self.settings.is_production and origin.startswith("chrome-extension://"):
            return True
        return False

    @staticmethod
    def _is_request_origin(origin: str, scope: Scope, headers: dict[str, str]) -> bool:
        """ブラウザが見ている同一オリジンを Host / proxy headers から判定する。"""
        parsed = urlsplit(origin)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return False

        forwarded_proto = headers.get("x-forwarded-proto", "").split(",", 1)[0].strip()
        request_scheme = forwarded_proto or str(scope.get("scheme", "http"))
        if parsed.scheme != request_scheme:
            return False

        request_hosts = {headers.get("host", "").strip().lower()}
        forwarded_host = headers.get("x-forwarded-host", "").split(",", 1)[0].strip().lower()
        if forwarded_host:
            request_hosts.add(forwarded_host)
        request_hosts.discard("")
        return parsed.netloc.lower() in request_hosts

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        method = scope.get("method", "GET")
        path = scope.get("path", "")
        if method in SAFE_METHODS or self._is_exempt(path, scope):
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        origin = headers.get("origin", "")
        normalized_origin = origin.rstrip("/")
        if origin and (
            self._is_allowed_origin(normalized_origin)
            or self._is_request_origin(normalized_origin, scope, headers)
        ):
            await self.app(scope, receive, send)
            return
        problem = build_problem(
            "origin_mismatch",
            status=403,
            title="リクエスト元を確認できません",
            instance=path,
            detail="Origin ヘッダが許可されていません。",
        )
        await problem_json_response(problem)(scope, receive, send)

    def _is_exempt(self, path: str, scope: Scope) -> bool:
        # 拡張トークン(Bearer)認証は CSRF 不能なため免除する。
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        auth = headers.get("authorization", "")
        if auth.startswith("Bearer "):
            return True
        if path in CSRF_EXEMPT_PATHS:
            return True
        return any(path.startswith(prefix) for prefix in CSRF_EXEMPT_PREFIXES)
