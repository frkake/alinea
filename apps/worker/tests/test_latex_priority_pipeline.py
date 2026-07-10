"""M2-01: arXiv 取り込みの取得優先順位 LaTeX > HTML > PDF(plans/05 §1.3・§5)。

- LaTeX ソース(e-print)が取得・解析できる場合、品質 A・`source_format='latex'`・
  `parser_version='latex-1.1.0'` で構造化され、HTML 取得(SourceAsset kind='arxiv_html')は
  行われない(優先順位の主経路化)。
- LaTeX の取得/解析に失敗した場合は既存の HTML 経路へ**可視的に**フォールバックする
  (`jobs.log` に warn を記録。P3)。

本ファイルは独自の LaTeX 応答 ASGI スタブ+ctx フィクスチャ(``latex_worker_ctx``)を
新規に定義し、``apps/worker/tests/conftest.py`` の既定 ``arxiv_http``/``worker_ctx``
(空バイト e-print → 既存 HTML 経路)は変更しない(読み取り専用)。フォールバック側は
既定の ``worker_ctx`` をそのまま使う。
"""

from __future__ import annotations

import base64
import gzip
import io
import json
import random
import re
import tarfile
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import fitz
import httpx
import pytest
import pytest_asyncio
from alinea_core.arxiv.fetch import RedisLike
from alinea_core.arxiv.ids import normalize_arxiv_id
from alinea_core.db.models import DocumentRevision, SourceAsset
from alinea_core.document.blocks import DocumentContent
from alinea_core.ingest import build_timeline
from alinea_core.jobs.store import JobStore
from alinea_core.settings import CoreSettings
from alinea_core.storage.s3 import S3Storage, StorageKeys
from alinea_llm.router import LLMRouter
from alinea_worker.pipeline import _html_figure_asset_url
from alinea_worker.tasks.ingest import ingest_paper
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

# --------------------------------------------------------------------------- #
# ローカル LaTeX e-print フィクスチャ(自作。tar.gz を動的構築)
# --------------------------------------------------------------------------- #

_LATEX_MAIN_TEX = (
    "\\documentclass{article}\n"
    "\\begin{document}\n"
    "\\section{Introduction}\n"
    "\\label{sec:intro}\n"
    "This is a deterministic mock introduction for pipeline testing purposes here.\n"
    "\\begin{equation}\n"
    "\\label{eq:one}\n"
    "E = mc^2\n"
    "\\end{equation}\n"
    "\\begin{figure}\n"
    "\\includegraphics{mock-figure.pdf}\n"
    "\\caption{A mock figure for asset persistence.}\n"
    "\\label{fig:mock}\n"
    "\\end{figure}\n"
    "\\section{Method}\n"
    "The method section describes the approach in detail for testing purposes here.\n"
    "\\end{document}\n"
)


def _tiny_pdf_figure() -> bytes:
    doc = fitz.open()
    page = doc.new_page(width=90, height=45)
    page.insert_text((10, 25), "figure")
    data = doc.tobytes()
    doc.close()
    return bytes(data)


def _build_latex_archive() -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        data = _LATEX_MAIN_TEX.encode()
        info = tarfile.TarInfo(name="main.tex")
        info.size = len(data)
        info.mtime = 0
        tar.addfile(info, io.BytesIO(data))
        fig = _tiny_pdf_figure()
        fig_info = tarfile.TarInfo(name="mock-figure.pdf")
        fig_info.size = len(fig)
        fig_info.mtime = 0
        tar.addfile(fig_info, io.BytesIO(fig))
    return gzip.compress(buf.getvalue(), mtime=0)


_ATOM_XML = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/{id}v1</id>
    <published>2022-09-07T13:00:00Z</published>
    <title>Mock LaTeX Priority Paper</title>
    <summary>A deterministic mock abstract for the LaTeX-priority pipeline test.</summary>
    <author><name>Mock Author</name></author>
    <arxiv:primary_category term="cs.LG"/>
  </entry>
</feed>
"""

_OAI_XML = """<?xml version="1.0" encoding="UTF-8"?>
<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">
  <GetRecord><record><metadata>
    <arXiv xmlns="http://arxiv.org/OAI/arXiv/">
      <license>http://creativecommons.org/licenses/by/4.0/</license>
    </arXiv>
  </metadata></record></GetRecord>
</OAI-PMH>
"""

_FIXTURES = Path(__file__).resolve().parents[3] / "packages" / "py-core" / "tests" / "fixtures"
_VALID_MULTI_PARAGRAPH_PDF = (_FIXTURES / "pdf_quality_b_sample.pdf").read_bytes()
_VALID_PRIORITY_PDF = (_FIXTURES / "pdf_table_sample.pdf").read_bytes()
_PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)


async def _query(request: Request) -> Response:
    id_list = request.query_params.get("id_list", "0000.00000")
    arxiv_id = re.sub(r"v\d+$", "", id_list)
    return Response(_ATOM_XML.format(id=arxiv_id), media_type="application/atom+xml")


async def _oai2(_request: Request) -> Response:
    return Response(_OAI_XML, media_type="text/xml")


async def _eprint_valid_latex(_request: Request) -> Response:
    return Response(_build_latex_archive(), media_type="application/x-eprint-tar")


async def _html_unused(_request: Request) -> Response:  # pragma: no cover
    """LaTeX 成功時は呼ばれないはず(優先順位検証)。呼ばれたら分かるよう 500 で明示する。"""
    return Response("html should not be fetched when latex succeeds", status_code=500)


async def _figure_png(_request: Request) -> Response:
    return Response(_PNG_1X1, media_type="image/png")


async def _pdf(_request: Request) -> Response:
    return Response(_VALID_PRIORITY_PDF, media_type="application/pdf")


def _make_latex_arxiv_stub() -> Starlette:
    return Starlette(
        routes=[
            Route("/api/query", _query, methods=["GET"]),
            Route("/oai2", _oai2, methods=["GET"]),
            Route("/e-print/{arxiv_id:path}", _eprint_valid_latex, methods=["GET"]),
            Route("/html/{versioned}/mock-figure.png", _figure_png, methods=["GET"]),
            Route("/html/{path:path}", _html_unused, methods=["GET"]),
            Route("/pdf/{arxiv_id:path}", _pdf, methods=["GET"]),
        ]
    )


async def _eprint_invalid(_request: Request) -> Response:
    return Response(b"not a latex archive", media_type="application/x-eprint-tar")


async def _html_missing(_request: Request) -> Response:
    return Response("not found", status_code=404)


async def _pdf_valid(_request: Request) -> Response:
    return Response(_VALID_MULTI_PARAGRAPH_PDF, media_type="application/pdf")


def _make_pdf_fallback_stub() -> Starlette:
    return Starlette(
        routes=[
            Route("/api/query", _query, methods=["GET"]),
            Route("/oai2", _oai2, methods=["GET"]),
            Route("/e-print/{arxiv_id:path}", _eprint_invalid, methods=["GET"]),
            Route("/html/{path:path}", _html_missing, methods=["GET"]),
            Route("/pdf/{arxiv_id:path}", _pdf_valid, methods=["GET"]),
        ]
    )


def _make_counting_arxiv_stub(
    calls: dict[str, int], *, pdf_status: int, latex_available: bool
) -> Starlette:
    async def eprint(_request: Request) -> Response:
        calls["eprint"] += 1
        body = _build_latex_archive() if latex_available else b""
        return Response(body, media_type="application/x-eprint-tar")

    async def html(_request: Request) -> Response:
        calls["html"] += 1
        return Response("optional source should not be requested", status_code=500)

    async def pdf(_request: Request) -> Response:
        calls["pdf"] += 1
        return Response("not found", status_code=pdf_status)

    return Starlette(
        routes=[
            Route("/api/query", _query, methods=["GET"]),
            Route("/oai2", _oai2, methods=["GET"]),
            Route("/e-print/{arxiv_id:path}", eprint, methods=["GET"]),
            Route("/html/{path:path}", html, methods=["GET"]),
            Route("/pdf/{arxiv_id:path}", pdf, methods=["GET"]),
        ]
    )


async def _noop_throttle(_redis: RedisLike) -> None:
    return None


class _FakeRedis:
    """in-memory の最小 Redis(conftest の FakeRedis と同形。本ファイル専用に複製)。"""

    def __init__(self) -> None:
        self._store: dict[str, bytes] = {}

    async def get(self, name: str) -> bytes | None:
        return self._store.get(name)

    async def set(
        self,
        name: str,
        value: bytes,
        *,
        ex: int | None = None,
        px: int | None = None,
        nx: bool = False,
    ) -> bool | None:
        if nx and name in self._store:
            return None
        self._store[name] = value
        return True

    async def aclose(self) -> None:
        return None


@pytest_asyncio.fixture
async def latex_arxiv_http() -> AsyncIterator[httpx.AsyncClient]:
    """有効な LaTeX tar.gz を e-print で返す ASGI スタブ(``latex_worker_ctx`` 専用)。"""
    transport = httpx.ASGITransport(app=_make_latex_arxiv_stub())
    async with httpx.AsyncClient(transport=transport, base_url="http://arxiv.test") as client:
        yield client


@pytest_asyncio.fixture
async def pdf_fallback_arxiv_http() -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=_make_pdf_fallback_stub())
    async with httpx.AsyncClient(transport=transport, base_url="http://arxiv.test") as client:
        yield client


@pytest.fixture
def latex_worker_ctx(router: LLMRouter, latex_arxiv_http: httpx.AsyncClient) -> dict[str, Any]:
    """``conftest.worker_ctx`` と同形だが LaTeX 応答スタブを使う独自 ctx(名前衝突なし)。"""
    return {
        "router": router,
        "arxiv_http": latex_arxiv_http,
        "redis": _FakeRedis(),
        "settings": CoreSettings(alinea_arxiv_base_url="http://arxiv.test"),
        "throttle": _noop_throttle,
    }


@pytest.fixture
def pdf_fallback_worker_ctx(
    router: LLMRouter, pdf_fallback_arxiv_http: httpx.AsyncClient
) -> dict[str, Any]:
    return {
        "router": router,
        "arxiv_http": pdf_fallback_arxiv_http,
        "redis": _FakeRedis(),
        "settings": CoreSettings(alinea_arxiv_base_url="http://arxiv.test"),
        "throttle": _noop_throttle,
    }


def _arxiv_id() -> str:
    n = (int(time.time() * 1000) + random.randint(0, 9999)) % 100000
    return f"{random.randint(1001, 2912)}.{n:05d}"


async def _source_asset_kinds(db: AsyncSession, paper_id: str) -> set[str]:
    rows = (
        await db.execute(select(SourceAsset.kind).where(SourceAsset.paper_id == paper_id))
    ).scalars()
    return set(rows.all())


# ============================ LaTeX 優先経路(成功時) ============================


def test_html_figure_asset_url_does_not_duplicate_version_prefix() -> None:
    ref = normalize_arxiv_id("2607.02963v1")
    settings = CoreSettings(alinea_arxiv_base_url="https://arxiv.org")

    url = _html_figure_asset_url(settings, ref, "2607.02963v1/x1.png")

    assert url == "https://arxiv.org/html/2607.02963v1/x1.png"


async def test_ingest_prefers_latex_source_when_available(
    db_session: AsyncSession,
    latex_worker_ctx: dict[str, Any],
    seed_ingest_job: Any,
) -> None:
    arxiv_id = _arxiv_id()
    ids = await seed_ingest_job(db_session, arxiv_id=arxiv_id)
    store = JobStore(db_session)

    job = await store.claim(ids["job_id"])
    assert job is not None
    await ingest_paper(latex_worker_ctx, store, job)  # arq プール無し → その場駆動

    job = await store.get(ids["job_id"])
    assert job is not None
    assert job.status == "succeeded"
    assert job.stage == "complete"

    rev = (
        (
            await db_session.execute(
                select(DocumentRevision).where(DocumentRevision.paper_id == ids["paper_id"])
            )
        )
        .scalars()
        .one()
    )
    assert rev.source_format == "latex"
    assert rev.parser_version == "latex-1.1.0"
    assert rev.quality_level == "A"
    assert rev.stats["candidate_failures"] == []
    assert rev.stats["completeness"]["accepted"] is True
    content = DocumentContent.model_validate(rev.content)
    fig = next(block for _sec, block in content.iter_blocks() if block.type == "figure")
    assert fig.asset_key is not None
    assert fig.asset_key.startswith(f"figures/{ids['paper_id']}/{rev.id}/{fig.id}.")
    assert fig.asset_key.endswith(".png")

    kinds = await _source_asset_kinds(db_session, ids["paper_id"])
    assert "arxiv_latex" in kinds
    assert "arxiv_html" not in kinds  # 優先順位: LaTeX 成功時は HTML を取得しない
    assert "pdf" in kinds

    timeline = build_timeline(job.log)
    assert timeline
    assert "LaTeX ソース取得" in timeline[0]["label"]


# ============================ HTML への可視的フォールバック(既存経路の保護) ============================


async def test_ingest_falls_back_to_html_and_logs_warning_when_latex_unavailable(
    db_session: AsyncSession,
    worker_ctx: dict[str, Any],  # conftest 既定(e-print は空バイト = LaTeX ソース無し)
    seed_ingest_job: Any,
) -> None:
    arxiv_id = _arxiv_id()
    ids = await seed_ingest_job(db_session, arxiv_id=arxiv_id)
    store = JobStore(db_session)

    job = await store.claim(ids["job_id"])
    assert job is not None
    await ingest_paper(worker_ctx, store, job)

    job = await store.get(ids["job_id"])
    assert job is not None
    assert job.status == "succeeded"

    rev = (
        (
            await db_session.execute(
                select(DocumentRevision).where(DocumentRevision.paper_id == ids["paper_id"])
            )
        )
        .scalars()
        .one()
    )
    assert rev.source_format == "arxiv_html"
    assert rev.parser_version == "html-1.0.0"
    assert rev.stats["candidate_failures"][0]["format"] == "latex"
    assert rev.stats["completeness"]["accepted"] is True

    kinds = await _source_asset_kinds(db_session, ids["paper_id"])
    assert "arxiv_html" in kinds
    assert "arxiv_latex" not in kinds
    assert "pdf" in kinds

    warn_entries = [row for row in job.log if row.get("level") == "warn"]
    assert any("LaTeX" in row.get("message", "") for row in warn_entries)


async def test_ingest_falls_back_to_stored_pdf_when_latex_and_html_fail(
    db_session: AsyncSession,
    pdf_fallback_worker_ctx: dict[str, Any],
    seed_ingest_job: Any,
) -> None:
    arxiv_id = _arxiv_id()
    ids = await seed_ingest_job(db_session, arxiv_id=arxiv_id)
    store = JobStore(db_session)

    job = await store.claim(ids["job_id"])
    assert job is not None
    await ingest_paper(pdf_fallback_worker_ctx, store, job)

    job = await store.get(ids["job_id"])
    assert job is not None
    assert job.status == "succeeded", job.error

    rev = (
        (
            await db_session.execute(
                select(DocumentRevision).where(DocumentRevision.paper_id == ids["paper_id"])
            )
        )
        .scalars()
        .one()
    )
    assert rev.source_format == "pdf"
    assert rev.parser_version == "pdf-1.0.0"
    assert rev.quality_level == "B"
    assert rev.stats["candidate_failures"] == [
        {
            "format": "latex",
            "code": "no_main_tex",
            "message": "no .tex content found in e-print archive",
        },
        {
            "format": "arxiv_html",
            "code": "source_not_found",
            "message": "arxiv html returned 404",
        },
    ]
    assert rev.stats["completeness"]["accepted"] is True

    pdf_asset = (
        (
            await db_session.execute(
                select(SourceAsset).where(
                    SourceAsset.paper_id == ids["paper_id"], SourceAsset.kind == "pdf"
                )
            )
        )
        .scalars()
        .one()
    )
    assert pdf_asset.storage_key == StorageKeys.original_pdf(ids["paper_id"], "v1")
    storage = S3Storage()
    assert await storage.get(storage.sources_bucket, pdf_asset.storage_key) == (
        _VALID_MULTI_PARAGRAPH_PDF
    )


async def test_ingest_reuses_cached_original_pdf_without_network_fetch(
    db_session: AsyncSession,
    router: LLMRouter,
    seed_ingest_job: Any,
) -> None:
    arxiv_id = _arxiv_id()
    ids = await seed_ingest_job(db_session, arxiv_id=arxiv_id)
    storage = S3Storage()
    key = StorageKeys.original_pdf(ids["paper_id"], "v1")
    await storage.put(
        storage.sources_bucket, key, _VALID_PRIORITY_PDF, content_type="application/pdf"
    )
    db_session.add(
        SourceAsset(
            paper_id=ids["paper_id"],
            kind="pdf",
            source_url=f"http://arxiv.test/pdf/{arxiv_id}v1",
            source_version="v1",
            storage_key=key,
            content_type="application/pdf",
            byte_size=len(_VALID_PRIORITY_PDF),
        )
    )
    await db_session.commit()
    store = JobStore(db_session)
    await store.checkpoint(
        ids["job_id"],
        "fetching",
        {"source_version": "v1", "source_format": "latex"},
        progress=10,
    )

    calls = {"pdf": 0, "eprint": 0, "html": 0}
    transport = httpx.ASGITransport(
        app=_make_counting_arxiv_stub(calls, pdf_status=500, latex_available=True)
    )
    async with httpx.AsyncClient(transport=transport, base_url="http://arxiv.test") as http:
        ctx = {
            "router": router,
            "arxiv_http": http,
            "redis": _FakeRedis(),
            "settings": CoreSettings(alinea_arxiv_base_url="http://arxiv.test"),
            "throttle": _noop_throttle,
        }
        job = await store.claim(ids["job_id"])
        assert job is not None
        await ingest_paper(ctx, store, job)

    job = await store.get(ids["job_id"])
    assert job is not None
    assert job.status == "succeeded", job.error
    assert calls == {"pdf": 0, "eprint": 1, "html": 0}


async def test_ingest_reuses_api_prefetched_latest_pdf_without_network_fetch(
    db_session: AsyncSession,
    router: LLMRouter,
    seed_ingest_job: Any,
) -> None:
    arxiv_id = _arxiv_id()
    ids = await seed_ingest_job(db_session, arxiv_id=arxiv_id)
    storage = S3Storage()
    key = StorageKeys.original_pdf(ids["paper_id"], "latest")
    await storage.put(
        storage.sources_bucket, key, _VALID_PRIORITY_PDF, content_type="application/pdf"
    )
    db_session.add(
        SourceAsset(
            paper_id=ids["paper_id"],
            kind="pdf",
            source_url=f"http://arxiv.test/pdf/{arxiv_id}",
            source_version="latest",
            storage_key=key,
            content_type="application/pdf",
            byte_size=len(_VALID_PRIORITY_PDF),
        )
    )
    await db_session.commit()

    calls = {"pdf": 0, "eprint": 0, "html": 0}
    transport = httpx.ASGITransport(
        app=_make_counting_arxiv_stub(calls, pdf_status=500, latex_available=True)
    )
    async with httpx.AsyncClient(transport=transport, base_url="http://arxiv.test") as http:
        ctx = {
            "router": router,
            "arxiv_http": http,
            "redis": _FakeRedis(),
            "settings": CoreSettings(alinea_arxiv_base_url="http://arxiv.test"),
            "throttle": _noop_throttle,
        }
        store = JobStore(db_session)
        job = await store.claim(ids["job_id"])
        assert job is not None
        await ingest_paper(ctx, store, job)

    job = await store.get(ids["job_id"])
    assert job is not None
    assert job.status == "succeeded", job.error
    assert calls == {"pdf": 0, "eprint": 1, "html": 0}
    pdf_assets = (
        (
            await db_session.execute(
                select(SourceAsset).where(
                    SourceAsset.paper_id == ids["paper_id"], SourceAsset.kind == "pdf"
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(pdf_assets) == 1
    assert pdf_assets[0].source_version == "v1"


async def test_ingest_resume_uses_structuring_checkpoint_without_reselecting_candidate(
    db_session: AsyncSession,
    router: LLMRouter,
    seed_ingest_job: Any,
) -> None:
    arxiv_id = _arxiv_id()
    ids = await seed_ingest_job(db_session, arxiv_id=arxiv_id)
    storage = S3Storage()
    key = StorageKeys.original_pdf(ids["paper_id"], "v1")
    await storage.put(
        storage.sources_bucket, key, _VALID_PRIORITY_PDF, content_type="application/pdf"
    )
    db_session.add(
        SourceAsset(
            paper_id=ids["paper_id"],
            kind="pdf",
            source_url=f"http://arxiv.test/pdf/{arxiv_id}v1",
            source_version="v1",
            storage_key=key,
            content_type="application/pdf",
            byte_size=len(_VALID_PRIORITY_PDF),
        )
    )
    await db_session.commit()

    calls = {"pdf": 0, "eprint": 0, "html": 0}
    transport = httpx.ASGITransport(
        app=_make_counting_arxiv_stub(calls, pdf_status=500, latex_available=True)
    )
    async with httpx.AsyncClient(transport=transport, base_url="http://arxiv.test") as http:
        ctx = {
            "router": router,
            "arxiv_http": http,
            "redis": _FakeRedis(),
            "settings": CoreSettings(alinea_arxiv_base_url="http://arxiv.test"),
            "throttle": _noop_throttle,
        }
        store = JobStore(db_session)
        job = await store.claim(ids["job_id"])
        assert job is not None
        await ingest_paper(ctx, store, job)
        assert calls == {"pdf": 0, "eprint": 1, "html": 0}

        job = await store.get(ids["job_id"])
        assert job is not None
        job.status = "queued"
        job.stage = "structuring"
        job.finished_at = None
        await db_session.commit()
        calls.update(pdf=0, eprint=0, html=0)

        job = await store.claim(ids["job_id"])
        assert job is not None
        await ingest_paper(ctx, store, job)

    assert calls == {"pdf": 0, "eprint": 0, "html": 0}
    revisions = (
        await db_session.execute(
            select(DocumentRevision).where(DocumentRevision.paper_id == ids["paper_id"])
        )
    ).scalars()
    assert len(revisions.all()) == 1


async def test_ingest_requires_pdf_after_cache_and_network_both_fail(
    db_session: AsyncSession,
    router: LLMRouter,
    seed_ingest_job: Any,
) -> None:
    arxiv_id = _arxiv_id()
    ids = await seed_ingest_job(db_session, arxiv_id=arxiv_id)
    db_session.add(
        SourceAsset(
            paper_id=ids["paper_id"],
            kind="pdf",
            source_url=f"http://arxiv.test/pdf/{arxiv_id}v1",
            source_version="v1",
            storage_key=f"sources/{ids['paper_id']}/v1/stale-original.pdf",
            content_type="application/pdf",
            byte_size=len(_VALID_PRIORITY_PDF),
        )
    )
    await db_session.commit()

    calls = {"pdf": 0, "eprint": 0, "html": 0}
    transport = httpx.ASGITransport(
        app=_make_counting_arxiv_stub(calls, pdf_status=404, latex_available=True)
    )
    async with httpx.AsyncClient(transport=transport, base_url="http://arxiv.test") as http:
        ctx = {
            "router": router,
            "arxiv_http": http,
            "redis": _FakeRedis(),
            "settings": CoreSettings(alinea_arxiv_base_url="http://arxiv.test"),
            "throttle": _noop_throttle,
        }
        store = JobStore(db_session)
        job = await store.claim(ids["job_id"])
        assert job is not None
        await ingest_paper(ctx, store, job)

    job = await store.get(ids["job_id"])
    assert job is not None
    assert job.status == "failed"
    error = json.loads(job.error or "{}")
    assert error["code"] == "source_not_found"
    assert "cache=asset_missing,canonical_missing" in error["message"]
    assert calls == {"pdf": 1, "eprint": 0, "html": 0}
    revisions = (
        await db_session.execute(
            select(DocumentRevision).where(DocumentRevision.paper_id == ids["paper_id"])
        )
    ).scalars()
    assert revisions.all() == []
