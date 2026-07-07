"""PY-ING-04: POST /api/ingest/pdf(拡張からの PDF 直接送信の受け口。M1-18。plans/03 §3.3)。

- private Paper 作成(visibility=private・owner_user_id・pdf_sha256・license=unknown)
- 同一ユーザー・同一 SHA-256 は 409 duplicate
- 50MB 超は 413 payload_too_large
- 非 PDF(先頭 5 バイトが `%PDF-` でない)は 415 unsupported_media_type
- テキストレイヤ無し PDF はジョブ側で failed(parsing, "テキストが抽出できません")(docs/02 §3)

外部 S3・arq は ``app.dependency_overrides`` で決定的に差し替える(実通信なし)。
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator
from typing import Any

import fitz
import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from yakudoku_api.main import app
from yakudoku_api.routers.ingest import get_job_wakeup, get_pdf_storage
from yakudoku_api.services.session_service import create_session
from yakudoku_api.services.user_service import upsert_user_by_email
from yakudoku_core.db.models import Job, LibraryItem, Paper, User


# ---------------------------------------------------------------------------
# フィクスチャ
# ---------------------------------------------------------------------------
async def _login(client: AsyncClient, db: AsyncSession, redis: Any, email: str) -> User:
    user = await upsert_user_by_email(db, email, provider="email")
    token = await create_session(redis, user.id)
    client.cookies.set("yk_session", token)
    return user


@pytest_asyncio.fixture(autouse=True)
async def _stub_wakeup() -> AsyncIterator[list[str]]:
    """arq 起床通知を no-op に差し替える(実プールを作らない)。呼ばれた job_id を記録する。"""
    calls: list[str] = []

    async def _noop(job_id: str) -> None:
        calls.append(job_id)

    app.dependency_overrides[get_job_wakeup] = lambda: _noop
    yield calls
    app.dependency_overrides.pop(get_job_wakeup, None)


@pytest.fixture
def wakeups(_stub_wakeup: list[str]) -> list[str]:
    return _stub_wakeup


class _FakeStorage:
    """S3Storage の決定的フェイク(put を記録するだけ)。"""

    sources_bucket = "sources"
    assets_bucket = "assets"

    def __init__(self) -> None:
        self.puts: list[tuple[str, str, bytes, str]] = []

    async def put(
        self,
        bucket: str,
        key: str,
        body: bytes,
        *,
        content_type: str = "application/octet-stream",
        metadata: dict[str, str] | None = None,
    ) -> None:
        self.puts.append((bucket, key, body, content_type))


@pytest.fixture
def fake_storage() -> Iterator[_FakeStorage]:
    storage = _FakeStorage()
    app.dependency_overrides[get_pdf_storage] = lambda: storage
    yield storage
    app.dependency_overrides.pop(get_pdf_storage, None)


@pytest_asyncio.fixture
async def created_papers(db_session: AsyncSession) -> AsyncIterator[list[str]]:
    """テストが作った papers を id で掃除する(cascade で library_items / jobs / assets も消える)。"""
    ids: list[str] = []
    yield ids
    if ids:
        await db_session.rollback()
        await db_session.execute(text("DELETE FROM papers WHERE id = ANY(:ids)"), {"ids": ids})
        await db_session.commit()


# ---------------------------------------------------------------------------
# PDF バイト列ヘルパ(外部フィクスチャファイルに依存しない。自己完結)
# ---------------------------------------------------------------------------
def _make_pdf_bytes(*, pages: int = 1) -> bytes:
    doc = fitz.open()
    for i in range(pages):
        page = doc.new_page(width=612, height=792)
        page.insert_text((72, 80), f"Sample Paper Page {i + 1}", fontsize=16)
        page.insert_text((72, 110), "1 Introduction", fontsize=13)
        page.insert_text(
            (72, 130), "This is a short paragraph of body text used for testing today.", fontsize=10
        )
        page.insert_text(
            (72, 145),
            "It has a second line so the sample is long enough for extraction.",
            fontsize=10,
        )
    data = bytes(doc.tobytes())
    doc.close()
    return data


def _make_no_text_layer_pdf() -> bytes:
    doc = fitz.open()
    page = doc.new_page(width=300, height=400)
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 100, 100))
    pix.set_rect(pix.irect, (10, 10, 10))
    page.insert_image(fitz.Rect(10, 10, 290, 390), pixmap=pix)
    data = bytes(doc.tobytes())
    doc.close()
    return data


def _files(data: bytes, filename: str = "paper.pdf") -> dict[str, tuple[str, bytes, str]]:
    return {"file": (filename, data, "application/pdf")}


def _meta(**kwargs: Any) -> dict[str, str]:
    payload = {"source_url": "https://example.com/paper.pdf", "title_guess": None, **kwargs}
    return {"meta": json.dumps(payload)}


# ---------------------------------------------------------------------------
# 正常系: private Paper 作成 + ジョブ投入
# ---------------------------------------------------------------------------
async def test_pdf_ingest_creates_private_paper_and_job(
    client: AsyncClient,
    db_session: AsyncSession,
    redis_client: Any,
    unique_email: str,
    created_papers: list[str],
    wakeups: list[str],
    fake_storage: _FakeStorage,
) -> None:
    user = await _login(client, db_session, redis_client, unique_email)
    data = _make_pdf_bytes()

    r = await client.post(
        "/api/ingest/pdf",
        files=_files(data),
        data=_meta(title_guess="My Uploaded Paper", tags=["distillation"], quick_note="読む"),
    )
    assert r.status_code == 202
    body = r.json()
    created_papers.append(body["paper_id"])
    assert body["duplicate"] is False
    assert body["job_id"] in wakeups

    paper = await db_session.get(Paper, body["paper_id"])
    assert paper is not None
    assert paper.visibility == "private"
    assert str(paper.owner_user_id) == str(user.id)
    assert paper.license == "unknown"
    assert paper.title == "My Uploaded Paper"
    assert paper.pdf_sha256 is not None and len(paper.pdf_sha256) == 64

    item = await db_session.get(LibraryItem, body["library_item_id"])
    assert item is not None
    assert item.status == "planned"
    assert "distillation" in item.tags
    assert item.one_line_note == "読む"

    asset = (
        (
            await db_session.execute(
                text(
                    "SELECT kind, storage_key, content_type FROM source_assets WHERE paper_id = :pid"
                ),
                {"pid": body["paper_id"]},
            )
        )
        .mappings()
        .first()
    )
    assert asset is not None
    assert asset["kind"] == "extension_capture"
    assert asset["storage_key"] == f"sources/{body['paper_id']}/v1/original.pdf"
    assert asset["content_type"] == "application/pdf"

    job = await db_session.get(Job, body["job_id"])
    assert job is not None
    assert job.kind == "ingest"
    assert job.status == "queued"
    assert job.payload["source"] == "pdf_upload"
    assert str(job.paper_id) == body["paper_id"]

    assert len(fake_storage.puts) == 1
    bucket, key, put_body, content_type = fake_storage.puts[0]
    assert bucket == "sources"
    assert key == f"sources/{body['paper_id']}/v1/original.pdf"
    assert put_body == data
    assert content_type == "application/pdf"


async def test_pdf_ingest_defaults_title_from_filename_when_no_guess(
    client: AsyncClient,
    db_session: AsyncSession,
    redis_client: Any,
    unique_email: str,
    created_papers: list[str],
    fake_storage: _FakeStorage,
) -> None:
    await _login(client, db_session, redis_client, unique_email)
    data = _make_pdf_bytes()

    r = await client.post(
        "/api/ingest/pdf", files=_files(data, filename="cool-research.pdf"), data=_meta()
    )
    assert r.status_code == 202
    body = r.json()
    created_papers.append(body["paper_id"])
    paper = await db_session.get(Paper, body["paper_id"])
    assert paper is not None
    assert paper.title == "cool-research"


# ---------------------------------------------------------------------------
# 重複検知(同一ユーザー・同一 SHA-256 → 409)
# ---------------------------------------------------------------------------
async def test_pdf_ingest_duplicate_sha256_same_user_returns_409(
    client: AsyncClient,
    db_session: AsyncSession,
    redis_client: Any,
    unique_email: str,
    created_papers: list[str],
    fake_storage: _FakeStorage,
) -> None:
    await _login(client, db_session, redis_client, unique_email)
    data = _make_pdf_bytes()

    first = await client.post("/api/ingest/pdf", files=_files(data), data=_meta())
    assert first.status_code == 202
    created_papers.append(first.json()["paper_id"])

    second = await client.post("/api/ingest/pdf", files=_files(data), data=_meta())
    assert second.status_code == 409
    problem = second.json()
    assert problem["code"] == "duplicate"
    assert problem["existing"]["library_item_id"] == first.json()["library_item_id"]


async def test_pdf_ingest_different_users_same_pdf_not_duplicate(
    client: AsyncClient,
    bare_client: AsyncClient,
    db_session: AsyncSession,
    redis_client: Any,
    unique_email: str,
    created_papers: list[str],
    fake_storage: _FakeStorage,
) -> None:
    await _login(client, db_session, redis_client, unique_email)
    data = _make_pdf_bytes()
    first = await client.post("/api/ingest/pdf", files=_files(data), data=_meta())
    assert first.status_code == 202
    created_papers.append(first.json()["paper_id"])

    other_email = f"other-{unique_email}"
    await _login(bare_client, db_session, redis_client, other_email)
    second = await bare_client.post("/api/ingest/pdf", files=_files(data), data=_meta())
    assert second.status_code == 202
    created_papers.append(second.json()["paper_id"])
    assert second.json()["paper_id"] != first.json()["paper_id"]


# ---------------------------------------------------------------------------
# 413 payload_too_large
# ---------------------------------------------------------------------------
async def test_pdf_ingest_over_50mb_returns_413(
    client: AsyncClient,
    db_session: AsyncSession,
    redis_client: Any,
    unique_email: str,
    fake_storage: _FakeStorage,
) -> None:
    await _login(client, db_session, redis_client, unique_email)
    oversized = b"%PDF-1.4\n" + b"0" * (51 * 1024 * 1024)

    r = await client.post("/api/ingest/pdf", files=_files(oversized), data=_meta())
    assert r.status_code == 413
    assert r.json()["code"] == "payload_too_large"


# ---------------------------------------------------------------------------
# 415 unsupported_media_type
# ---------------------------------------------------------------------------
async def test_pdf_ingest_non_pdf_returns_415(
    client: AsyncClient,
    db_session: AsyncSession,
    redis_client: Any,
    unique_email: str,
    fake_storage: _FakeStorage,
) -> None:
    await _login(client, db_session, redis_client, unique_email)
    r = await client.post(
        "/api/ingest/pdf",
        files=_files(b"this is not a pdf file at all", filename="notes.txt"),
        data=_meta(),
    )
    assert r.status_code == 415
    assert r.json()["code"] == "unsupported_media_type"


# ---------------------------------------------------------------------------
# テキストレイヤ無し PDF はジョブ側で failed(parsing, ...)
# ---------------------------------------------------------------------------
async def test_pdf_ingest_no_text_layer_marks_job_failed(
    client: AsyncClient,
    db_session: AsyncSession,
    redis_client: Any,
    unique_email: str,
    created_papers: list[str],
    fake_storage: _FakeStorage,
) -> None:
    await _login(client, db_session, redis_client, unique_email)
    data = _make_no_text_layer_pdf()

    r = await client.post("/api/ingest/pdf", files=_files(data), data=_meta())
    assert r.status_code == 202  # 受け口自体は成功(ジョブ側の失敗として記録)。
    body = r.json()
    created_papers.append(body["paper_id"])

    job = await db_session.get(Job, body["job_id"])
    assert job is not None
    assert job.status == "failed"
    assert job.stage == "parsing"
    error = json.loads(job.error or "{}")
    assert error["code"] == "no_text_layer"
    assert error["message"] == "テキストが抽出できません"
    assert job.log and job.log[-1]["message"] == "テキストが抽出できません"

    paper = await db_session.get(Paper, body["paper_id"])
    assert paper is not None
    assert paper.visibility == "private"


# ---------------------------------------------------------------------------
# 422: meta が不正な JSON / 不正な status
# ---------------------------------------------------------------------------
async def test_pdf_ingest_invalid_meta_json_returns_422(
    client: AsyncClient,
    db_session: AsyncSession,
    redis_client: Any,
    unique_email: str,
    fake_storage: _FakeStorage,
) -> None:
    await _login(client, db_session, redis_client, unique_email)
    r = await client.post(
        "/api/ingest/pdf", files=_files(_make_pdf_bytes()), data={"meta": "{not json"}
    )
    assert r.status_code == 422


async def test_pdf_ingest_invalid_status_returns_422(
    client: AsyncClient,
    db_session: AsyncSession,
    redis_client: Any,
    unique_email: str,
    fake_storage: _FakeStorage,
) -> None:
    await _login(client, db_session, redis_client, unique_email)
    r = await client.post(
        "/api/ingest/pdf", files=_files(_make_pdf_bytes()), data=_meta(status="not-a-status")
    )
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# Idempotency-Key: 同一キー再送は初回レスポンスを再生
# ---------------------------------------------------------------------------
async def test_pdf_ingest_idempotency_key_replays_first_response(
    client: AsyncClient,
    db_session: AsyncSession,
    redis_client: Any,
    unique_email: str,
    created_papers: list[str],
    fake_storage: _FakeStorage,
) -> None:
    await _login(client, db_session, redis_client, unique_email)
    key = "idem-pdf-test-key"
    data = _make_pdf_bytes()

    first = await client.post(
        "/api/ingest/pdf",
        files=_files(data),
        data=_meta(),
        headers={"Idempotency-Key": key},
    )
    assert first.status_code == 202
    created_papers.append(first.json()["paper_id"])

    second = await client.post(
        "/api/ingest/pdf",
        files=_files(_make_pdf_bytes(pages=2)),  # 内容が違っても再送とみなして初回を再生
        data=_meta(),
        headers={"Idempotency-Key": key},
    )
    assert second.status_code == 202
    assert second.json() == first.json()
    # ストレージへの PUT は初回の 1 回だけ(再送は本体を読む前に短絡する)。
    assert len(fake_storage.puts) == 1
