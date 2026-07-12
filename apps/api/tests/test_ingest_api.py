"""ingest / papers / assets API テスト(M0-19。plans/03 §3・§4・§22)。

- PY-ING-01: GET /api/ingest/check の 3 分岐(新規プレビュー / 非対応 / 既存)。
- PY-ING-03: API 経由の既存判定(saved に読書位置・進捗が載る)。
- PY-ING-06: GET /api/papers/{id}/ingest-log(joblog の射影・at 昇順)。
- 併せて POST /api/ingest/arxiv(202・Idempotency-Key・409 duplicate)、recent、reingest、
  pdf/asset 配信(302)を検証する。

認証はメールユーザー + セッション Cookie を張って行う(conftest の client を再利用)。
外部 arXiv・arq・S3 は app.dependency_overrides で決定的に差し替える(実通信なし)。
"""

from __future__ import annotations

import datetime as dt
import random
import uuid
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
import pytest_asyncio
from alinea_api.main import app
from alinea_api.routers.ingest import (
    ArxivGateway,
    _ensure_pdf_placeholder_revision,
    get_arxiv_gateway,
    get_job_wakeup,
    get_pdf_storage,
)
from alinea_api.routers.papers import get_storage
from alinea_api.schemas.assets import encode_asset_id
from alinea_api.services.session_service import create_session
from alinea_api.services.user_service import upsert_user_by_email
from alinea_core.arxiv.fetch import FetchError
from alinea_core.arxiv.ids import ArxivId
from alinea_core.arxiv.metadata import ArxivMeta
from alinea_core.db.models import (
    Article,
    BlockSearchIndex,
    DocumentRevision,
    ExplainerFigure,
    Job,
    LibraryItem,
    Paper,
    SourceAsset,
    TranslationSet,
    User,
)
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

_MINIMAL_PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


# ---------------------------------------------------------------------------
# フィクスチャ
# ---------------------------------------------------------------------------
async def _login(client: AsyncClient, db: AsyncSession, redis: Any, email: str) -> User:
    user = await upsert_user_by_email(db, email, provider="email")
    token = await create_session(redis, user.id)
    client.cookies.set("yk_session", token)
    return user


def _rand_arxiv() -> str:
    return f"23{random.randint(1, 12):02d}.{random.randint(10000, 99999)}"


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


class _FakeGateway(ArxivGateway):
    async def fetch_metadata(self, ref: ArxivId) -> ArxivMeta:
        return ArxivMeta(
            arxiv_id=ref.id,
            title=f"Mock Paper for {ref.id}",
            authors=[{"name": "Xingchang Liu"}, {"name": "Chengyue Gong"}, {"name": "Qiang Liu"}],
            abstract="A deterministic mock abstract.",
            published_on="2022-09-07",
            arxiv_categories=["cs.LG", "stat.ML"],
            doi=None,
            venue="ICLR 2023",
            latest_version="v3",
            license="cc-by-4.0",
        )

    async def probe_latex_available(self, ref: ArxivId) -> bool:
        return True

    async def fetch_pdf(self, ref: ArxivId, settings: Any) -> bytes:
        return _MINIMAL_PDF


@pytest.fixture
def seed_arxiv_mock() -> Iterator[None]:
    app.dependency_overrides[get_arxiv_gateway] = lambda: _FakeGateway()
    yield
    app.dependency_overrides.pop(get_arxiv_gateway, None)


@pytest.fixture
def fake_storage() -> Iterator[Any]:
    class _FakeStorage:
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

        async def presign_get(self, bucket: str, key: str, expires_in: int = 600) -> str:
            return f"https://signed.example/{bucket}/{key}?exp={expires_in}"

        async def get(self, bucket: str, key: str) -> bytes:
            return _MINIMAL_PDF

    storage = _FakeStorage()
    app.dependency_overrides[get_storage] = lambda: storage
    app.dependency_overrides[get_pdf_storage] = lambda: storage
    yield storage
    app.dependency_overrides.pop(get_storage, None)
    app.dependency_overrides.pop(get_pdf_storage, None)


@pytest_asyncio.fixture
async def created_papers(db_session: AsyncSession) -> AsyncIterator[list[str]]:
    """テストが作った papers を id で掃除する(cascade で library_items / jobs も消える)。"""
    ids: list[str] = []
    yield ids
    if ids:
        await db_session.rollback()
        await db_session.execute(text("DELETE FROM papers WHERE id = ANY(:ids)"), {"ids": ids})
        await db_session.commit()


# ---------------------------------------------------------------------------
# PY-ING-01: check の 3 分岐
# ---------------------------------------------------------------------------
async def test_check_new_returns_preview(
    client: AsyncClient,
    db_session: AsyncSession,
    redis_client: Any,
    unique_email: str,
    seed_arxiv_mock: None,
) -> None:
    await _login(client, db_session, redis_client, unique_email)
    aid = _rand_arxiv()
    r = await client.get("/api/ingest/check", params={"url": f"https://arxiv.org/abs/{aid}"})
    assert r.status_code == 200
    body = r.json()
    assert body["kind"] == "arxiv"
    assert body["arxiv_id"] == aid
    assert body["arxiv_version"] is None
    assert body["latex_available"] is True
    assert body["saved"] is None
    assert body["bib"]["title"].startswith("Mock Paper")
    assert body["bib"]["authors_short"] == "Liu, Gong, Liu"
    assert body["bib"]["year"] == 2022
    assert body["suggested_tags"] == ["cs.LG", "stat.ML"]


async def test_check_unsupported_url(
    client: AsyncClient, db_session: AsyncSession, redis_client: Any, unique_email: str
) -> None:
    await _login(client, db_session, redis_client, unique_email)
    r = await client.get("/api/ingest/check", params={"url": "https://example.com/not-a-paper"})
    assert r.status_code == 200
    body = r.json()
    assert body["kind"] == "unsupported"
    assert body["arxiv_id"] is None
    assert body["bib"] is None
    assert body["latex_available"] is None
    assert body["saved"] is None


async def test_check_existing_returns_saved(
    client: AsyncClient,
    db_session: AsyncSession,
    redis_client: Any,
    unique_email: str,
    created_papers: list[str],
) -> None:
    user = await _login(client, db_session, redis_client, unique_email)
    aid = _rand_arxiv()
    paper = Paper(arxiv_id=aid, title="Existing Paper", visibility="public")
    db_session.add(paper)
    await db_session.flush()
    created_papers.append(paper.id)
    db_session.add(LibraryItem(user_id=user.id, paper_id=paper.id, status="reading"))
    await db_session.commit()

    r = await client.get("/api/ingest/check", params={"url": f"arXiv:{aid}v2"})
    assert r.status_code == 200
    body = r.json()
    assert body["kind"] == "arxiv"
    assert body["arxiv_version"] == "v2"
    assert body["saved"] is not None
    assert body["saved"]["status"] == "reading"


async def test_check_existing_projects_waiting_input_ingest_pipeline(
    client: AsyncClient,
    db_session: AsyncSession,
    redis_client: Any,
    unique_email: str,
    created_papers: list[str],
) -> None:
    user = await _login(client, db_session, redis_client, unique_email)
    aid = _rand_arxiv()
    paper = Paper(arxiv_id=aid, title="Long paper", visibility="public")
    db_session.add(paper)
    await db_session.flush()
    created_papers.append(str(paper.id))
    item = LibraryItem(user_id=user.id, paper_id=paper.id, status="reading")
    db_session.add(item)
    await db_session.flush()
    db_session.add(
        Job(
            kind="ingest",
            paper_id=paper.id,
            library_item_id=item.id,
            user_id=user.id,
            status="waiting_input",
            stage="selecting_sections",
            progress=45,
        )
    )
    await db_session.commit()

    response = await client.get("/api/ingest/check", params={"url": f"https://arxiv.org/abs/{aid}"})

    assert response.status_code == 200, response.text
    assert response.json()["saved"]["pipeline"] == {
        "job_id": response.json()["saved"]["pipeline"]["job_id"],
        "stage": "selecting_sections",
        "status": "waiting_input",
        "progress_pct": 45,
        "readable_upto": None,
        "failed_reason": None,
    }


async def test_check_requires_url(
    client: AsyncClient, db_session: AsyncSession, redis_client: Any, unique_email: str
) -> None:
    await _login(client, db_session, redis_client, unique_email)
    r = await client.get("/api/ingest/check")
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# PY-ING-03: API 経由の既存判定(読書位置・進捗つき saved)
# 共有シード(2209.03003)には依存せず、ランダム arXiv ID で最小データを組む(隔離のため)。
# ---------------------------------------------------------------------------
async def test_check_existing_has_position_and_progress(
    client: AsyncClient,
    db_session: AsyncSession,
    redis_client: Any,
    unique_email: str,
    created_papers: list[str],
) -> None:
    user = await _login(client, db_session, redis_client, unique_email)
    aid = _rand_arxiv()
    paper = Paper(
        arxiv_id=aid,
        title="Flow Straight and Fast",
        authors=[{"name": "Xingchang Liu"}, {"name": "Qiang Liu"}],
        published_on=dt.date(2022, 9, 7),
        visibility="public",
    )
    db_session.add(paper)
    await db_session.flush()
    created_papers.append(paper.id)
    revision = DocumentRevision(
        paper_id=paper.id,
        source_version="v1",
        parser_version="arxiv-html-2026.07.1",
        quality_level="A",
        source_format="arxiv_html",
        content={},
    )
    db_session.add(revision)
    await db_session.flush()
    # 3 ブロック分の検索索引(位置比で進捗を出す)。読書位置は中央ブロックを指す。
    blocks = [
        ("blk-1-p1", "§1 はじめに", 0),
        ("blk-2-1-p1", "§2.1 整流フロー", 1),
        ("blk-3-p1", "§3 実験", 2),
    ]
    for block_id, section_label, position in blocks:
        db_session.add(
            BlockSearchIndex(
                revision_id=revision.id,
                block_id=block_id,
                block_type="paragraph",
                section_path="sec",
                section_label=section_label,
                position=position,
                source_text="x",
            )
        )
    # LaTeX ソースの存在 → latex_available True。
    db_session.add(
        SourceAsset(
            paper_id=paper.id,
            kind="arxiv_latex",
            source_version="v1",
            storage_key=f"sources/{paper.id}/v1/latex.tar.gz",
            content_type="application/gzip",
            byte_size=1,
        )
    )
    db_session.add(
        LibraryItem(
            user_id=user.id,
            paper_id=paper.id,
            status="reading",
            suggested_tags=["拡散モデル"],
            reading_position={
                "revision_id": revision.id,
                "block_id": "blk-2-1-p1",
                "view_mode": "translation",
            },
        )
    )
    await db_session.commit()

    r = await client.get("/api/ingest/check", params={"url": f"https://arxiv.org/abs/{aid}"})
    assert r.status_code == 200
    body = r.json()
    assert body["kind"] == "arxiv"
    assert body["arxiv_id"] == aid
    assert body["latex_available"] is True  # arxiv_latex ソースあり
    assert "拡散モデル" in body["suggested_tags"]
    saved = body["saved"]
    assert saved is not None
    assert saved["status"] == "reading"
    assert saved["progress_pct"] == 66  # 中央ブロック(2/3)
    assert saved["last_position"]["block_id"] == "blk-2-1-p1"
    assert saved["last_position"]["mode"] == "translation"
    assert saved["last_position"]["section_display"] == "§2.1 整流フロー"
    assert body["bib"]["title"] == "Flow Straight and Fast"


async def test_check_existing_ignores_foreign_and_invalid_reading_revision(
    client: AsyncClient,
    db_session: AsyncSession,
    redis_client: Any,
    unique_email: str,
    created_papers: list[str],
) -> None:
    user = await _login(client, db_session, redis_client, unique_email)
    paper = Paper(arxiv_id=_rand_arxiv(), title="Owned ingest paper", visibility="public")
    foreign_paper = Paper(title="Foreign ingest paper", visibility="public")
    db_session.add_all([paper, foreign_paper])
    await db_session.flush()
    created_papers.extend([str(paper.id), str(foreign_paper.id)])
    own_revision = DocumentRevision(
        paper_id=paper.id,
        source_version="v1",
        parser_version="test",
        quality_level="A",
        source_format="arxiv_html",
        content={"quality_level": "A", "sections": []},
    )
    foreign_revision = DocumentRevision(
        paper_id=foreign_paper.id,
        source_version="v1",
        parser_version="test",
        quality_level="A",
        source_format="arxiv_html",
        content={"quality_level": "A", "sections": []},
    )
    db_session.add_all([own_revision, foreign_revision])
    await db_session.flush()
    paper.latest_revision_id = own_revision.id
    foreign_paper.latest_revision_id = foreign_revision.id
    db_session.add(
        BlockSearchIndex(
            revision_id=foreign_revision.id,
            block_id="blk-foreign",
            block_type="paragraph",
            section_path="sec-foreign",
            section_label="§秘密",
            position=0,
            source_text="private foreign text",
        )
    )
    item = LibraryItem(
        user_id=user.id,
        paper_id=paper.id,
        status="reading",
        reading_position={
            "revision_id": str(foreign_revision.id),
            "block_id": "blk-foreign",
        },
    )
    db_session.add(item)
    await db_session.commit()

    foreign_response = await client.get(
        "/api/ingest/check", params={"url": f"https://arxiv.org/abs/{paper.arxiv_id}"}
    )
    assert foreign_response.status_code == 200, foreign_response.text
    assert foreign_response.json()["saved"]["progress_pct"] == 0
    assert foreign_response.json()["saved"]["last_position"] is None

    item.reading_position = {"revision_id": "not-a-uuid", "block_id": "blk-foreign"}
    await db_session.commit()
    invalid_response = await client.get(
        "/api/ingest/check", params={"url": f"https://arxiv.org/abs/{paper.arxiv_id}"}
    )
    assert invalid_response.status_code == 200, invalid_response.text
    assert invalid_response.json()["saved"]["progress_pct"] == 0
    assert invalid_response.json()["saved"]["last_position"] is None


async def test_pdf_placeholder_replaces_foreign_latest_revision(
    db_session: AsyncSession, created_papers: list[str]
) -> None:
    paper = Paper(title="Owned placeholder paper", visibility="public")
    foreign_paper = Paper(title="Foreign placeholder paper", visibility="public")
    db_session.add_all([paper, foreign_paper])
    await db_session.flush()
    created_papers.extend([str(paper.id), str(foreign_paper.id)])
    foreign_revision = DocumentRevision(
        paper_id=foreign_paper.id,
        source_version="v9",
        parser_version="test",
        quality_level="A",
        source_format="arxiv_html",
        content={"quality_level": "A", "sections": []},
    )
    db_session.add(foreign_revision)
    await db_session.flush()
    foreign_paper.latest_revision_id = foreign_revision.id
    paper.latest_revision_id = foreign_revision.id

    await _ensure_pdf_placeholder_revision(db_session, paper, "v1")
    await db_session.flush()

    assert str(paper.latest_revision_id) != str(foreign_revision.id)
    placeholder = await db_session.get(DocumentRevision, paper.latest_revision_id)
    assert placeholder is not None
    assert str(placeholder.paper_id) == str(paper.id)
    assert placeholder.parser_version == "pdf-placeholder-1.0.0"


# ---------------------------------------------------------------------------
# PY-ING-06: ingest-log の射影
# ---------------------------------------------------------------------------
async def test_ingest_log_projection_sorted(
    client: AsyncClient,
    db_session: AsyncSession,
    redis_client: Any,
    unique_email: str,
    created_papers: list[str],
) -> None:
    user = await _login(client, db_session, redis_client, unique_email)
    paper = Paper(arxiv_id=_rand_arxiv(), title="Log Paper", visibility="public")
    db_session.add(paper)
    await db_session.flush()
    created_papers.append(paper.id)
    db_session.add(LibraryItem(user_id=user.id, paper_id=paper.id, status="reading"))
    # 意図的に時系列を逆順で投入し、API が at 昇順に並べ替えることを検証する。
    db_session.add(
        Job(
            kind="ingest",
            paper_id=paper.id,
            user_id=user.id,
            status="succeeded",
            stage="complete",
            log=[
                {
                    "at": "2026-07-02T21:04:20+00:00",
                    "stage": "structuring",
                    "level": "warn",
                    "message": "図の切り出しに失敗(続行)",
                    "detail": {},
                },
                {
                    "at": "2026-07-02T21:04:12+00:00",
                    "stage": "fetching",
                    "level": "info",
                    "message": "arXiv から HTML 取得",
                    "detail": {"format": "arxiv_html", "timeline": True},
                },
            ],
        )
    )
    await db_session.commit()

    r = await client.get(f"/api/papers/{paper.id}/ingest-log")
    assert r.status_code == 200
    entries = r.json()["entries"]
    assert len(entries) == 2
    assert entries[0]["message"] == "arXiv から HTML 取得"  # 昇順(古→新)
    assert entries[0]["stage"] == "fetching"
    assert entries[1]["stage"] == "structuring"
    # 射影は at/stage/level/message のみ(detail を漏らさない)。
    assert set(entries[0].keys()) == {"at", "stage", "level", "message"}


async def test_ingest_log_denies_non_owner(
    client: AsyncClient,
    db_session: AsyncSession,
    redis_client: Any,
    unique_email: str,
    created_papers: list[str],
) -> None:
    # 別ユーザーが所有(LibraryItem 付き)する public 論文のログは、無関係ユーザーには 404。
    owner = await upsert_user_by_email(
        db_session, f"owner-{uuid.uuid4().hex}@example.com", provider="email"
    )
    paper = Paper(arxiv_id=_rand_arxiv(), title="Private-ish", visibility="public")
    db_session.add(paper)
    await db_session.flush()
    created_papers.append(paper.id)
    db_session.add(LibraryItem(user_id=owner.id, paper_id=paper.id, status="reading"))
    await db_session.commit()

    await _login(client, db_session, redis_client, unique_email)  # 別人でログイン
    r = await client.get(f"/api/papers/{paper.id}/ingest-log")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/ingest/arxiv
# ---------------------------------------------------------------------------
async def test_arxiv_ingest_creates_job(
    client: AsyncClient,
    db_session: AsyncSession,
    redis_client: Any,
    unique_email: str,
    created_papers: list[str],
    wakeups: list[str],
    seed_arxiv_mock: None,
    fake_storage: Any,
) -> None:
    await _login(client, db_session, redis_client, unique_email)
    aid = _rand_arxiv()
    r = await client.post("/api/ingest/arxiv", json={"url": f"https://arxiv.org/abs/{aid}v1"})
    assert r.status_code == 202
    body = r.json()
    created_papers.append(body["paper_id"])
    assert body["duplicate"] is False
    assert body["job_id"] in wakeups  # 起床通知が飛んでいる

    job = await db_session.get(Job, body["job_id"])
    assert job is not None
    assert job.kind == "ingest"
    assert str(job.paper_id) == body["paper_id"]
    assert job.payload["arxiv_id"] == aid
    assert job.payload["mode"] == "initial"
    paper = await db_session.get(Paper, body["paper_id"])
    assert paper is not None and paper.arxiv_id == aid and paper.visibility == "public"
    assert paper.latest_revision_id is not None

    asset = (
        (
            await db_session.execute(
                select(SourceAsset).where(
                    SourceAsset.paper_id == body["paper_id"], SourceAsset.kind == "pdf"
                )
            )
        )
        .scalars()
        .one()
    )
    assert asset.storage_key == f"sources/{body['paper_id']}/v1/original.pdf"
    assert asset.content_type == "application/pdf"

    revision = await db_session.get(DocumentRevision, paper.latest_revision_id)
    assert revision is not None
    assert revision.source_format == "pdf"
    assert revision.quality_level == "B"
    assert revision.parser_version == "pdf-placeholder-1.0.0"

    viewer = await client.get(f"/api/library-items/{body['library_item_id']}/viewer")
    assert viewer.status_code == 200
    assert viewer.json()["revision"]["id"] == str(revision.id)

    assert fake_storage.puts == [
        ("sources", f"sources/{body['paper_id']}/v1/original.pdf", _MINIMAL_PDF, "application/pdf")
    ]


async def test_arxiv_ingest_continues_when_pdf_prefetch_fails(
    client: AsyncClient,
    db_session: AsyncSession,
    redis_client: Any,
    unique_email: str,
    created_papers: list[str],
    wakeups: list[str],
    fake_storage: Any,
) -> None:
    class _PdfFailGateway(_FakeGateway):
        async def fetch_pdf(self, ref: ArxivId, settings: Any) -> bytes:
            raise FetchError("network_error", "temporary arxiv outage")

    app.dependency_overrides[get_arxiv_gateway] = lambda: _PdfFailGateway()
    try:
        await _login(client, db_session, redis_client, unique_email)
        aid = _rand_arxiv()
        r = await client.post("/api/ingest/arxiv", json={"url": f"https://arxiv.org/abs/{aid}"})
    finally:
        app.dependency_overrides.pop(get_arxiv_gateway, None)

    assert r.status_code == 202
    body = r.json()
    created_papers.append(body["paper_id"])
    assert body["job_id"] in wakeups
    assert fake_storage.puts == []

    paper = await db_session.get(Paper, body["paper_id"])
    assert paper is not None
    assert paper.latest_revision_id is not None

    revision = await db_session.get(DocumentRevision, paper.latest_revision_id)
    assert revision is not None
    assert revision.source_format == "pdf"
    assert revision.quality_level == "B"
    assert revision.parser_version == "pdf-placeholder-1.0.0"

    viewer = await client.get(f"/api/library-items/{body['library_item_id']}/viewer")
    assert viewer.status_code == 200
    assert viewer.json()["revision"]["id"] == str(revision.id)

    asset = (
        (
            await db_session.execute(
                select(SourceAsset).where(
                    SourceAsset.paper_id == body["paper_id"], SourceAsset.kind == "pdf"
                )
            )
        )
        .scalars()
        .first()
    )
    assert asset is None


async def test_arxiv_ingest_idempotency_replays(
    client: AsyncClient,
    db_session: AsyncSession,
    redis_client: Any,
    unique_email: str,
    created_papers: list[str],
    seed_arxiv_mock: None,
    fake_storage: Any,
) -> None:
    user = await _login(client, db_session, redis_client, unique_email)
    aid = _rand_arxiv()
    key = "idem-" + uuid.uuid4().hex
    first = await client.post(
        "/api/ingest/arxiv",
        json={"url": f"https://arxiv.org/abs/{aid}"},
        headers={"Idempotency-Key": key},
    )
    assert first.status_code == 202
    created_papers.append(first.json()["paper_id"])
    second = await client.post(
        "/api/ingest/arxiv",
        json={"url": f"https://arxiv.org/abs/{aid}"},
        headers={"Idempotency-Key": key},
    )
    assert second.status_code == 202
    assert second.json() == first.json()  # 初回レスポンスを再生

    # ライブラリ項目は 1 件だけ(冪等)。
    count = await db_session.scalar(
        text("SELECT count(*) FROM library_items WHERE user_id = :u AND paper_id = :p"),
        {"u": user.id, "p": first.json()["paper_id"]},
    )
    assert count == 1
    assert len(fake_storage.puts) == 1


async def test_public_waiting_ingest_is_isolated_between_users(
    client: AsyncClient,
    db_session: AsyncSession,
    redis_client: Any,
    created_papers: list[str],
    seed_arxiv_mock: None,
    fake_storage: Any,
    wakeups: list[str],
) -> None:
    user_a = await _login(
        client, db_session, redis_client, f"ingest-owner-a-{uuid.uuid4().hex}@example.com"
    )
    aid = _rand_arxiv()
    first = await client.post("/api/ingest/arxiv", json={"url": f"https://arxiv.org/abs/{aid}"})
    assert first.status_code == 202, first.text
    created_papers.append(first.json()["paper_id"])
    first_job = await db_session.get(Job, first.json()["job_id"])
    assert first_job is not None
    first_job.status = "waiting_input"
    first_job.stage = "selecting_sections"
    await db_session.commit()

    user_b = await _login(
        client, db_session, redis_client, f"ingest-owner-b-{uuid.uuid4().hex}@example.com"
    )
    second = await client.post("/api/ingest/arxiv", json={"url": f"https://arxiv.org/abs/{aid}"})

    assert second.status_code == 202, second.text
    assert second.json()["paper_id"] == first.json()["paper_id"]
    assert second.json()["library_item_id"] != first.json()["library_item_id"]
    assert second.json()["job_id"] != first.json()["job_id"]
    second_job = await db_session.get(Job, second.json()["job_id"])
    second_item = await db_session.get(LibraryItem, second.json()["library_item_id"])
    assert second_job is not None and str(second_job.user_id) == str(user_b.id)
    assert second_item is not None and str(second_item.user_id) == str(user_b.id)
    await db_session.refresh(first_job)
    assert first_job.status == "waiting_input"
    assert second_job.status == "queued"
    assert str(first_job.user_id) == str(user_a.id)
    assert {first.json()["job_id"], second.json()["job_id"]} <= set(wakeups)


async def test_arxiv_ingest_idempotency_key_is_scoped_to_user(
    client: AsyncClient,
    db_session: AsyncSession,
    redis_client: Any,
    created_papers: list[str],
    seed_arxiv_mock: None,
    fake_storage: Any,
) -> None:
    await _login(client, db_session, redis_client, f"ingest-idem-a-{uuid.uuid4().hex}@example.com")
    aid = _rand_arxiv()
    key = "shared-client-key-" + uuid.uuid4().hex
    first = await client.post(
        "/api/ingest/arxiv",
        json={"url": f"https://arxiv.org/abs/{aid}"},
        headers={"Idempotency-Key": key},
    )
    assert first.status_code == 202, first.text
    created_papers.append(first.json()["paper_id"])
    first_job = await db_session.get(Job, first.json()["job_id"])
    assert first_job is not None
    first_job.status = "succeeded"
    first_job.stage = "complete"
    await db_session.commit()

    user_b = await _login(
        client, db_session, redis_client, f"ingest-idem-b-{uuid.uuid4().hex}@example.com"
    )
    second = await client.post(
        "/api/ingest/arxiv",
        json={"url": f"https://arxiv.org/abs/{aid}"},
        headers={"Idempotency-Key": key},
    )

    assert second.status_code == 202, second.text
    assert second.json()["paper_id"] == first.json()["paper_id"]
    assert second.json()["library_item_id"] != first.json()["library_item_id"]
    assert second.json()["job_id"] != first.json()["job_id"]
    second_job = await db_session.get(Job, second.json()["job_id"])
    assert second_job is not None and str(second_job.user_id) == str(user_b.id)
    assert second_job.idempotency_key != first_job.idempotency_key


async def test_arxiv_ingest_duplicate_conflict(
    client: AsyncClient,
    db_session: AsyncSession,
    redis_client: Any,
    unique_email: str,
    created_papers: list[str],
    seed_arxiv_mock: None,
    fake_storage: Any,
) -> None:
    await _login(client, db_session, redis_client, unique_email)
    aid = _rand_arxiv()
    first = await client.post("/api/ingest/arxiv", json={"url": f"https://arxiv.org/abs/{aid}"})
    assert first.status_code == 202
    li = first.json()["library_item_id"]
    created_papers.append(first.json()["paper_id"])

    dup = await client.post("/api/ingest/arxiv", json={"url": f"https://arxiv.org/abs/{aid}"})
    assert dup.status_code == 409
    assert dup.headers["content-type"].startswith("application/problem+json")
    body = dup.json()
    assert body["code"] == "duplicate"
    assert body["existing"]["library_item_id"] == li
    assert body["existing"]["status"]
    assert len(fake_storage.puts) == 1


async def test_arxiv_ingest_rejects_non_arxiv(
    client: AsyncClient, db_session: AsyncSession, redis_client: Any, unique_email: str
) -> None:
    await _login(client, db_session, redis_client, unique_email)
    r = await client.post("/api/ingest/arxiv", json={"url": "https://example.com/x.pdf"})
    assert r.status_code == 422
    assert r.json()["code"] == "validation_error"


# ---------------------------------------------------------------------------
# GET /api/ingest/recent
# ---------------------------------------------------------------------------
async def test_recent_lists_ingested(
    client: AsyncClient,
    db_session: AsyncSession,
    redis_client: Any,
    unique_email: str,
    created_papers: list[str],
    seed_arxiv_mock: None,
    fake_storage: Any,
) -> None:
    await _login(client, db_session, redis_client, unique_email)
    aid = _rand_arxiv()
    posted = await client.post("/api/ingest/arxiv", json={"url": f"https://arxiv.org/abs/{aid}"})
    assert posted.status_code == 202
    li = posted.json()["library_item_id"]
    created_papers.append(posted.json()["paper_id"])

    r = await client.get("/api/ingest/recent")
    assert r.status_code == 200
    items = r.json()["items"]
    assert any(it["library_item_id"] == li for it in items)
    match = next(it for it in items if it["library_item_id"] == li)
    assert match["viewer_url"] == f"/papers/{li}"
    assert match["pipeline"]["stage"] == "queued"
    assert match["pipeline"]["status"] == "queued"


# ---------------------------------------------------------------------------
# POST /api/papers/{id}/reingest
# ---------------------------------------------------------------------------
async def test_reingest_starts_and_conflicts(
    client: AsyncClient,
    db_session: AsyncSession,
    redis_client: Any,
    unique_email: str,
    created_papers: list[str],
) -> None:
    user = await _login(client, db_session, redis_client, unique_email)
    paper = Paper(arxiv_id=_rand_arxiv(), title="Reingest Paper", visibility="public")
    db_session.add(paper)
    await db_session.flush()
    created_papers.append(paper.id)
    db_session.add(LibraryItem(user_id=user.id, paper_id=paper.id, status="done"))
    await db_session.commit()

    r = await client.post(f"/api/papers/{paper.id}/reingest")
    assert r.status_code == 202
    job_id = r.json()["job_id"]
    job = await db_session.get(Job, job_id)
    assert job is not None and job.kind == "ingest" and job.payload["mode"] == "reingest"

    # 稼働中 ingest があるので二度目は 409 conflict(uq_jobs_ingest_active)。
    again = await client.post(f"/api/papers/{paper.id}/reingest")
    assert again.status_code == 409
    assert again.json()["code"] == "conflict"


async def test_reingest_missing_paper_404(
    client: AsyncClient, db_session: AsyncSession, redis_client: Any, unique_email: str
) -> None:
    await _login(client, db_session, redis_client, unique_email)
    r = await client.post(f"/api/papers/{uuid.uuid4()}/reingest")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/papers/{id}/pdf(200)
# ---------------------------------------------------------------------------
async def test_paper_pdf_streams_extension_capture_bytes(
    client: AsyncClient,
    db_session: AsyncSession,
    redis_client: Any,
    unique_email: str,
    created_papers: list[str],
    fake_storage: Any,
) -> None:
    user = await _login(client, db_session, redis_client, unique_email)
    paper = Paper(arxiv_id=_rand_arxiv(), title="PDF Paper", visibility="public")
    db_session.add(paper)
    await db_session.flush()
    created_papers.append(paper.id)
    db_session.add(LibraryItem(user_id=user.id, paper_id=paper.id, status="reading"))
    revision = DocumentRevision(
        paper_id=paper.id,
        source_version="v1",
        parser_version="pdf-placeholder-1.0.0",
        quality_level="B",
        source_format="pdf",
        content={"quality_level": "B", "sections": []},
        stats={},
    )
    db_session.add(revision)
    await db_session.flush()
    paper.latest_revision_id = revision.id
    key = f"sources/{paper.id}/v1/original.pdf"
    db_session.add(
        SourceAsset(
            paper_id=paper.id,
            kind="extension_capture",
            source_version="v1",
            storage_key=key,
            content_type="application/pdf",
            byte_size=1,
        )
    )
    await db_session.commit()

    r = await client.get(f"/api/papers/{paper.id}/pdf", follow_redirects=False)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/pdf")
    assert r.headers["cache-control"] == "private, max-age=600"
    assert r.content == _MINIMAL_PDF


async def test_paper_pdf_prefers_current_revision_canonical_asset_over_newer_duplicates(
    client: AsyncClient,
    db_session: AsyncSession,
    redis_client: Any,
    unique_email: str,
    created_papers: list[str],
    fake_storage: Any,
) -> None:
    user = await _login(client, db_session, redis_client, unique_email)
    paper = Paper(arxiv_id=_rand_arxiv(), title="Canonical PDF Paper", visibility="public")
    db_session.add(paper)
    await db_session.flush()
    created_papers.append(paper.id)
    db_session.add(LibraryItem(user_id=user.id, paper_id=paper.id, status="reading"))
    revision = DocumentRevision(
        paper_id=paper.id,
        source_version="v1",
        parser_version="html-1.0.0",
        quality_level="A",
        source_format="arxiv_html",
        content={"quality_level": "A", "sections": []},
        stats={},
    )
    db_session.add(revision)
    await db_session.flush()
    paper.latest_revision_id = revision.id

    canonical_key = f"sources/{paper.id}/v1/original.pdf"
    stale_key = f"sources/{paper.id}/v1/stale.pdf"
    unrelated_key = f"sources/{paper.id}/v2/original.pdf"
    now = dt.datetime.now(dt.UTC)
    db_session.add_all(
        [
            SourceAsset(
                paper_id=paper.id,
                kind="pdf",
                source_version="v1",
                storage_key=canonical_key,
                content_type="application/pdf",
                byte_size=10,
                created_at=now - dt.timedelta(minutes=2),
            ),
            SourceAsset(
                paper_id=paper.id,
                kind="pdf",
                source_version="v1",
                storage_key=stale_key,
                content_type="application/pdf",
                byte_size=5,
                created_at=now - dt.timedelta(minutes=1),
            ),
            SourceAsset(
                paper_id=paper.id,
                kind="pdf",
                source_version="v2",
                storage_key=unrelated_key,
                content_type="application/pdf",
                byte_size=8,
                created_at=now,
            ),
        ]
    )
    await db_session.commit()

    canonical_bytes = b"%PDF-canonical-current-version"

    async def get_selected(_bucket: str, key: str) -> bytes:
        return canonical_bytes if key == canonical_key else b"%PDF-stale-or-unrelated"

    fake_storage.get = get_selected
    response = await client.get(f"/api/papers/{paper.id}/pdf", follow_redirects=False)

    assert response.status_code == 200
    assert response.content == canonical_bytes


async def test_paper_pdf_ignores_foreign_latest_revision_source_version(
    client: AsyncClient,
    db_session: AsyncSession,
    redis_client: Any,
    unique_email: str,
    created_papers: list[str],
    fake_storage: Any,
) -> None:
    user = await _login(client, db_session, redis_client, unique_email)
    paper = Paper(title="Owned PDF assets", visibility="public")
    foreign_paper = Paper(title="Foreign revision", visibility="public")
    db_session.add_all([paper, foreign_paper])
    await db_session.flush()
    created_papers.extend([str(paper.id), str(foreign_paper.id)])
    db_session.add(LibraryItem(user_id=user.id, paper_id=paper.id, status="reading"))
    foreign_revision = DocumentRevision(
        paper_id=foreign_paper.id,
        source_version="v2",
        parser_version="test",
        quality_level="A",
        source_format="arxiv_html",
        content={"quality_level": "A", "sections": []},
    )
    db_session.add(foreign_revision)
    await db_session.flush()
    foreign_paper.latest_revision_id = foreign_revision.id
    paper.latest_revision_id = foreign_revision.id
    now = dt.datetime.now(dt.UTC)
    v1_key = f"sources/{paper.id}/v1/original.pdf"
    v2_key = f"sources/{paper.id}/v2/original.pdf"
    db_session.add_all(
        [
            SourceAsset(
                paper_id=paper.id,
                kind="pdf",
                source_version="v2",
                storage_key=v2_key,
                content_type="application/pdf",
                byte_size=2,
                created_at=now - dt.timedelta(minutes=1),
            ),
            SourceAsset(
                paper_id=paper.id,
                kind="pdf",
                source_version="v1",
                storage_key=v1_key,
                content_type="application/pdf",
                byte_size=2,
                created_at=now,
            ),
        ]
    )
    await db_session.commit()

    async def get_selected(_bucket: str, key: str) -> bytes:
        return b"v1" if key == v1_key else b"v2"

    fake_storage.get = get_selected
    response = await client.get(f"/api/papers/{paper.id}/pdf", follow_redirects=False)

    assert response.status_code == 200, response.text
    assert response.content == b"v1"


async def test_paper_pdf_streams_translated_variant(
    client: AsyncClient,
    db_session: AsyncSession,
    redis_client: Any,
    unique_email: str,
    created_papers: list[str],
    fake_storage: Any,
) -> None:
    user = await _login(client, db_session, redis_client, unique_email)
    paper = Paper(arxiv_id=_rand_arxiv(), title="Translated PDF Paper", visibility="public")
    db_session.add(paper)
    await db_session.flush()
    created_papers.append(paper.id)
    db_session.add(LibraryItem(user_id=user.id, paper_id=paper.id, status="reading"))
    db_session.add(
        SourceAsset(
            paper_id=paper.id,
            kind="translated_pdf",
            source_version="v1",
            storage_key=f"sources/{paper.id}/v1/translated-natural.pdf",
            content_type="application/pdf",
            byte_size=1,
        )
    )
    await db_session.commit()

    r = await client.get(
        f"/api/papers/{paper.id}/pdf", params={"variant": "translated"}, follow_redirects=False
    )
    assert r.status_code == 200
    assert r.headers["content-disposition"] == 'inline; filename="paper-translated.pdf"'
    assert r.content == _MINIMAL_PDF

    literal = await client.get(
        f"/api/papers/{paper.id}/pdf",
        params={"variant": "translated", "style": "literal"},
        follow_redirects=False,
    )
    assert literal.status_code == 404


async def test_translated_pdf_resolves_the_requesters_effective_personal_set(
    client: AsyncClient,
    db_session: AsyncSession,
    redis_client: Any,
    unique_email: str,
    created_papers: list[str],
    fake_storage: Any,
) -> None:
    personal_user = await _login(client, db_session, redis_client, unique_email)
    shared_user = await upsert_user_by_email(
        db_session,
        f"shared-pdf-{uuid.uuid4().hex}@example.com",
        provider="email",
    )
    paper = Paper(arxiv_id=_rand_arxiv(), title="Scoped PDF", visibility="public")
    db_session.add(paper)
    await db_session.flush()
    created_papers.append(paper.id)
    db_session.add_all(
        [
            LibraryItem(user_id=personal_user.id, paper_id=paper.id, status="reading"),
            LibraryItem(user_id=shared_user.id, paper_id=paper.id, status="reading"),
        ]
    )
    revision = DocumentRevision(
        paper_id=paper.id,
        source_version="v1",
        parser_version="test",
        quality_level="A",
        source_format="latex",
        content={"quality_level": "A", "sections": []},
        stats={},
    )
    db_session.add(revision)
    await db_session.flush()
    paper.latest_revision_id = revision.id
    shared_set = TranslationSet(
        revision_id=revision.id,
        style="natural",
        scope="shared",
        status="complete",
        glossary_snapshot=[],
    )
    db_session.add(shared_set)
    await db_session.flush()
    personal_set = TranslationSet(
        revision_id=revision.id,
        style="natural",
        scope="personal",
        user_id=personal_user.id,
        base_set_id=shared_set.id,
        status="complete",
        glossary_snapshot=[],
    )
    db_session.add(personal_set)
    await db_session.flush()
    shared_key = f"sources/{paper.id}/v1/translated-natural.pdf"
    personal_key = f"sources/{paper.id}/v1/translated-natural-{personal_set.id}.pdf"
    db_session.add_all(
        [
            SourceAsset(
                paper_id=paper.id,
                kind="translated_pdf",
                source_version="v1",
                storage_key=shared_key,
                content_type="application/pdf",
            ),
            SourceAsset(
                paper_id=paper.id,
                kind="translated_pdf",
                source_version="v1",
                storage_key=personal_key,
                content_type="application/pdf",
            ),
        ]
    )
    await db_session.commit()

    async def get_scoped(_bucket: str, key: str) -> bytes:
        return b"personal" if key == personal_key else b"shared"

    fake_storage.get = get_scoped
    personal_response = await client.get(
        f"/api/papers/{paper.id}/pdf",
        params={"variant": "translated", "style": "natural"},
    )
    shared_token = await create_session(redis_client, shared_user.id)
    client.cookies.set("yk_session", shared_token)
    shared_response = await client.get(
        f"/api/papers/{paper.id}/pdf",
        params={"variant": "translated", "style": "natural"},
    )

    assert personal_response.status_code == 200
    assert personal_response.content == b"personal"
    assert shared_response.status_code == 200
    assert shared_response.content == b"shared"


async def test_paper_pdf_missing_asset_404(
    client: AsyncClient,
    db_session: AsyncSession,
    redis_client: Any,
    unique_email: str,
    created_papers: list[str],
    fake_storage: None,
) -> None:
    user = await _login(client, db_session, redis_client, unique_email)
    paper = Paper(arxiv_id=_rand_arxiv(), title="No PDF", visibility="public")
    db_session.add(paper)
    await db_session.flush()
    created_papers.append(paper.id)
    db_session.add(LibraryItem(user_id=user.id, paper_id=paper.id, status="reading"))
    await db_session.commit()

    r = await client.get(f"/api/papers/{paper.id}/pdf", follow_redirects=False)
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/assets/{asset_id}
# ---------------------------------------------------------------------------
async def test_asset_serves_for_owner(
    client: AsyncClient,
    db_session: AsyncSession,
    redis_client: Any,
    unique_email: str,
    created_papers: list[str],
    fake_storage: None,
) -> None:
    user = await _login(client, db_session, redis_client, unique_email)
    paper = Paper(arxiv_id=_rand_arxiv(), title="Asset Paper", visibility="public")
    db_session.add(paper)
    await db_session.flush()
    created_papers.append(paper.id)
    db_session.add(LibraryItem(user_id=user.id, paper_id=paper.id, status="reading"))
    await db_session.commit()

    key = f"figures/{paper.id}/rev-1/blk-2-1-fig1.png"
    asset_id = encode_asset_id(key)
    r = await client.get(f"/api/assets/{asset_id}", follow_redirects=False)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/png")
    assert r.headers["content-disposition"] == 'inline; filename="blk-2-1-fig1.png"'
    assert r.content == _MINIMAL_PDF


async def test_legacy_asset_key_serves_when_referenced_by_revision(
    client: AsyncClient,
    db_session: AsyncSession,
    redis_client: Any,
    unique_email: str,
    created_papers: list[str],
    fake_storage: None,
) -> None:
    user = await _login(client, db_session, redis_client, unique_email)
    paper = Paper(arxiv_id=_rand_arxiv(), title="Legacy Asset Paper", visibility="public")
    db_session.add(paper)
    await db_session.flush()
    created_papers.append(paper.id)
    key = "figures/fig-1.png"
    db_session.add(LibraryItem(user_id=user.id, paper_id=paper.id, status="reading"))
    db_session.add(
        DocumentRevision(
            paper_id=paper.id,
            source_version="v1",
            quality_level="A",
            source_format="latex",
            parser_version="test",
            content={
                "quality_level": "A",
                "sections": [
                    {
                        "id": "sec-1",
                        "blocks": [{"id": "blk-fig1", "type": "figure", "asset_key": key}],
                    }
                ],
            },
        )
    )
    await db_session.commit()

    r = await client.get(f"/api/assets/{encode_asset_id(key)}", follow_redirects=False)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/png")
    assert r.headers["content-disposition"] == 'inline; filename="fig-1.png"'
    assert r.content == _MINIMAL_PDF


async def test_generated_article_figure_asset_serves_for_owner(
    client: AsyncClient,
    db_session: AsyncSession,
    redis_client: Any,
    unique_email: str,
    created_papers: list[str],
    fake_storage: None,
) -> None:
    user = await _login(client, db_session, redis_client, unique_email)
    paper = Paper(arxiv_id=_rand_arxiv(), title="Generated Figure Paper", visibility="public")
    db_session.add(paper)
    await db_session.flush()
    created_papers.append(paper.id)
    item = LibraryItem(user_id=user.id, paper_id=paper.id, status="reading")
    db_session.add(item)
    await db_session.flush()
    article = Article(
        library_item_id=item.id,
        title="Researcher article",
        preset="researcher",
        include_math=True,
    )
    db_session.add(article)
    await db_session.flush()
    figure_id = str(uuid.uuid4())
    key = f"renders/explainer/{figure_id}/v1.png"
    db_session.add(
        ExplainerFigure(
            id=figure_id,
            article_id=article.id,
            slot=0,
            version=1,
            provider="google",
            model="gemini-3.1-flash-image",
            prompt="test",
            image_storage_key=key,
            caption="test",
        )
    )
    await db_session.commit()

    r = await client.get(f"/api/assets/{encode_asset_id(key)}", follow_redirects=False)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/png")


async def test_asset_bad_id_404(
    client: AsyncClient,
    db_session: AsyncSession,
    redis_client: Any,
    unique_email: str,
    fake_storage: None,
) -> None:
    await _login(client, db_session, redis_client, unique_email)
    r = await client.get("/api/assets/@@not-base64@@", follow_redirects=False)
    assert r.status_code == 404


async def test_asset_denies_without_access(
    client: AsyncClient,
    db_session: AsyncSession,
    redis_client: Any,
    unique_email: str,
    created_papers: list[str],
    fake_storage: None,
) -> None:
    # public 論文だが、この論文の LibraryItem を持たないユーザーには 404(存在を隠す)。
    paper = Paper(arxiv_id=_rand_arxiv(), title="Foreign Asset", visibility="public")
    db_session.add(paper)
    await db_session.flush()
    created_papers.append(paper.id)
    await db_session.commit()

    await _login(client, db_session, redis_client, unique_email)
    asset_id = encode_asset_id(f"figures/{paper.id}/rev-1/blk-1.png")
    r = await client.get(f"/api/assets/{asset_id}", follow_redirects=False)
    assert r.status_code == 404
