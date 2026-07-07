"""``jobs.kind='resource_meta'`` ハンドラのテスト(M2-13 / docs/12 §3・plans/02)。

- kind 別メタ取得(github/youtube/slides/article)を HTTP スタブで検証する。
- 取得失敗でもジョブは ``succeeded`` になり、対象 ``ResourceLink`` は
  ``fetch_status='failed'``(P3。取得失敗で処理自体を失敗させない)。
- ``kind='article'`` の取得結果が PDF だった場合に ``slides`` へ再分類する。

DB は実 PostgreSQL。外部 HTTP は一切発行しない(``httpx.AsyncClient`` をスタブに差し替える)。
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from yakudoku_core.db.models import LibraryItem, Paper, User
from yakudoku_core.db.models import ResourceLink as ResourceLinkModel
from yakudoku_core.jobs.store import JobStore
from yakudoku_worker.tasks.fetch_resource_meta import (
    classify_kind,
    run_fetch_resource_meta_job,
    youtube_video_id,
)


# ---------------------------------------------------------------------------
# 外部 HTTP スタブ(apps/api/tests/test_resources.py と同方針)
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
    monkeypatch.setattr(httpx, "AsyncClient", lambda *_a, **_kw: _FakeAsyncClient(responses or {}))


def _pdf_bytes(*, title: str | None = None) -> bytes:
    import fitz

    doc = fitz.open()
    doc.new_page()
    if title is not None:
        doc.set_metadata({"title": title})
    data: bytes = doc.tobytes()
    doc.close()
    return data


async def _seed_resource_link(
    db: AsyncSession, *, url: str, kind: str = "article"
) -> ResourceLinkModel:
    user = User(id=str(uuid.uuid4()), email=f"{uuid.uuid4().hex}@t.test")
    db.add(user)
    await db.flush()
    paper = Paper(
        id=str(uuid.uuid4()), title="Test Paper", owner_user_id=user.id, visibility="private"
    )
    db.add(paper)
    await db.flush()
    item = LibraryItem(id=str(uuid.uuid4()), user_id=user.id, paper_id=paper.id)
    db.add(item)
    await db.flush()
    link = ResourceLinkModel(
        id=str(uuid.uuid4()),
        library_item_id=item.id,
        status="active",
        kind=kind,
        url=url,
        url_normalized=url,
        fetch_status="pending",
    )
    db.add(link)
    await db.flush()
    await db.commit()
    return link


async def _run_job_for(db: AsyncSession, link: ResourceLinkModel) -> Any:
    store = JobStore(db)
    job_id = await store.enqueue(kind="resource_meta", payload={"resource_link_id": link.id})
    job = await store.claim(job_id)
    assert job is not None
    await run_fetch_resource_meta_job({}, store, job)
    return await store.get(job_id)


# ===========================================================================
# URL 判定(apps/api/routers/resources.py と同一規則。複製の健全性を確認)
# ===========================================================================
def test_classify_kind_matches_api_router_rules() -> None:
    assert classify_kind("https://github.com/gnobitab/RectifiedFlow") == (
        "github",
        ("gnobitab", "RectifiedFlow"),
    )
    assert classify_kind("https://www.youtube.com/watch?v=abc123") == ("youtube", None)
    assert classify_kind("https://speakerdeck.com/x/y") == ("slides", None)
    assert classify_kind("https://example.com/post") == ("article", None)


def test_youtube_video_id_extraction() -> None:
    assert youtube_video_id("https://youtu.be/abc123") == "abc123"
    assert youtube_video_id("https://www.youtube.com/watch?v=abc123") == "abc123"


# ===========================================================================
# ジョブ本体: kind 別メタ取得
# ===========================================================================
async def test_github_job_updates_meta_and_succeeds(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    link = await _seed_resource_link(
        db_session, url="https://github.com/gnobitab/RectifiedFlow", kind="github"
    )
    _patch_http(
        monkeypatch,
        {
            "https://api.github.com/repos/gnobitab/RectifiedFlow": _FakeResponse(
                json_data={
                    "language": "Python",
                    "stargazers_count": 1200,
                    "pushed_at": "2023-11-01T00:00:00Z",
                }
            )
        },
    )
    job = await _run_job_for(db_session, link)
    assert job is not None
    assert job.status == "succeeded"

    await db_session.refresh(link)
    assert link.fetch_status == "ok"
    assert link.title == "gnobitab/RectifiedFlow"
    assert link.meta == {"language": "Python", "stars": 1200, "updated_at": "2023-11-01T00:00:00Z"}


async def test_youtube_job_fetches_oembed_thumbnail(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    link = await _seed_resource_link(db_session, url="https://youtu.be/abc123", kind="youtube")
    _patch_http(
        monkeypatch,
        {
            "https://www.youtube.com/oembed": _FakeResponse(
                json_data={"title": "Talk", "thumbnail_url": "https://i.ytimg.com/x.jpg"}
            )
        },
    )
    await _run_job_for(db_session, link)
    await db_session.refresh(link)
    assert link.fetch_status == "ok"
    assert link.title == "Talk"
    assert link.thumbnail_url == "https://i.ytimg.com/x.jpg"
    assert link.meta == {"duration_seconds": None}


async def test_slides_job_counts_pdf_pages_and_reads_title(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    link = await _seed_resource_link(
        db_session, url="https://iclr.cc/slides/deck.pdf", kind="slides"
    )
    _patch_http(
        monkeypatch,
        {
            "https://iclr.cc/slides/deck.pdf": _FakeResponse(
                content=_pdf_bytes(title="発表スライド")
            )
        },
    )
    await _run_job_for(db_session, link)
    await db_session.refresh(link)
    assert link.fetch_status == "ok"
    assert link.title == "発表スライド"
    assert link.meta == {"format": "pdf", "pages": 1}


async def test_article_job_reclassifies_to_slides_on_pdf_content_type(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    link = await _seed_resource_link(
        db_session, url="https://example.com/whitepaper", kind="article"
    )
    _patch_http(
        monkeypatch,
        {
            "https://example.com/whitepaper": _FakeResponse(
                content=_pdf_bytes(), headers={"content-type": "application/pdf"}
            )
        },
    )
    await _run_job_for(db_session, link)
    await db_session.refresh(link)
    assert link.kind == "slides"
    assert link.fetch_status == "ok"


async def test_fetch_failure_still_succeeds_job_with_failed_fetch_status(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """取得失敗でもジョブは succeeded(P3。再取得はユーザー操作起点でリトライしない)。"""
    link = await _seed_resource_link(
        db_session, url="https://example.com/unreachable", kind="article"
    )
    _patch_http(monkeypatch, {})
    job = await _run_job_for(db_session, link)
    assert job is not None
    assert job.status == "succeeded"

    await db_session.refresh(link)
    assert link.fetch_status == "failed"
    assert link.title == "example.com/unreachable"
    assert link.meta == {}


async def test_missing_resource_link_skips_gracefully(db_session: AsyncSession) -> None:
    store = JobStore(db_session)
    job_id = await store.enqueue(
        kind="resource_meta", payload={"resource_link_id": str(uuid.uuid4())}
    )
    job = await store.claim(job_id)
    assert job is not None
    await run_fetch_resource_meta_job({}, store, job)

    finished = await store.get(job_id)
    assert finished is not None
    assert finished.status == "succeeded"
    assert finished.result.get("skipped") is True
