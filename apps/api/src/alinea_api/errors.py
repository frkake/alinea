"""RFC 9457 (= RFC 7807 後継) Problem Details とアプリ例外(plans/03 §1.4・plans/01 §9.1)。

- 全エラーは `application/problem+json` で返す。
- 独自拡張 `code`(機械判定用スネークケース)と `errors[]`(422 のバリデーション詳細)を持つ。
- `type` は `https://alinea.app/problems/{code のケバブケース}` で決定的に導出する。
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

PROBLEM_BASE_URI = "https://alinea.app/problems"
PROBLEM_CONTENT_TYPE = "application/problem+json"

# code -> (HTTP status, 既定タイトル)。plans/03 §1.4 の共通エラーコード表の逐語。
CODE_TABLE: dict[str, tuple[int, str]] = {
    "bad_request": (400, "リクエストが不正です"),
    "unauthorized": (401, "ログインが必要です"),
    "forbidden": (403, "アクセスできません"),
    "token_scope_exceeded": (403, "この操作は拡張トークンでは実行できません"),
    "origin_mismatch": (403, "リクエスト元を確認できません"),
    "not_found": (404, "見つかりません"),
    "method_not_allowed": (405, "許可されていないメソッドです"),
    "duplicate": (409, "すでに存在します"),
    "conflict": (409, "状態が競合しています"),
    "payload_too_large": (413, "アップロード上限を超えています"),
    "unsupported_media_type": (415, "対応していない形式です"),
    "validation_error": (422, "入力内容に誤りがあります"),
    "rate_limited": (429, "リクエストが多すぎます"),
    "quota_exceeded": (429, "月間クォータを超過しました"),
    "provider_error": (502, "外部サービスの呼び出しに失敗しました"),
    "service_unavailable": (503, "現在利用できません"),
    "internal_error": (500, "サーバー内部エラーが発生しました"),
}

# StarletteHTTPException の status から code を引く(明示 code が無いとき用)。
_STATUS_TO_CODE: dict[int, str] = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    413: "payload_too_large",
    415: "unsupported_media_type",
    422: "validation_error",
    429: "rate_limited",
    502: "provider_error",
    503: "service_unavailable",
}


class ProblemError(BaseModel):
    field: str
    message: str


class Problem(BaseModel):
    """RFC 9457 Problem Details。OpenAPI にもこの形で載せる。"""

    type: str
    title: str
    status: int
    detail: str | None = None
    instance: str | None = None
    code: str
    errors: list[ProblemError] = Field(default_factory=list)


class ProblemException(Exception):  # noqa: N818 — 例外だが Problem 変換前提のため命名を保持
    """アプリ内でどこからでも投げられる Problem 例外。ハンドラが Problem に変換する。"""

    def __init__(
        self,
        code: str,
        *,
        detail: str | None = None,
        title: str | None = None,
        status: int | None = None,
        errors: list[ProblemError] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        default_status, default_title = CODE_TABLE.get(code, (400, "リクエストが不正です"))
        self.code = code
        self.status = status if status is not None else default_status
        self.title = title if title is not None else default_title
        self.detail = detail
        self.errors = errors or []
        self.headers = headers or {}
        super().__init__(self.detail or self.title)


def _type_uri(code: str) -> str:
    return f"{PROBLEM_BASE_URI}/{code.replace('_', '-')}"


def build_problem(
    code: str,
    *,
    status: int,
    title: str,
    instance: str | None = None,
    detail: str | None = None,
    errors: list[ProblemError] | None = None,
) -> Problem:
    return Problem(
        type=_type_uri(code),
        title=title,
        status=status,
        detail=detail,
        instance=instance,
        code=code,
        errors=errors or [],
    )


def problem_json_response(
    problem: Problem, *, headers: dict[str, str] | None = None
) -> JSONResponse:
    response = JSONResponse(
        status_code=problem.status,
        content=problem.model_dump(mode="json"),
        media_type=PROBLEM_CONTENT_TYPE,
    )
    if headers:
        for key, value in headers.items():
            response.headers[key] = value
    return response


async def _handle_problem_exception(request: Request, exc: ProblemException) -> JSONResponse:
    problem = build_problem(
        exc.code,
        status=exc.status,
        title=exc.title,
        instance=request.url.path,
        detail=exc.detail,
        errors=exc.errors,
    )
    return problem_json_response(problem, headers=exc.headers)


async def _handle_http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    code = _STATUS_TO_CODE.get(exc.status_code, "bad_request")
    _, default_title = CODE_TABLE.get(code, (exc.status_code, "エラー"))
    detail = exc.detail if isinstance(exc.detail, str) else None
    problem = build_problem(
        code,
        status=exc.status_code,
        title=default_title,
        instance=request.url.path,
        detail=detail,
    )
    headers = dict(exc.headers or {})
    return problem_json_response(problem, headers=headers)


async def _handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    errors: list[ProblemError] = []
    for err in exc.errors():
        location = err.get("loc", ())
        field = ".".join(str(part) for part in location)
        errors.append(ProblemError(field=field, message=str(err.get("msg", ""))))
    problem = build_problem(
        "validation_error",
        status=422,
        title=CODE_TABLE["validation_error"][1],
        instance=request.url.path,
        errors=errors,
    )
    return problem_json_response(problem)


async def _handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    problem = build_problem(
        "internal_error",
        status=500,
        title=CODE_TABLE["internal_error"][1],
        instance=request.url.path,
    )
    return problem_json_response(problem)


def register_exception_handlers(app: FastAPI) -> None:
    """FastAPI にすべての Problem 変換ハンドラを登録する。"""
    app.add_exception_handler(ProblemException, _handle_problem_exception)  # type: ignore[arg-type]
    app.add_exception_handler(StarletteHTTPException, _handle_http_exception)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, _handle_validation_error)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, _handle_unexpected_error)
