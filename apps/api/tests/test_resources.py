"""resources API テスト(M2-13 / plans/03 §12・docs/12・plans/09-screens/5a)。

- PY-RES-01: URL 種別判定表(github/youtube/slides/article)・正規化(トラッキングパラメータ除去)。
- PY-RES-02: メタ自動取得(HTTP モック)。kind 別 meta・YouTube サムネ。取得失敗でも
  ``fetch_status=failed``(``meta_fetched=false``)で URL のみ登録完了(P3)。kind の PATCH 変更可。
- PY-RES-03: 公式実装検出。``papers.official_repo_url`` → suggestion 生成 → accept で
  official=true・dismiss で永続的に再提案されない。
- PY-RES-04: note_md 内 §チップ(note_anchors)の保存とアンカー実在検証。
- PY-RES-05: 件数バッジ = status=active の COUNT(suggested/dismissed を数えない)。
- PY-RES-06: 同一 URL(正規化後)二重登録 409。

外部 HTTP は一切発行しない(``httpx.AsyncClient`` をスタブに差し替える。test_oauth.py と同方針)。
DB は実 PostgreSQL(factories 経由)。認証はセッション直発行 + cookie。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import pytest_asyncio
from alinea_api.services.session_service import create_session
from alinea_api.services.user_service import purge_user
from alinea_core.db.models import LibraryItem, Paper, ResourceLink
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession


def _build_app() -> FastAPI:
    """本タスク所有ルータのみをマウントしたアプリ(main.py は article レーンが登録)。"""
    from alinea_api.errors import register_exception_handlers
    from alinea_api.middleware import OriginCsrfMiddleware, RequestIdMiddleware
    from alinea_api.ratelimit import RateLimitMiddleware
    from alinea_api.redis_client import get_redis
    from alinea_api.routers import resources
    from alinea_api.settings import get_api_settings

    s = get_api_settings()
    app = FastAPI()
    register_exception_handlers(app)
    app.add_middleware(OriginCsrfMiddleware, settings=s)
    app.add_middleware(RateLimitMiddleware, redis_factory=get_redis)
    app.add_middleware(RequestIdMiddleware)
    app.include_router(resources.router)
    return app


# ---------------------------------------------------------------------------
# 外部 HTTP スタブ(test_oauth.py の _FakeAsyncClient と同方針)
# ---------------------------------------------------------------------------
class _FakeResponse:
    def __init__(
        self,
        *,
        json_data: Any = None,
        text_data: str | None = None,
        content: bytes | None = None,
        headers: dict[str, str] | None = None,
        status_code: int = 200,
    ) -> None:
        self._json = json_data
        self._text = text_data if text_data is not None else ""
        self._content = content if content is not None else self._text.encode("utf-8")
        self.headers = headers or {}
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("GET", "https://example.test")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError("error", request=request, response=response)

    def json(self) -> Any:
        return self._json

    @property
    def text(self) -> str:
        return self._text

    @property
    def content(self) -> bytes:
        return self._content


class _FakeAsyncClient:
    """URL(クエリ無視)→ ``_FakeResponse``|Exception の辞書で解決するスタブ。"""

    def __init__(self, responses: dict[str, _FakeResponse | Exception]) -> None:
        self._responses = responses

    async def __aenter__(self) -> _FakeAsyncClient:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    async def get(self, url: str, **_kwargs: Any) -> _FakeResponse:
        key = url.split("?", 1)[0]
        if key not in self._responses:
            raise httpx.ConnectError(f"no fake response registered for {url}")
        resp = self._responses[key]
        if isinstance(resp, Exception):
            raise resp
        return resp


def _patch_http(
    monkeypatch: pytest.MonkeyPatch, responses: dict[str, _FakeResponse | Exception] | None = None
) -> None:
    """外部 HTTP を一切発行しない(未登録 URL は ConnectError → meta_fetched=false に落ちる)。"""
    monkeypatch.setattr(httpx, "AsyncClient", lambda *_a, **_kw: _FakeAsyncClient(responses or {}))


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------
@pytest_asyncio.fixture
async def env(
    db_session: AsyncSession, redis_client: Any, factories: Any
) -> AsyncIterator[tuple[AsyncClient, LibraryItem, Paper, str]]:
    """(client, library_item, paper, user_id)。私有 Paper+Revision+LibraryItem を用意する。"""
    user = await factories.make_user(db_session)
    paper = await factories.make_paper(db_session, owner=user, visibility="private")
    await factories.make_revision(db_session, paper=paper)
    item = await factories.make_library_item(db_session, user=user, paper=paper)
    uid = str(user.id)
    await db_session.commit()

    transport = ASGITransport(app=_build_app())
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
        headers={"Origin": "http://localhost:3000"},
        trust_env=False,
    ) as ac:
        token = await create_session(redis_client, uid)
        ac.cookies.set("yk_session", token)
        try:
            yield ac, item, paper, uid
        finally:
            await db_session.rollback()
            await purge_user(db_session, uid)


def _pdf_bytes(*, title: str | None = None) -> bytes:
    import fitz

    doc = fitz.open()
    doc.new_page()
    if title is not None:
        doc.set_metadata({"title": title})
    data: bytes = doc.tobytes()
    doc.close()
    return data


# ===========================================================================
# PY-RES-01: URL 種別判定・正規化(unit。HTTP/DB 不要)
# ===========================================================================
@pytest.mark.parametrize(
    ("url", "expected_kind"),
    [
        ("https://github.com/gnobitab/RectifiedFlow", "github"),
        ("https://github.com/gnobitab/RectifiedFlow/tree/main", "github"),
        ("https://gist.github.com/someone/abc123", "article"),
        ("https://someone.github.io/page", "article"),
        ("https://www.youtube.com/watch?v=abc123", "youtube"),
        ("https://youtu.be/abc123", "youtube"),
        ("https://youtube.com/live/abc123", "youtube"),
        ("https://iclr.cc/slides/deck.pdf", "slides"),
        ("https://speakerdeck.com/someone/talk", "slides"),
        ("https://www.slideshare.net/someone/talk", "slides"),
        ("https://zenn.dev/some/articles/xyz", "article"),
        ("https://example.com/blog/post", "article"),
    ],
)
def test_classify_kind_table(url: str, expected_kind: str) -> None:
    from alinea_api.routers.resources import classify_kind

    kind, _gh = classify_kind(url)
    assert kind == expected_kind


def test_classify_kind_github_extracts_owner_repo_and_strips_git_suffix() -> None:
    from alinea_api.routers.resources import classify_kind

    kind, gh = classify_kind("https://github.com/gnobitab/RectifiedFlow.git")
    assert kind == "github"
    assert gh == ("gnobitab", "RectifiedFlow")


def test_youtube_video_id_watch_and_short_forms() -> None:
    from alinea_api.routers.resources import youtube_video_id

    assert youtube_video_id("https://www.youtube.com/watch?v=abc123&t=5s") == "abc123"
    assert youtube_video_id("https://youtu.be/abc123") == "abc123"
    assert youtube_video_id("https://youtube.com/live/abc123") == "abc123"
    assert youtube_video_id("https://example.com/watch?v=abc123") is None


def test_normalize_url_strips_tracking_params_lowercases_host_and_www() -> None:
    from alinea_api.routers.resources import normalize_url

    a = normalize_url("https://WWW.Example.com/Path/?utm_source=newsletter&b=2&a=1")
    b = normalize_url("https://example.com/Path?a=1&b=2")
    assert a == b


def test_normalize_url_preserves_case_sensitive_path_and_query() -> None:
    from alinea_api.routers.resources import normalize_url

    assert normalize_url("https://github.com/Owner/Repo") == "https://github.com/Owner/Repo"
    # 動画 ID は大小区別を保つ。
    assert "AbC123" in normalize_url("https://youtu.be/AbC123")


# ===========================================================================
# PY-RES-02: メタ自動取得(kind 別)・取得失敗時の成立・kind の PATCH 変更
# ===========================================================================
async def test_create_github_resource_fetches_meta(
    env: tuple[AsyncClient, LibraryItem, Paper, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, item, _paper, _uid = env
    _patch_http(
        monkeypatch,
        {
            "https://api.github.com/repos/gnobitab/RectifiedFlow": _FakeResponse(
                json_data={
                    "language": "Python",
                    "stargazers_count": 1234,
                    "pushed_at": "2023-11-15T00:00:00Z",
                }
            )
        },
    )
    r = await client.post(
        f"/api/library-items/{item.id}/resources",
        json={"url": "https://github.com/gnobitab/RectifiedFlow"},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["kind"] == "github"
    assert body["title"] == "gnobitab/RectifiedFlow"
    assert body["source_label"] == "GitHub"
    assert body["meta"] == {
        "language": "Python",
        "stars": 1234,
        "updated_at": "2023-11-15T00:00:00Z",
    }
    assert body["meta_fetched"] is True
    assert body["official"] is False


async def test_create_youtube_resource_fetches_oembed_and_thumbnail(
    env: tuple[AsyncClient, LibraryItem, Paper, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, item, _paper, _uid = env
    _patch_http(
        monkeypatch,
        {
            "https://www.youtube.com/oembed": _FakeResponse(
                json_data={
                    "title": "ICLR 2023 Oral — Flow Straight and Fast",
                    "thumbnail_url": "https://i.ytimg.com/vi/abc123/hqdefault.jpg",
                }
            )
        },
    )
    r = await client.post(
        f"/api/library-items/{item.id}/resources",
        json={"url": "https://www.youtube.com/watch?v=abc123"},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["kind"] == "youtube"
    assert body["title"] == "ICLR 2023 Oral — Flow Straight and Fast"
    assert body["source_label"] == "YouTube"
    assert body["thumbnail_url"] == "https://i.ytimg.com/vi/abc123/hqdefault.jpg"
    # YOUTUBE_API_KEY 未設定(既定)では再生時間を省略する(docs/12 §3.2)。
    assert body["meta"] == {"duration_seconds": None}


async def test_create_youtube_resource_fetches_duration_when_api_key_configured(
    env: tuple[AsyncClient, LibraryItem, Paper, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, item, _paper, _uid = env
    monkeypatch.setenv("YOUTUBE_API_KEY", "test-key")
    _patch_http(
        monkeypatch,
        {
            "https://www.youtube.com/oembed": _FakeResponse(
                json_data={"title": "Talk", "thumbnail_url": None}
            ),
            "https://www.googleapis.com/youtube/v3/videos": _FakeResponse(
                json_data={"items": [{"contentDetails": {"duration": "PT12M34S"}}]}
            ),
        },
    )
    r = await client.post(
        f"/api/library-items/{item.id}/resources",
        json={"url": "https://youtu.be/xyz789"},
    )
    assert r.status_code == 201
    assert r.json()["meta"] == {"duration_seconds": 754}


async def test_create_slides_resource_counts_pdf_pages(
    env: tuple[AsyncClient, LibraryItem, Paper, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, item, _paper, _uid = env
    pdf_bytes = _pdf_bytes(title="発表スライド")
    _patch_http(
        monkeypatch,
        {"https://iclr.cc/slides/deck.pdf": _FakeResponse(content=pdf_bytes)},
    )
    r = await client.post(
        f"/api/library-items/{item.id}/resources",
        json={"url": "https://iclr.cc/slides/deck.pdf"},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["kind"] == "slides"
    assert body["title"] == "発表スライド"
    assert body["meta"] == {"format": "pdf", "pages": 1}


async def test_create_article_resource_parses_og_tags_and_reading_minutes(
    env: tuple[AsyncClient, LibraryItem, Paper, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, item, _paper, _uid = env
    html = (
        "<html><head>"
        '<meta property="og:title" content="Rectified Flow を図で理解する">'
        '<meta property="og:site_name" content="zenn.dev">'
        "</head><body>" + ("説明文。" * 200) + "</body></html>"
    )
    _patch_http(
        monkeypatch,
        {
            "https://zenn.dev/some/articles/xyz": _FakeResponse(
                text_data=html, headers={"content-type": "text/html; charset=utf-8"}
            )
        },
    )
    r = await client.post(
        f"/api/library-items/{item.id}/resources",
        json={"url": "https://zenn.dev/some/articles/xyz"},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["kind"] == "article"
    assert body["title"] == "Rectified Flow を図で理解する"
    assert body["source_label"] == "zenn.dev"
    assert body["meta"]["reading_minutes"] is not None
    assert body["meta"]["reading_minutes"] > 0


async def test_create_article_reclassified_as_slides_when_content_type_is_pdf(
    env: tuple[AsyncClient, LibraryItem, Paper, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, item, _paper, _uid = env
    pdf_bytes = _pdf_bytes()
    _patch_http(
        monkeypatch,
        {
            "https://example.com/whitepaper": _FakeResponse(
                content=pdf_bytes, headers={"content-type": "application/pdf"}
            )
        },
    )
    r = await client.post(
        f"/api/library-items/{item.id}/resources",
        json={"url": "https://example.com/whitepaper"},
    )
    assert r.status_code == 201
    assert r.json()["kind"] == "slides"


async def test_create_resource_meta_fetch_failure_still_completes(
    env: tuple[AsyncClient, LibraryItem, Paper, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """取得失敗でも URL のみで登録完了する(P3)。title はスキーム除去した URL。"""
    client, item, _paper, _uid = env
    _patch_http(monkeypatch, {})  # 未登録 URL は ConnectError
    r = await client.post(
        f"/api/library-items/{item.id}/resources",
        json={"url": "https://example.com/unreachable-article"},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["meta_fetched"] is False
    assert body["meta"] == {}
    assert body["title"] == "example.com/unreachable-article"


async def test_patch_resource_kind_can_be_changed_manually(
    env: tuple[AsyncClient, LibraryItem, Paper, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, item, _paper, _uid = env
    _patch_http(monkeypatch, {})
    created = await client.post(
        f"/api/library-items/{item.id}/resources",
        json={"url": "https://example.com/misclassified"},
    )
    resource_id = created.json()["id"]
    assert created.json()["kind"] == "article"

    r = await client.patch(f"/api/resources/{resource_id}", json={"kind": "slides"})
    assert r.status_code == 200
    assert r.json()["kind"] == "slides"


async def test_create_resource_rejects_invalid_url(
    env: tuple[AsyncClient, LibraryItem, Paper, str],
) -> None:
    client, item, _paper, _uid = env
    r = await client.post(f"/api/library-items/{item.id}/resources", json={"url": "not a url"})
    assert r.status_code == 422
    assert r.json()["code"] == "validation_error"


async def test_refresh_meta_updates_stored_row(
    env: tuple[AsyncClient, LibraryItem, Paper, str],
    monkeypatch: pytest.MonkeyPatch,
    db_session: AsyncSession,
) -> None:
    client, item, _paper, _uid = env
    _patch_http(monkeypatch, {})
    created = await client.post(
        f"/api/library-items/{item.id}/resources",
        json={"url": "https://github.com/gnobitab/RectifiedFlow"},
    )
    resource_id = created.json()["id"]
    assert created.json()["meta_fetched"] is False

    _patch_http(
        monkeypatch,
        {
            "https://api.github.com/repos/gnobitab/RectifiedFlow": _FakeResponse(
                json_data={"language": "Python", "stargazers_count": 5, "pushed_at": "2024-01-01"}
            )
        },
    )
    r = await client.post(f"/api/resources/{resource_id}/refresh-meta")
    assert r.status_code == 200
    body = r.json()
    assert body["meta_fetched"] is True
    assert body["meta"]["stars"] == 5


# ===========================================================================
# PY-RES-03: 公式実装検出(suggestion → accept/dismiss、永続)
# ===========================================================================
async def test_suggestion_derived_from_paper_official_repo_url(
    env: tuple[AsyncClient, LibraryItem, Paper, str], db_session: AsyncSession
) -> None:
    client, item, paper, _uid = env
    paper.official_repo_url = "https://github.com/gnobitab/RectifiedFlow"
    await db_session.commit()

    r = await client.get(f"/api/library-items/{item.id}/resources")
    assert r.status_code == 200
    body = r.json()
    assert body["suggestion"] == {
        "url": "https://github.com/gnobitab/RectifiedFlow",
        "detected_from": "arxiv_page",
    }
    assert body["count"] == 0  # 提案は件数に数えない


async def test_accept_suggestion_creates_official_resource_and_clears_suggestion(
    env: tuple[AsyncClient, LibraryItem, Paper, str],
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, item, paper, _uid = env
    paper.official_repo_url = "https://github.com/gnobitab/RectifiedFlow"
    await db_session.commit()
    _patch_http(monkeypatch, {})

    r = await client.post(f"/api/library-items/{item.id}/resource-suggestion/accept")
    assert r.status_code == 201
    body = r.json()
    assert body["official"] is True
    assert body["kind"] == "github"

    listing = (await client.get(f"/api/library-items/{item.id}/resources")).json()
    assert listing["suggestion"] is None
    assert listing["count"] == 1
    assert listing["items"][0]["official"] is True


async def test_dismiss_suggestion_is_permanent(
    env: tuple[AsyncClient, LibraryItem, Paper, str], db_session: AsyncSession
) -> None:
    client, item, paper, _uid = env
    paper.official_repo_url = "https://github.com/gnobitab/RectifiedFlow"
    await db_session.commit()

    r = await client.post(f"/api/library-items/{item.id}/resource-suggestion/dismiss")
    assert r.status_code == 204

    listing = (await client.get(f"/api/library-items/{item.id}/resources")).json()
    assert listing["suggestion"] is None
    assert listing["count"] == 0

    # 再取り込み相当(official_repo_url は不変)でも再提案されない。
    listing_again = (await client.get(f"/api/library-items/{item.id}/resources")).json()
    assert listing_again["suggestion"] is None

    # 却下済みの候補への accept/dismiss はもう存在しないため 404。
    again = await client.post(f"/api/library-items/{item.id}/resource-suggestion/accept")
    assert again.status_code == 404


async def test_suggestion_actions_404_when_no_official_repo_url(
    env: tuple[AsyncClient, LibraryItem, Paper, str],
) -> None:
    client, item, _paper, _uid = env
    assert (
        await client.post(f"/api/library-items/{item.id}/resource-suggestion/accept")
    ).status_code == 404
    assert (
        await client.post(f"/api/library-items/{item.id}/resource-suggestion/dismiss")
    ).status_code == 404


# ===========================================================================
# PY-RES-04: ひとことメモの § チップ保存とアンカー実在検証
# ===========================================================================
async def test_patch_note_with_valid_section_chip_persists_note_anchors(
    env: tuple[AsyncClient, LibraryItem, Paper, str],
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, item, _paper, _uid = env
    _patch_http(monkeypatch, {})
    created = await client.post(
        f"/api/library-items/{item.id}/resources",
        json={"url": "https://github.com/gnobitab/RectifiedFlow"},
    )
    resource_id = created.json()["id"]

    note = "train_reflow.py が [[sec:sec-1|§1]] の手順に対応。"
    r = await client.patch(f"/api/resources/{resource_id}", json={"note": note})
    assert r.status_code == 200
    assert r.json()["note"] == note

    row = await db_session.get(ResourceLink, resource_id)
    assert row is not None
    assert row.note_md == note
    assert row.note_anchors == [{"type": "section", "section_id": "sec-1", "label": "§1"}]


async def test_patch_note_with_unknown_section_chip_is_rejected(
    env: tuple[AsyncClient, LibraryItem, Paper, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, item, _paper, _uid = env
    _patch_http(monkeypatch, {})
    created = await client.post(
        f"/api/library-items/{item.id}/resources",
        json={"url": "https://github.com/gnobitab/RectifiedFlow"},
    )
    resource_id = created.json()["id"]

    r = await client.patch(
        f"/api/resources/{resource_id}", json={"note": "存在しない [[sec:sec-99|§9]] への参照。"}
    )
    assert r.status_code == 422
    assert r.json()["code"] == "validation_error"


async def test_patch_note_whitespace_only_clears_it(
    env: tuple[AsyncClient, LibraryItem, Paper, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, item, _paper, _uid = env
    _patch_http(monkeypatch, {})
    created = await client.post(
        f"/api/library-items/{item.id}/resources",
        json={"url": "https://github.com/gnobitab/RectifiedFlow", "note": "メモ"},
    )
    resource_id = created.json()["id"]
    assert created.json()["note"] == "メモ"

    r = await client.patch(f"/api/resources/{resource_id}", json={"note": "   "})
    assert r.status_code == 200
    assert r.json()["note"] is None


async def test_create_resource_with_note_chip_at_creation_time(
    env: tuple[AsyncClient, LibraryItem, Paper, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, item, _paper, _uid = env
    _patch_http(monkeypatch, {})
    r = await client.post(
        f"/api/library-items/{item.id}/resources",
        json={
            "url": "https://github.com/gnobitab/RectifiedFlow",
            "note": "[[sec:sec-2|§2]] を参照。",
        },
    )
    assert r.status_code == 201
    assert r.json()["note"] == "[[sec:sec-2|§2]] を参照。"


# ===========================================================================
# PY-RES-05: 件数バッジ = status=active の COUNT
# ===========================================================================
async def test_count_excludes_dismissed_resources(
    env: tuple[AsyncClient, LibraryItem, Paper, str],
    db_session: AsyncSession,
    factories: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, item, paper, _uid = env
    _patch_http(monkeypatch, {})
    await client.post(
        f"/api/library-items/{item.id}/resources", json={"url": "https://example.com/a"}
    )
    await client.post(
        f"/api/library-items/{item.id}/resources", json={"url": "https://example.com/b"}
    )
    # 無視済み提案(status=dismissed)は数えない。
    paper.official_repo_url = "https://github.com/gnobitab/RectifiedFlow"
    await db_session.commit()
    await client.post(f"/api/library-items/{item.id}/resource-suggestion/dismiss")

    r = await client.get(f"/api/library-items/{item.id}/resources")
    body = r.json()
    assert body["count"] == 2
    assert len(body["items"]) == 2


# ===========================================================================
# PY-RES-06: 同一 URL(正規化後)二重登録 409
# ===========================================================================
async def test_duplicate_url_returns_409_with_existing_resource_id(
    env: tuple[AsyncClient, LibraryItem, Paper, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, item, _paper, _uid = env
    _patch_http(monkeypatch, {})
    first = await client.post(
        f"/api/library-items/{item.id}/resources",
        json={"url": "https://github.com/gnobitab/RectifiedFlow"},
    )
    assert first.status_code == 201
    resource_id = first.json()["id"]

    dup = await client.post(
        f"/api/library-items/{item.id}/resources",
        json={"url": "https://github.com/gnobitab/RectifiedFlow?utm_source=share"},
    )
    assert dup.status_code == 409
    assert dup.json()["code"] == "duplicate"
    assert dup.json()["existing"]["resource_id"] == resource_id


async def test_different_url_is_not_a_duplicate(
    env: tuple[AsyncClient, LibraryItem, Paper, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, item, _paper, _uid = env
    _patch_http(monkeypatch, {})
    await client.post(
        f"/api/library-items/{item.id}/resources", json={"url": "https://example.com/a"}
    )
    r = await client.post(
        f"/api/library-items/{item.id}/resources", json={"url": "https://example.com/b"}
    )
    assert r.status_code == 201


# ===========================================================================
# 所有チェック・DELETE
# ===========================================================================
async def test_resources_not_visible_for_other_users_item(
    env: tuple[AsyncClient, LibraryItem, Paper, str],
) -> None:
    client, _item, _paper, _uid = env
    r = await client.get(f"/api/library-items/{uuid.uuid4()}/resources")
    assert r.status_code == 404


async def test_delete_resource_removes_it(
    env: tuple[AsyncClient, LibraryItem, Paper, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, item, _paper, _uid = env
    _patch_http(monkeypatch, {})
    created = await client.post(
        f"/api/library-items/{item.id}/resources", json={"url": "https://example.com/a"}
    )
    resource_id = created.json()["id"]

    r = await client.delete(f"/api/resources/{resource_id}")
    assert r.status_code == 204

    listing = (await client.get(f"/api/library-items/{item.id}/resources")).json()
    assert listing["items"] == []
