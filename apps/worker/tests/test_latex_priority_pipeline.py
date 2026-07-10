"""M2-01: arXiv 取り込みの取得優先順位 LaTeX > HTML > PDF(plans/05 §1.3・§5)。

- LaTeX ソース(e-print)が取得・解析できる場合、品質 A・`source_format='latex'`・
  `parser_version='latex-1.2.0'` で構造化され、HTML 取得(SourceAsset kind='arxiv_html')は
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
import hashlib
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
from alinea_core.arxiv.fetch import FetchError, RedisLike
from alinea_core.arxiv.ids import normalize_arxiv_id
from alinea_core.db.models import DocumentRevision, Paper, SourceAsset
from alinea_core.document.blocks import DocumentContent
from alinea_core.ingest import DocumentCompleteness, build_timeline
from alinea_core.jobs.store import JobStore
from alinea_core.parsing.html_parser import PARSER_VERSION as HTML_PARSER_VERSION
from alinea_core.parsing.pdf_parser import PARSER_VERSION as PDF_PARSER_VERSION
from alinea_core.settings import CoreSettings
from alinea_core.storage.s3 import S3Storage, StorageKeys
from alinea_llm.router import LLMRouter
from alinea_worker.figure_assets import html_asset_url
from alinea_worker.pipeline import IngestRun, run_ingest
from alinea_worker.source_candidates import embedded_pdf_bytes, parse_html_candidate
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
    "\\graphicspath{{../images/}}\n"
    "\\begin{document}\n"
    "\\section{Introduction}\n"
    "\\label{sec:intro}\n"
    "This is a deterministic mock introduction for pipeline testing purposes here.\n"
    "\\begin{equation}\n"
    "\\label{eq:one}\n"
    "E = mc^2\n"
    "\\end{equation}\n"
    "\\begin{figure}\n"
    "\\includegraphics{mock-figure}\n"
    "\\caption{A mock figure for asset persistence.}\n"
    "\\label{fig:mock}\n"
    "\\end{figure}\n"
    "\\section{Method}\n"
    "The method section describes the approach in detail for testing purposes here.\n"
    "\\end{document}\n"
)
_LATEX_MISSING_ASSET_TEX = _LATEX_MAIN_TEX.replace(
    "\\end{document}\n",
    "\\begin{figure}\n"
    "\\caption{A figure whose source is missing.}\n"
    "\\label{fig:missing}\n"
    "\\end{figure}\n"
    "\\end{document}\n",
)


def _tiny_pdf_figure() -> bytes:
    doc = fitz.open()
    page = doc.new_page(width=90, height=45)
    page.insert_text((10, 25), "figure")
    data = doc.tobytes()
    doc.close()
    return bytes(data)


def _build_latex_archive(main_tex: str = _LATEX_MAIN_TEX) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        data = main_tex.encode()
        info = tarfile.TarInfo(name="paper/main.tex")
        info.size = len(data)
        info.mtime = 0
        tar.addfile(info, io.BytesIO(data))
        fig = _tiny_pdf_figure()
        fig_info = tarfile.TarInfo(name="images/mock-figure.pdf")
        fig_info.size = len(fig)
        fig_info.mtime = 0
        tar.addfile(fig_info, io.BytesIO(fig))
    return gzip.compress(buf.getvalue(), mtime=0)


_EMBEDDED_PDF_WRAPPER_TEX = (
    "\\documentclass{article}\n"
    "\\usepackage{pdfpages}\n"
    "\\begin{document}\n"
    "\\includepdf[pages=-]{body.pdf}\n"
    "\\end{document}\n"
)


def _build_embedded_pdf_archive(
    pdf_bytes: bytes | None = None,
    *,
    additional_pdfs: dict[str, bytes] | None = None,
) -> bytes:
    pdf_bytes = _VALID_MULTI_PARAGRAPH_PDF if pdf_bytes is None else pdf_bytes
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        tex = _EMBEDDED_PDF_WRAPPER_TEX.encode()
        tex_info = tarfile.TarInfo(name="main.tex")
        tex_info.size = len(tex)
        tex_info.mtime = 0
        tar.addfile(tex_info, io.BytesIO(tex))
        pdf_info = tarfile.TarInfo(name="body.pdf")
        pdf_info.size = len(pdf_bytes)
        pdf_info.mtime = 0
        tar.addfile(pdf_info, io.BytesIO(pdf_bytes))
        for name, data in sorted((additional_pdfs or {}).items()):
            extra_info = tarfile.TarInfo(name=name)
            extra_info.size = len(data)
            extra_info.mtime = 0
            tar.addfile(extra_info, io.BytesIO(data))
    return gzip.compress(buf.getvalue(), mtime=0)


def _incomplete_original_pdf() -> bytes:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_textbox(
        fitz.Rect(72, 72, 520, 180),
        "This canonical original PDF is intentionally incomplete and contains only one "
        "meaningful paragraph for fallback testing.",
        fontsize=11,
    )
    data = bytes(doc.tobytes())
    doc.close()
    return data


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
_CORRUPT_PDF_LIKE = b"%PDF-1.4\ncorrupt"
_VALID_STORED_HTML = b"""<!doctype html><html><body><article class="ltx_document">
<section class="ltx_section"><h2 class="ltx_title ltx_title_section">1 Stored Method</h2>
<div class="ltx_para"><p class="ltx_p">The stored candidate has its first complete paragraph.</p></div>
<div class="ltx_para"><p class="ltx_p">The stored candidate has its second complete paragraph.</p></div>
</section></article></body></html>"""
_VALID_INLINE_SVG_HTML = b"""<!doctype html><html><body><article class="ltx_document">
<section class="ltx_section"><h2 class="ltx_title ltx_title_section">1 Results</h2>
<div class="ltx_para"><p class="ltx_p">The first complete result paragraph is here.</p></div>
<div class="ltx_para"><p class="ltx_p">The second complete result paragraph is here.</p></div>
<figure id="S1.F1" class="ltx_figure"><svg width="20" height="10">
<rect width="20" height="10" fill="blue"></rect></svg></figure>
</section></article></body></html>"""
_VALID_STALE_HTML = _VALID_STORED_HTML.replace(b"The stored candidate", b"The stale duplicate")
_PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)


async def _query(request: Request) -> Response:
    id_list = request.query_params.get("id_list", "0000.00000")
    arxiv_id = re.sub(r"v\d+$", "", id_list)
    return Response(_ATOM_XML.format(id=arxiv_id), media_type="application/atom+xml")


async def _oai2(_request: Request) -> Response:
    return Response(_OAI_XML, media_type="text/xml")


async def _html_unused(_request: Request) -> Response:  # pragma: no cover
    """LaTeX 成功時は呼ばれないはず(優先順位検証)。呼ばれたら分かるよう 500 で明示する。"""
    return Response("html should not be fetched when latex succeeds", status_code=500)


async def _figure_png(_request: Request) -> Response:
    return Response(_PNG_1X1, media_type="image/png")


async def _pdf(_request: Request) -> Response:
    return Response(_VALID_PRIORITY_PDF, media_type="application/pdf")


def _make_latex_arxiv_stub(archive: bytes | None = None) -> Starlette:
    selected_archive = archive if archive is not None else _build_latex_archive()

    async def eprint(_request: Request) -> Response:
        return Response(selected_archive, media_type="application/x-eprint-tar")

    return Starlette(
        routes=[
            Route("/api/query", _query, methods=["GET"]),
            Route("/oai2", _oai2, methods=["GET"]),
            Route("/e-print/{arxiv_id:path}", eprint, methods=["GET"]),
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


def _make_embedded_pdf_wrapper_stub(
    calls: dict[str, int], *, archive: bytes, original_pdf: bytes
) -> Starlette:
    async def eprint(_request: Request) -> Response:
        calls["eprint"] += 1
        return Response(archive, media_type="application/x-eprint-tar")

    async def html(_request: Request) -> Response:
        calls["html"] += 1
        return Response("html must not be needed", status_code=500)

    async def pdf(_request: Request) -> Response:
        calls["pdf"] += 1
        return Response(original_pdf, media_type="application/pdf")

    return Starlette(
        routes=[
            Route("/api/query", _query, methods=["GET"]),
            Route("/oai2", _oai2, methods=["GET"]),
            Route("/e-print/{arxiv_id:path}", eprint, methods=["GET"]),
            Route("/html/{path:path}", html, methods=["GET"]),
            Route("/pdf/{arxiv_id:path}", pdf, methods=["GET"]),
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


def _make_candidate_status_stub(
    *,
    eprint_status: int,
    eprint_body: bytes,
    html_status: int,
    html_body: bytes = _VALID_STORED_HTML,
    pdf_bytes: bytes = _VALID_PRIORITY_PDF,
) -> Starlette:
    async def eprint(_request: Request) -> Response:
        return Response(
            eprint_body,
            status_code=eprint_status,
            media_type="application/x-eprint-tar",
        )

    async def html(_request: Request) -> Response:
        return Response(
            html_body,
            status_code=html_status,
            media_type="text/html; charset=utf-8",
        )

    async def pdf(_request: Request) -> Response:
        return Response(pdf_bytes, media_type="application/pdf")

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


async def _seed_existing_pdf_revision_for_embedded_selection(
    db: AsyncSession,
    seed_ingest_job: Any,
    *,
    provenance: str,
    embedded_pdf: bytes = _VALID_MULTI_PARAGRAPH_PDF,
) -> tuple[dict[str, str], bytes, bytes, str, dict[str, Any], dict[str, Any]]:
    arxiv_id = _arxiv_id()
    ids = await seed_ingest_job(db, arxiv_id=arxiv_id)
    archive = _build_embedded_pdf_archive(embedded_pdf)
    original_pdf = _incomplete_original_pdf()
    storage = S3Storage()
    pdf_key = StorageKeys.original_pdf(ids["paper_id"], "v1")
    latex_key = StorageKeys.latex_tar(ids["paper_id"], "v1")
    await storage.put(
        storage.sources_bucket,
        pdf_key,
        original_pdf,
        content_type="application/pdf",
    )
    db.add(
        SourceAsset(
            paper_id=ids["paper_id"],
            kind="pdf",
            source_url=f"http://arxiv.test/pdf/{arxiv_id}v1",
            source_version="v1",
            storage_key=pdf_key,
            content_type="application/pdf",
            byte_size=len(original_pdf),
            sha256=hashlib.sha256(original_pdf).hexdigest(),
        )
    )
    stats: dict[str, Any] = {
        "marker": "existing revision must not be mutated",
        "candidate_failures": [],
        "completeness": {"accepted": True, "code": None},
    }
    if provenance in {"exact", "member_digest_mismatch", "partial"}:
        stats["embedded_pdf_source"] = "body.pdf"
    if provenance in {"exact", "member_digest_mismatch"}:
        stats.update(
            {
                "embedded_pdf_sha256": (
                    hashlib.sha256(embedded_pdf).hexdigest() if provenance == "exact" else "0" * 64
                ),
                "embedded_pdf_container_sha256": hashlib.sha256(archive).hexdigest(),
                "embedded_pdf_container_storage_key": latex_key,
            }
        )
    content = DocumentContent(quality_level="B", sections=[]).model_dump()
    revision = DocumentRevision(
        paper_id=ids["paper_id"],
        source_version="v1",
        parser_version=PDF_PARSER_VERSION,
        quality_level="B",
        source_format="pdf",
        content=content,
        stats=stats,
    )
    db.add(revision)
    await db.flush()
    revision_id = str(revision.id)
    await db.commit()
    store = JobStore(db)
    await store.checkpoint(ids["job_id"], "fetching", {"source_version": "v1"}, progress=10)
    return (
        ids,
        archive,
        original_pdf,
        revision_id,
        json.loads(json.dumps(content)),
        json.loads(json.dumps(stats)),
    )


def _completeness_report(code: str | None) -> DocumentCompleteness:
    return DocumentCompleteness(
        accepted=False,
        code=code,
        source_chars=0,
        structured_chars=0,
        paragraph_count=0,
        figure_count=0,
    )


def test_embedded_pdf_bytes_requires_wrapper_report() -> None:
    assert (
        embedded_pdf_bytes(
            _completeness_report("document_incomplete"),
            {"paper.pdf": _VALID_MULTI_PARAGRAPH_PDF},
        )
        is None
    )


def test_embedded_pdf_bytes_rejects_zero_or_multiple_eligible_pdfs() -> None:
    report = _completeness_report("embedded_pdf_wrapper")

    assert embedded_pdf_bytes(report, {"figure.png": _PNG_1X1}) is None
    assert (
        embedded_pdf_bytes(
            report,
            {
                "z-paper.pdf": _VALID_MULTI_PARAGRAPH_PDF,
                "a-paper.PDF": _VALID_PRIORITY_PDF,
            },
        )
        is None
    )


def test_embedded_pdf_bytes_counts_invalid_pdf_member_as_ambiguous() -> None:
    assert (
        embedded_pdf_bytes(
            _completeness_report("embedded_pdf_wrapper"),
            {
                "paper.pdf": _VALID_MULTI_PARAGRAPH_PDF,
                "placeholder.pdf": b"",
            },
        )
        is None
    )


def test_embedded_pdf_bytes_accepts_uppercase_extension_and_ignores_non_pdf_entries() -> None:
    result = embedded_pdf_bytes(
        _completeness_report("embedded_pdf_wrapper"),
        {
            "notes.txt": b"not a PDF",
            "nested/PAPER.PDF": _VALID_MULTI_PARAGRAPH_PDF,
        },
    )

    assert result == ("nested/PAPER.PDF", _VALID_MULTI_PARAGRAPH_PDF)


@pytest.mark.parametrize(
    "member_name",
    [
        "/paper.pdf",
        "../paper.pdf",
        "nested/../../paper.pdf",
        "nested\\paper.pdf",
        "https:paper.pdf",
        "nested/\x01paper.pdf",
    ],
)
def test_embedded_pdf_bytes_rejects_unsafe_member_names(member_name: str) -> None:
    assert (
        embedded_pdf_bytes(
            _completeness_report("embedded_pdf_wrapper"),
            {member_name: _VALID_MULTI_PARAGRAPH_PDF},
        )
        is None
    )


@pytest.mark.parametrize("data", [b"", b"this is not a PDF"])
def test_embedded_pdf_bytes_rejects_non_pdf_payload(data: bytes) -> None:
    assert (
        embedded_pdf_bytes(
            _completeness_report("embedded_pdf_wrapper"),
            {"paper.pdf": data},
        )
        is None
    )


# ============================ LaTeX 優先経路(成功時) ============================


def test_html_figure_asset_url_does_not_duplicate_version_prefix() -> None:
    ref = normalize_arxiv_id("2607.02963v1")
    settings = CoreSettings(alinea_arxiv_base_url="https://arxiv.org")

    url = html_asset_url(settings.alinea_arxiv_base_url, ref.versioned, "2607.02963v1/x1.png")

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
    parsing_checkpoint = JobStore.get_checkpoint(job)["parsing"]
    assert parsing_checkpoint["source_storage_key"] == StorageKeys.latex_tar(ids["paper_id"], "v1")
    storage = S3Storage()
    stored_latex = await storage.get(
        storage.sources_bucket, parsing_checkpoint["source_storage_key"]
    )
    assert parsing_checkpoint["source_sha256"] == hashlib.sha256(stored_latex).hexdigest()

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
    assert rev.parser_version == "latex-1.2.0"
    assert rev.quality_level == "A"
    assert rev.stats["candidate_failures"] == []
    assert rev.stats["completeness"]["accepted"] is True
    assert rev.stats["figure_asset_failures"] == []
    assert rev.stats["latex_source"]["main_tex"] == "paper/main.tex"
    assert rev.stats["latex_source"]["graphicspaths"] == ["../images/"]
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


async def test_missing_latex_figure_key_is_persisted_as_structured_failure(
    db_session: AsyncSession,
    router: LLMRouter,
    seed_ingest_job: Any,
) -> None:
    ids = await seed_ingest_job(db_session, arxiv_id=_arxiv_id())
    transport = httpx.ASGITransport(
        app=_make_latex_arxiv_stub(_build_latex_archive(_LATEX_MISSING_ASSET_TEX))
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

    revision = (
        (
            await db_session.execute(
                select(DocumentRevision).where(DocumentRevision.paper_id == ids["paper_id"])
            )
        )
        .scalars()
        .one()
    )
    content = DocumentContent.model_validate(revision.content)
    missing = next(
        block for _section, block in content.iter_blocks() if block.label == "fig:missing"
    )
    assert revision.stats["figure_asset_failures"] == [
        {
            "code": "missing_asset_key",
            "figure_id": missing.id,
            "source": "latex",
        }
    ]
    assert missing.asset_key is None


async def test_latex_wrapper_promotes_embedded_pdf_to_pdf_candidate(
    db_session: AsyncSession,
    router: LLMRouter,
    seed_ingest_job: Any,
) -> None:
    arxiv_id = _arxiv_id()
    ids = await seed_ingest_job(db_session, arxiv_id=arxiv_id)
    archive = _build_embedded_pdf_archive()
    original_pdf = _incomplete_original_pdf()
    calls = {"pdf": 0, "eprint": 0, "html": 0}
    transport = httpx.ASGITransport(
        app=_make_embedded_pdf_wrapper_stub(
            calls,
            archive=archive,
            original_pdf=original_pdf,
        )
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
    assert calls == {"pdf": 1, "eprint": 1, "html": 0}

    revision = (
        (
            await db_session.execute(
                select(DocumentRevision).where(DocumentRevision.paper_id == ids["paper_id"])
            )
        )
        .scalars()
        .one()
    )
    assert revision.source_format == "pdf"
    assert revision.parser_version == PDF_PARSER_VERSION
    assert revision.quality_level == "B"
    assert revision.stats["embedded_pdf_source"] == "body.pdf"
    assert (
        revision.stats["embedded_pdf_sha256"]
        == hashlib.sha256(_VALID_MULTI_PARAGRAPH_PDF).hexdigest()
    )
    assert revision.stats["embedded_pdf_container_sha256"] == hashlib.sha256(archive).hexdigest()
    assert revision.stats["embedded_pdf_container_storage_key"] == StorageKeys.latex_tar(
        ids["paper_id"], "v1"
    )
    assert revision.stats["blocks"] > 5
    assert "body.pdf" not in json.dumps(revision.content)
    assert revision.stats["candidate_failures"][0]["format"] == "latex"
    assert revision.stats["candidate_failures"][0]["code"] == "embedded_pdf_wrapper"

    parsing_checkpoint = JobStore.get_checkpoint(job)["parsing"]
    assert parsing_checkpoint["source_storage_key"] == StorageKeys.latex_tar(ids["paper_id"], "v1")
    assert parsing_checkpoint["source_sha256"] == hashlib.sha256(archive).hexdigest()
    assert parsing_checkpoint["candidate_diagnostics"] == [
        {
            "kind": "embedded_pdf",
            "embedded_pdf_source": "body.pdf",
            "embedded_pdf_sha256": hashlib.sha256(_VALID_MULTI_PARAGRAPH_PDF).hexdigest(),
        }
    ]

    assets = (
        (
            await db_session.execute(
                select(SourceAsset).where(SourceAsset.paper_id == ids["paper_id"])
            )
        )
        .scalars()
        .all()
    )
    assets_by_kind = {asset.kind: asset for asset in assets}
    assert set(assets_by_kind) == {"arxiv_latex", "pdf"}
    storage = S3Storage()
    original_asset = assets_by_kind["pdf"]
    assert original_asset.storage_key == StorageKeys.original_pdf(ids["paper_id"], "v1")
    assert await storage.get(storage.sources_bucket, original_asset.storage_key) == original_pdf
    latex_asset = assets_by_kind["arxiv_latex"]
    assert latex_asset.storage_key == StorageKeys.latex_tar(ids["paper_id"], "v1")
    assert await storage.get(storage.sources_bucket, latex_asset.storage_key) == archive


@pytest.mark.parametrize(
    "provenance",
    ["none", "member_digest_mismatch"],
    ids=["existing-original-pdf", "same-member-name-mismatched-digest"],
)
async def test_fresh_embedded_candidate_rejects_mismatched_existing_pdf_revision(
    db_session: AsyncSession,
    router: LLMRouter,
    seed_ingest_job: Any,
    provenance: str,
) -> None:
    (
        ids,
        archive,
        original_pdf,
        revision_id,
        stored_content,
        stored_stats,
    ) = await _seed_existing_pdf_revision_for_embedded_selection(
        db_session,
        seed_ingest_job,
        provenance=provenance,
    )
    calls = {"pdf": 0, "eprint": 0, "html": 0}
    transport = httpx.ASGITransport(
        app=_make_embedded_pdf_wrapper_stub(
            calls,
            archive=archive,
            original_pdf=original_pdf,
        )
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
        with pytest.raises(FetchError) as error:
            await run_ingest(ctx, store, job)

    assert error.value.kind == "parse_error"
    assert calls == {"pdf": 0, "eprint": 1, "html": 0}
    revisions = (
        (
            await db_session.execute(
                select(DocumentRevision).where(DocumentRevision.paper_id == ids["paper_id"])
            )
        )
        .scalars()
        .all()
    )
    assert [str(revision.id) for revision in revisions] == [revision_id]
    assert revisions[0].content == stored_content
    assert revisions[0].stats == stored_stats
    paper = await db_session.get(Paper, ids["paper_id"])
    assert paper is not None
    assert paper.latest_revision_id is None

    assets = (
        (
            await db_session.execute(
                select(SourceAsset).where(SourceAsset.paper_id == ids["paper_id"])
            )
        )
        .scalars()
        .all()
    )
    assert [asset.kind for asset in assets].count("pdf") == 1
    assert [asset.kind for asset in assets].count("arxiv_latex") == 0
    storage = S3Storage()
    pdf_asset = next(asset for asset in assets if asset.kind == "pdf")
    assert await storage.get(storage.sources_bucket, pdf_asset.storage_key) == original_pdf


async def test_fresh_embedded_mismatch_preserves_existing_archive_and_checkpoint(
    db_session: AsyncSession,
    router: LLMRouter,
    seed_ingest_job: Any,
) -> None:
    (
        ids,
        old_archive,
        original_pdf,
        revision_id,
        stored_content,
        stored_stats,
    ) = await _seed_existing_pdf_revision_for_embedded_selection(
        db_session,
        seed_ingest_job,
        provenance="exact",
        embedded_pdf=_VALID_PRIORITY_PDF,
    )
    storage = S3Storage()
    latex_key = StorageKeys.latex_tar(ids["paper_id"], "v1")
    await storage.put(
        storage.sources_bucket,
        latex_key,
        old_archive,
        content_type="application/x-old-latex",
    )
    old_asset = SourceAsset(
        paper_id=ids["paper_id"],
        kind="arxiv_latex",
        source_url="http://old.example/e-print/source",
        source_version="v1",
        storage_key=latex_key,
        content_type="application/x-old-latex",
        byte_size=len(old_archive),
        sha256=hashlib.sha256(old_archive).hexdigest(),
    )
    db_session.add(old_asset)
    await db_session.commit()
    await db_session.refresh(old_asset)
    old_asset_id = str(old_asset.id)
    asset_snapshot = {
        "id": old_asset_id,
        "paper_id": str(old_asset.paper_id),
        "kind": old_asset.kind,
        "source_url": old_asset.source_url,
        "source_version": old_asset.source_version,
        "storage_key": old_asset.storage_key,
        "content_type": old_asset.content_type,
        "byte_size": old_asset.byte_size,
        "sha256": old_asset.sha256,
        "fetched_at": old_asset.fetched_at,
        "created_at": old_asset.created_at,
    }
    store = JobStore(db_session)
    job = await store.get(ids["job_id"])
    assert job is not None
    checkpoint_snapshot = json.loads(json.dumps(JobStore.get_checkpoint(job)))

    new_archive = _build_embedded_pdf_archive()
    calls = {"pdf": 0, "eprint": 0, "html": 0}
    transport = httpx.ASGITransport(
        app=_make_embedded_pdf_wrapper_stub(
            calls,
            archive=new_archive,
            original_pdf=original_pdf,
        )
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
        with pytest.raises(FetchError) as error:
            await run_ingest(ctx, store, job)

    assert error.value.kind == "parse_error"
    assert calls == {"pdf": 0, "eprint": 1, "html": 0}
    assert await storage.get(storage.sources_bucket, latex_key) == old_archive
    assert (
        await storage.get(
            storage.sources_bucket,
            StorageKeys.original_pdf(ids["paper_id"], "v1"),
        )
        == original_pdf
    )
    current_asset = await db_session.get(SourceAsset, old_asset_id)
    assert current_asset is not None
    assert {
        "id": str(current_asset.id),
        "paper_id": str(current_asset.paper_id),
        "kind": current_asset.kind,
        "source_url": current_asset.source_url,
        "source_version": current_asset.source_version,
        "storage_key": current_asset.storage_key,
        "content_type": current_asset.content_type,
        "byte_size": current_asset.byte_size,
        "sha256": current_asset.sha256,
        "fetched_at": current_asset.fetched_at,
        "created_at": current_asset.created_at,
    } == asset_snapshot
    revision = await db_session.get(DocumentRevision, revision_id)
    assert revision is not None
    assert revision.content == stored_content
    assert revision.stats == stored_stats
    paper = await db_session.get(Paper, ids["paper_id"])
    assert paper is not None
    assert paper.latest_revision_id is None
    job = await store.get(ids["job_id"])
    assert job is not None
    assert JobStore.get_checkpoint(job) == checkpoint_snapshot


async def test_fresh_embedded_candidate_reuses_exact_existing_pdf_revision(
    db_session: AsyncSession,
    router: LLMRouter,
    seed_ingest_job: Any,
) -> None:
    (
        ids,
        archive,
        original_pdf,
        revision_id,
        stored_content,
        stored_stats,
    ) = await _seed_existing_pdf_revision_for_embedded_selection(
        db_session,
        seed_ingest_job,
        provenance="exact",
    )
    calls = {"pdf": 0, "eprint": 0, "html": 0}
    transport = httpx.ASGITransport(
        app=_make_embedded_pdf_wrapper_stub(
            calls,
            archive=archive,
            original_pdf=original_pdf,
        )
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
        await run_ingest(ctx, store, job)

        assert calls == {"pdf": 0, "eprint": 1, "html": 0}
        job = await store.get(ids["job_id"])
        assert job is not None
        job.status = "queued"
        job.stage = "structuring"
        job.finished_at = None
        await db_session.commit()
        calls.update(pdf=0, eprint=0, html=0)
        resumed_job = await store.claim(ids["job_id"])
        assert resumed_job is not None
        await run_ingest(ctx, store, resumed_job)

    assert calls == {"pdf": 0, "eprint": 0, "html": 0}
    revisions = (
        (
            await db_session.execute(
                select(DocumentRevision).where(DocumentRevision.paper_id == ids["paper_id"])
            )
        )
        .scalars()
        .all()
    )
    assert [str(revision.id) for revision in revisions] == [revision_id]
    assert revisions[0].content == stored_content
    assert revisions[0].stats == stored_stats
    paper = await db_session.get(Paper, ids["paper_id"])
    assert paper is not None
    assert str(paper.latest_revision_id) == revision_id


@pytest.mark.parametrize(
    ("corruption", "expected_error"),
    [
        ("missing", "storage_error"),
        ("container", "storage_error"),
        ("member", "storage_error"),
        ("partial_identity", "parse_error"),
    ],
)
async def test_embedded_structuring_checkpoint_validates_retained_source(
    db_session: AsyncSession,
    router: LLMRouter,
    seed_ingest_job: Any,
    corruption: str,
    expected_error: str,
) -> None:
    (
        ids,
        archive,
        original_pdf,
        revision_id,
        _stored_content,
        _stored_stats,
    ) = await _seed_existing_pdf_revision_for_embedded_selection(
        db_session,
        seed_ingest_job,
        provenance="exact",
    )
    calls = {"pdf": 0, "eprint": 0, "html": 0}
    transport = httpx.ASGITransport(
        app=_make_embedded_pdf_wrapper_stub(
            calls,
            archive=archive,
            original_pdf=original_pdf,
        )
    )
    storage = S3Storage()
    latex_key = StorageKeys.latex_tar(ids["paper_id"], "v1")

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
        await run_ingest(ctx, store, job)
        assert calls == {"pdf": 0, "eprint": 1, "html": 0}

        changed_archive = _build_embedded_pdf_archive(_VALID_PRIORITY_PDF)
        if corruption == "missing":
            await storage.delete_many(storage.sources_bucket, [latex_key])
        elif corruption in {"container", "member"}:
            await storage.put(
                storage.sources_bucket,
                latex_key,
                changed_archive,
                content_type="application/gzip",
            )
        if corruption == "partial_identity":
            job = await store.get(ids["job_id"])
            assert job is not None
            parsing_checkpoint = dict(JobStore.get_checkpoint(job)["parsing"])
            parsing_checkpoint.pop("candidate_diagnostics")
            await store.checkpoint(
                ids["job_id"],
                "parsing",
                parsing_checkpoint,
                progress=20,
            )
            revision = await db_session.get(DocumentRevision, revision_id)
            assert revision is not None
            revision.stats = {
                key: value
                for key, value in revision.stats.items()
                if key
                not in {
                    "embedded_pdf_source",
                    "embedded_pdf_sha256",
                    "embedded_pdf_container_sha256",
                    "embedded_pdf_container_storage_key",
                }
            }
            await db_session.commit()
        if corruption == "member":
            job = await store.get(ids["job_id"])
            assert job is not None
            parsing_checkpoint = dict(JobStore.get_checkpoint(job)["parsing"])
            parsing_checkpoint["source_sha256"] = hashlib.sha256(changed_archive).hexdigest()
            await store.checkpoint(
                ids["job_id"],
                "parsing",
                parsing_checkpoint,
                progress=20,
            )
            revision = await db_session.get(DocumentRevision, revision_id)
            assert revision is not None
            revision.stats = {
                **revision.stats,
                "embedded_pdf_container_sha256": hashlib.sha256(changed_archive).hexdigest(),
            }
            latex_asset = (
                (
                    await db_session.execute(
                        select(SourceAsset).where(
                            SourceAsset.paper_id == ids["paper_id"],
                            SourceAsset.kind == "arxiv_latex",
                        )
                    )
                )
                .scalars()
                .one()
            )
            latex_asset.byte_size = len(changed_archive)
            latex_asset.sha256 = hashlib.sha256(changed_archive).hexdigest()
            await db_session.commit()

        revision = await db_session.get(DocumentRevision, revision_id)
        assert revision is not None
        revision_content = json.loads(json.dumps(revision.content))
        revision_stats = json.loads(json.dumps(revision.stats))
        job = await store.get(ids["job_id"])
        assert job is not None
        job.status = "queued"
        job.stage = "structuring"
        job.error = None
        job.finished_at = None
        await db_session.commit()
        calls.update(pdf=0, eprint=0, html=0)
        resumed_job = await store.claim(ids["job_id"])
        assert resumed_job is not None
        with pytest.raises(FetchError) as error:
            await run_ingest(ctx, store, resumed_job)

    assert error.value.kind == expected_error
    assert "body.pdf" not in str(error.value)
    assert calls == {"pdf": 0, "eprint": 0, "html": 0}
    revision = await db_session.get(DocumentRevision, revision_id)
    assert revision is not None
    assert revision.content == revision_content
    assert revision.stats == revision_stats


@pytest.mark.parametrize(
    "provenance",
    ["exact", "partial"],
    ids=["complete-embedded-provenance", "partial-embedded-provenance"],
)
async def test_fresh_original_pdf_candidate_rejects_existing_embedded_revision(
    db_session: AsyncSession,
    router: LLMRouter,
    seed_ingest_job: Any,
    provenance: str,
) -> None:
    (
        ids,
        _archive,
        _old_original_pdf,
        revision_id,
        stored_content,
        stored_stats,
    ) = await _seed_existing_pdf_revision_for_embedded_selection(
        db_session,
        seed_ingest_job,
        provenance=provenance,
    )
    storage = S3Storage()
    pdf_key = StorageKeys.original_pdf(ids["paper_id"], "v1")
    await storage.put(
        storage.sources_bucket,
        pdf_key,
        _VALID_MULTI_PARAGRAPH_PDF,
        content_type="application/pdf",
    )
    calls = {"pdf": 0, "eprint": 0, "html": 0}
    transport = httpx.ASGITransport(
        app=_make_counting_arxiv_stub(calls, pdf_status=500, latex_available=False)
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
        with pytest.raises(FetchError) as error:
            await run_ingest(ctx, store, job)

    assert error.value.kind == "parse_error"
    assert calls == {"pdf": 0, "eprint": 1, "html": 1}
    revisions = (
        (
            await db_session.execute(
                select(DocumentRevision).where(DocumentRevision.paper_id == ids["paper_id"])
            )
        )
        .scalars()
        .all()
    )
    assert [str(revision.id) for revision in revisions] == [revision_id]
    assert revisions[0].content == stored_content
    assert revisions[0].stats == stored_stats
    paper = await db_session.get(Paper, ids["paper_id"])
    assert paper is not None
    assert paper.latest_revision_id is None


async def test_embedded_pdf_parsing_checkpoint_reuses_exact_archive_member(
    db_session: AsyncSession,
    router: LLMRouter,
    seed_ingest_job: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    arxiv_id = _arxiv_id()
    ids = await seed_ingest_job(db_session, arxiv_id=arxiv_id)
    archive = _build_embedded_pdf_archive()
    original_pdf = _incomplete_original_pdf()
    calls = {"pdf": 0, "eprint": 0, "html": 0}
    transport = httpx.ASGITransport(
        app=_make_embedded_pdf_wrapper_stub(
            calls,
            archive=archive,
            original_pdf=original_pdf,
        )
    )

    async def crash_after_parsing_checkpoint(_run: IngestRun, _data: bytes) -> None:
        raise RuntimeError("simulated crash after parsing checkpoint")

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
        with monkeypatch.context() as patch:
            patch.setattr(IngestRun, "_structure_pdf", crash_after_parsing_checkpoint)
            with pytest.raises(RuntimeError, match="simulated crash"):
                await run_ingest(ctx, store, job)

        job = await store.get(ids["job_id"])
        assert job is not None
        parsing_checkpoint = JobStore.get_checkpoint(job)["parsing"]
        assert parsing_checkpoint["source_storage_key"] == StorageKeys.latex_tar(
            ids["paper_id"], "v1"
        )
        assert parsing_checkpoint["source_sha256"] == hashlib.sha256(archive).hexdigest()
        assert parsing_checkpoint["candidate_diagnostics"][0]["embedded_pdf_sha256"] == (
            hashlib.sha256(_VALID_MULTI_PARAGRAPH_PDF).hexdigest()
        )
        assert (
            await db_session.execute(
                select(DocumentRevision).where(DocumentRevision.paper_id == ids["paper_id"])
            )
        ).scalars().all() == []

        calls.update(pdf=0, eprint=0, html=0)
        job.status = "queued"
        job.stage = "parsing"
        job.error = None
        job.finished_at = None
        await db_session.commit()
        resumed_job = await store.claim(ids["job_id"])
        assert resumed_job is not None
        await run_ingest(ctx, store, resumed_job)

    assert calls == {"pdf": 0, "eprint": 0, "html": 0}
    revision = (
        (
            await db_session.execute(
                select(DocumentRevision).where(DocumentRevision.paper_id == ids["paper_id"])
            )
        )
        .scalars()
        .one()
    )
    assert revision.source_format == "pdf"
    assert revision.parser_version == PDF_PARSER_VERSION
    assert revision.stats["embedded_pdf_source"] == "body.pdf"
    assert "This paper studies things" in json.dumps(revision.content)
    assert "body.pdf" not in json.dumps(revision.content)

    assets = (
        (
            await db_session.execute(
                select(SourceAsset).where(SourceAsset.paper_id == ids["paper_id"])
            )
        )
        .scalars()
        .all()
    )
    assert [asset.kind for asset in assets].count("pdf") == 1
    assert [asset.kind for asset in assets].count("arxiv_latex") == 1
    storage = S3Storage()
    original_asset = next(asset for asset in assets if asset.kind == "pdf")
    assert await storage.get(storage.sources_bucket, original_asset.storage_key) == original_pdf


@pytest.mark.parametrize(
    ("archive", "expected_failure_codes"),
    [
        (
            _build_embedded_pdf_archive(_CORRUPT_PDF_LIKE),
            ["embedded_pdf_wrapper", "parse_error"],
        ),
        (
            _build_embedded_pdf_archive(additional_pdfs={"appendix.pdf": _VALID_PRIORITY_PDF}),
            ["embedded_pdf_wrapper"],
        ),
    ],
    ids=["embedded-parse-fails", "multiple-pdfs-are-ambiguous"],
)
async def test_embedded_pdf_wrapper_failure_continues_to_html(
    db_session: AsyncSession,
    router: LLMRouter,
    seed_ingest_job: Any,
    archive: bytes,
    expected_failure_codes: list[str],
) -> None:
    arxiv_id = _arxiv_id()
    ids = await seed_ingest_job(db_session, arxiv_id=arxiv_id)
    transport = httpx.ASGITransport(
        app=_make_candidate_status_stub(
            eprint_status=200,
            eprint_body=archive,
            html_status=200,
        )
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
    revision = (
        (
            await db_session.execute(
                select(DocumentRevision).where(DocumentRevision.paper_id == ids["paper_id"])
            )
        )
        .scalars()
        .one()
    )
    assert revision.source_format == "arxiv_html"
    assert [failure["code"] for failure in revision.stats["candidate_failures"]] == (
        expected_failure_codes
    )
    if len(expected_failure_codes) == 2:
        assert revision.stats["candidate_failures"][1]["embedded_pdf_source"] == "body.pdf"
    kinds = await _source_asset_kinds(db_session, ids["paper_id"])
    assert kinds == {"arxiv_html", "pdf"}


@pytest.mark.parametrize(
    ("checkpoint_member", "stored_member_bytes", "expected_error"),
    [
        ("missing.pdf", _VALID_MULTI_PARAGRAPH_PDF, "parse_error"),
        ("../body.pdf", _VALID_MULTI_PARAGRAPH_PDF, "parse_error"),
        ("body.pdf", _VALID_PRIORITY_PDF, "storage_error"),
        ("body.pdf", _VALID_MULTI_PARAGRAPH_PDF, "parse_error"),
    ],
    ids=[
        "missing-member",
        "unsafe-member",
        "changed-member",
        "existing-revision-digest-mismatch",
    ],
)
async def test_embedded_pdf_parsing_checkpoint_rejects_invalid_member_identity(
    db_session: AsyncSession,
    router: LLMRouter,
    seed_ingest_job: Any,
    checkpoint_member: str,
    stored_member_bytes: bytes,
    expected_error: str,
) -> None:
    arxiv_id = _arxiv_id()
    ids = await seed_ingest_job(db_session, arxiv_id=arxiv_id)
    original_pdf = _incomplete_original_pdf()
    archive = _build_embedded_pdf_archive(stored_member_bytes)
    storage = S3Storage()
    pdf_key = StorageKeys.original_pdf(ids["paper_id"], "v1")
    latex_key = StorageKeys.latex_tar(ids["paper_id"], "v1")
    await storage.put(storage.sources_bucket, pdf_key, original_pdf, content_type="application/pdf")
    await storage.put(
        storage.sources_bucket,
        latex_key,
        archive,
        content_type="application/gzip",
    )
    stored_revision_stats = {
        "candidate_failures": [],
        "completeness": {"accepted": True, "code": None},
        "embedded_pdf_source": "body.pdf",
        "embedded_pdf_sha256": "0" * 64,
        "embedded_pdf_container_sha256": hashlib.sha256(archive).hexdigest(),
        "embedded_pdf_container_storage_key": latex_key,
    }
    db_session.add_all(
        [
            SourceAsset(
                paper_id=ids["paper_id"],
                kind="pdf",
                source_url=f"http://arxiv.test/pdf/{arxiv_id}v1",
                source_version="v1",
                storage_key=pdf_key,
                content_type="application/pdf",
                byte_size=len(original_pdf),
                sha256=hashlib.sha256(original_pdf).hexdigest(),
            ),
            SourceAsset(
                paper_id=ids["paper_id"],
                kind="arxiv_latex",
                source_url=f"http://arxiv.test/e-print/{arxiv_id}v1",
                source_version="v1",
                storage_key=latex_key,
                content_type="application/gzip",
                byte_size=len(archive),
                sha256=hashlib.sha256(archive).hexdigest(),
            ),
            DocumentRevision(
                paper_id=ids["paper_id"],
                source_version="v1",
                parser_version=PDF_PARSER_VERSION,
                quality_level="B",
                source_format="pdf",
                content=DocumentContent(quality_level="B", sections=[]).model_dump(),
                stats=stored_revision_stats,
            ),
        ]
    )
    await db_session.commit()

    store = JobStore(db_session)
    await store.checkpoint(ids["job_id"], "fetching", {"source_version": "v1"}, progress=10)
    await store.checkpoint(
        ids["job_id"],
        "parsing",
        {
            "source_format": "pdf",
            "parser_version": PDF_PARSER_VERSION,
            "candidate_failures": [{"format": "latex", "code": "embedded_pdf_wrapper"}],
            "candidate_diagnostics": [
                {
                    "kind": "embedded_pdf",
                    "embedded_pdf_source": checkpoint_member,
                    "embedded_pdf_sha256": hashlib.sha256(_VALID_MULTI_PARAGRAPH_PDF).hexdigest(),
                }
            ],
            "completeness": {"accepted": True, "code": None},
            "adopt_from_revision_id": None,
            "source_storage_key": latex_key,
            "source_sha256": hashlib.sha256(archive).hexdigest(),
        },
        progress=20,
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
        with pytest.raises(FetchError) as error:
            await run_ingest(ctx, store, job)

    assert error.value.kind == expected_error
    assert checkpoint_member not in str(error.value)
    assert calls == {"pdf": 0, "eprint": 0, "html": 0}
    paper = await db_session.get(Paper, ids["paper_id"])
    assert paper is not None
    assert paper.latest_revision_id is None
    revision = (
        (
            await db_session.execute(
                select(DocumentRevision).where(DocumentRevision.paper_id == ids["paper_id"])
            )
        )
        .scalars()
        .one()
    )
    assert revision.stats == stored_revision_stats


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
    assert rev.parser_version == HTML_PARSER_VERSION
    assert rev.stats["candidate_failures"][0]["format"] == "latex"
    assert rev.stats["completeness"]["accepted"] is True

    kinds = await _source_asset_kinds(db_session, ids["paper_id"])
    assert "arxiv_html" in kinds
    assert "arxiv_latex" not in kinds
    assert "pdf" in kinds

    warn_entries = [row for row in job.log if row.get("level") == "warn"]
    assert any("LaTeX" in row.get("message", "") for row in warn_entries)


async def test_html_inline_svg_is_rasterized_and_raw_is_not_persisted(
    db_session: AsyncSession,
    router: LLMRouter,
    seed_ingest_job: Any,
) -> None:
    ids = await seed_ingest_job(db_session, arxiv_id=_arxiv_id())
    transport = httpx.ASGITransport(
        app=_make_candidate_status_stub(
            eprint_status=404,
            eprint_body=b"",
            html_status=200,
            html_body=_VALID_INLINE_SVG_HTML,
        )
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

    revision = (
        (
            await db_session.execute(
                select(DocumentRevision).where(DocumentRevision.paper_id == ids["paper_id"])
            )
        )
        .scalars()
        .one()
    )
    figure = next(
        block
        for _section, block in DocumentContent.model_validate(revision.content).iter_blocks()
        if block.type == "figure"
    )
    assert figure.raw is None
    assert figure.asset_key is not None and figure.asset_key.endswith(".png")
    assert revision.stats["figure_asset_failures"] == []
    stored = await S3Storage().get(S3Storage().assets_bucket, figure.asset_key)
    assert stored.startswith(b"\x89PNG")


async def test_reingest_creates_repaired_revision_after_html_parser_rollout(
    db_session: AsyncSession,
    router: LLMRouter,
    seed_ingest_job: Any,
) -> None:
    """A parser rollout must not reuse an unsafe revision from the previous parser."""

    arxiv_id = _arxiv_id()
    ids = await seed_ingest_job(db_session, arxiv_id=arxiv_id)
    legacy = DocumentRevision(
        paper_id=ids["paper_id"],
        source_version="v1",
        parser_version="html-1.0.0",
        quality_level="A",
        source_format="arxiv_html",
        content=DocumentContent(quality_level="A", sections=[]).model_dump(),
        stats={
            "candidate_failures": [],
            "completeness": {"accepted": True, "code": None},
        },
    )
    db_session.add(legacy)
    await db_session.flush()
    paper = await db_session.get(Paper, ids["paper_id"])
    assert paper is not None
    paper.latest_revision_id = legacy.id
    await db_session.commit()

    transport = httpx.ASGITransport(
        app=_make_candidate_status_stub(
            eprint_status=404,
            eprint_body=b"",
            html_status=200,
            html_body=_VALID_INLINE_SVG_HTML,
        )
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

    revisions = (
        (
            await db_session.execute(
                select(DocumentRevision)
                .where(DocumentRevision.paper_id == ids["paper_id"])
                .order_by(DocumentRevision.created_at)
            )
        )
        .scalars()
        .all()
    )
    assert [revision.parser_version for revision in revisions] == [
        "html-1.0.0",
        HTML_PARSER_VERSION,
    ]
    repaired = revisions[-1]
    figure = next(
        block
        for _section, block in DocumentContent.model_validate(repaired.content).iter_blocks()
        if block.type == "figure"
    )
    assert figure.raw is None
    assert figure.asset_key is not None and figure.asset_key.endswith(".png")
    await db_session.refresh(paper)
    assert paper.latest_revision_id == repaired.id


async def test_ingest_backfills_diagnostics_on_existing_same_parser_revision(
    db_session: AsyncSession,
    worker_ctx: dict[str, Any],
    seed_ingest_job: Any,
) -> None:
    arxiv_id = _arxiv_id()
    ids = await seed_ingest_job(db_session, arxiv_id=arxiv_id)
    store = JobStore(db_session)
    job = await store.claim(ids["job_id"])
    assert job is not None
    await ingest_paper(worker_ctx, store, job)

    revision = (
        (
            await db_session.execute(
                select(DocumentRevision).where(DocumentRevision.paper_id == ids["paper_id"])
            )
        )
        .scalars()
        .one()
    )
    revision_id = str(revision.id)
    revision.stats = {"legacy": True}
    await db_session.commit()

    job_id = await store.enqueue(
        kind="ingest",
        payload={
            "mode": "reingest",
            "source": "arxiv",
            "arxiv_id": arxiv_id,
            "library_item_id": ids["library_item_id"],
        },
        priority="bulk",
        user_id=ids["user_id"],
        paper_id=ids["paper_id"],
        library_item_id=ids["library_item_id"],
    )
    job = await store.claim(job_id)
    assert job is not None
    await ingest_paper(worker_ctx, store, job)

    revisions = (
        (
            await db_session.execute(
                select(DocumentRevision).where(DocumentRevision.paper_id == ids["paper_id"])
            )
        )
        .scalars()
        .all()
    )
    assert [str(item.id) for item in revisions] == [revision_id]
    assert revisions[0].stats["completeness"]["accepted"] is True
    assert revisions[0].stats["candidate_failures"][0]["format"] == "latex"


async def test_ingest_rejects_incomplete_existing_same_parser_revision(
    db_session: AsyncSession,
    worker_ctx: dict[str, Any],
    seed_ingest_job: Any,
) -> None:
    arxiv_id = _arxiv_id()
    ids = await seed_ingest_job(db_session, arxiv_id=arxiv_id)
    store = JobStore(db_session)
    job = await store.claim(ids["job_id"])
    assert job is not None
    await ingest_paper(worker_ctx, store, job)

    revision = (
        (
            await db_session.execute(
                select(DocumentRevision).where(DocumentRevision.paper_id == ids["paper_id"])
            )
        )
        .scalars()
        .one()
    )
    revision.content = {"quality_level": "A", "sections": []}
    revision.stats = {"legacy": True}
    await db_session.commit()

    job_id = await store.enqueue(
        kind="ingest",
        payload={
            "mode": "reingest",
            "source": "arxiv",
            "arxiv_id": arxiv_id,
            "library_item_id": ids["library_item_id"],
        },
        priority="bulk",
        user_id=ids["user_id"],
        paper_id=ids["paper_id"],
        library_item_id=ids["library_item_id"],
    )
    job = await store.claim(job_id)
    assert job is not None
    await ingest_paper(worker_ctx, store, job)

    job = await store.get(job_id)
    assert job is not None
    assert job.status == "failed"
    assert json.loads(job.error or "{}")["code"] == "document_incomplete"
    await db_session.refresh(revision)
    assert revision.stats["completeness"]["accepted"] is False


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


async def test_ingest_reconciles_duplicate_pdf_assets_deterministically(
    db_session: AsyncSession,
    router: LLMRouter,
    seed_ingest_job: Any,
) -> None:
    arxiv_id = _arxiv_id()
    ids = await seed_ingest_job(db_session, arxiv_id=arxiv_id)
    storage = S3Storage()
    prefix = f"sources/{ids['paper_id']}/v1"
    later_key = f"{prefix}/z-original.pdf"
    preferred_key = f"{prefix}/a-original.pdf"
    latest_prefix = f"sources/{ids['paper_id']}/latest"
    latest_keys = [f"{latest_prefix}/a-original.pdf", f"{latest_prefix}/z-original.pdf"]
    await storage.put(
        storage.sources_bucket,
        later_key,
        _VALID_MULTI_PARAGRAPH_PDF,
        content_type="application/pdf",
    )
    await storage.put(
        storage.sources_bucket,
        preferred_key,
        _VALID_PRIORITY_PDF,
        content_type="application/pdf",
    )
    for latest_key in latest_keys:
        await storage.put(
            storage.sources_bucket,
            latest_key,
            _VALID_MULTI_PARAGRAPH_PDF,
            content_type="application/pdf",
        )
    later_asset = SourceAsset(
        paper_id=ids["paper_id"],
        kind="pdf",
        source_url=f"http://arxiv.test/pdf/{arxiv_id}v1",
        source_version="v1",
        storage_key=later_key,
        content_type="application/pdf",
        byte_size=len(_VALID_MULTI_PARAGRAPH_PDF),
    )
    preferred_asset = SourceAsset(
        paper_id=ids["paper_id"],
        kind="pdf",
        source_url=f"http://arxiv.test/pdf/{arxiv_id}v1",
        source_version="v1",
        storage_key=preferred_key,
        content_type="application/pdf",
        byte_size=len(_VALID_PRIORITY_PDF),
    )
    latest_assets = [
        SourceAsset(
            paper_id=ids["paper_id"],
            kind="pdf",
            source_url=f"http://arxiv.test/pdf/{arxiv_id}",
            source_version="latest",
            storage_key=latest_key,
            content_type="application/pdf",
            byte_size=len(_VALID_MULTI_PARAGRAPH_PDF),
        )
        for latest_key in latest_keys
    ]
    # Deliberately insert the lexically later exact key first. Reconciliation must
    # normalize every compatible exact/latest row, independent of heap order.
    db_session.add_all([later_asset, preferred_asset, *latest_assets])
    await db_session.commit()

    store = JobStore(db_session)
    await store.checkpoint(ids["job_id"], "fetching", {"source_version": "v1"}, progress=10)
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

    canonical_key = StorageKeys.original_pdf(ids["paper_id"], "v1")
    assert calls["pdf"] == 0
    assert await storage.get(storage.sources_bucket, canonical_key) == _VALID_PRIORITY_PDF
    pdf_assets = (
        (
            await db_session.execute(
                select(SourceAsset).where(
                    SourceAsset.paper_id == ids["paper_id"],
                    SourceAsset.kind == "pdf",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(pdf_assets) == 4
    expected_sha256 = hashlib.sha256(_VALID_PRIORITY_PDF).hexdigest()
    assert {asset.storage_key for asset in pdf_assets} == {canonical_key}
    assert {asset.source_version for asset in pdf_assets} == {"v1"}
    assert {asset.content_type for asset in pdf_assets} == {"application/pdf"}
    assert {asset.byte_size for asset in pdf_assets} == {len(_VALID_PRIORITY_PDF)}
    assert {asset.sha256 for asset in pdf_assets} == {expected_sha256}


async def test_ingest_prefers_canonical_pdf_over_stale_exact_asset(
    db_session: AsyncSession,
    router: LLMRouter,
    seed_ingest_job: Any,
) -> None:
    arxiv_id = _arxiv_id()
    ids = await seed_ingest_job(db_session, arxiv_id=arxiv_id)
    storage = S3Storage()
    canonical_key = StorageKeys.original_pdf(ids["paper_id"], "v1")
    stale_key = f"sources/{ids['paper_id']}/v1/000-stale-original.pdf"
    await storage.put(
        storage.sources_bucket,
        canonical_key,
        _VALID_PRIORITY_PDF,
        content_type="application/pdf",
    )
    await storage.put(
        storage.sources_bucket,
        stale_key,
        _VALID_MULTI_PARAGRAPH_PDF,
        content_type="application/pdf",
    )
    db_session.add(
        SourceAsset(
            paper_id=ids["paper_id"],
            kind="pdf",
            source_url=f"http://arxiv.test/pdf/{arxiv_id}v1",
            source_version="v1",
            storage_key=stale_key,
            content_type="application/pdf",
            byte_size=len(_VALID_MULTI_PARAGRAPH_PDF),
        )
    )
    await db_session.commit()
    store = JobStore(db_session)
    await store.checkpoint(ids["job_id"], "fetching", {"source_version": "v1"}, progress=10)

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

    assert calls["pdf"] == 0
    assert await storage.get(storage.sources_bucket, canonical_key) == _VALID_PRIORITY_PDF


async def test_ingest_reuses_api_prefetched_latest_pdf_without_network_fetch(
    db_session: AsyncSession,
    router: LLMRouter,
    seed_ingest_job: Any,
) -> None:
    arxiv_id = _arxiv_id()
    ids = await seed_ingest_job(db_session, arxiv_id=arxiv_id)
    storage = S3Storage()
    resolved_key = StorageKeys.original_pdf(ids["paper_id"], "v1")
    key = StorageKeys.original_pdf(ids["paper_id"], "latest")
    await storage.put(
        storage.sources_bucket, key, _VALID_PRIORITY_PDF, content_type="application/pdf"
    )
    latest_asset = SourceAsset(
        paper_id=ids["paper_id"],
        kind="pdf",
        source_url=f"http://arxiv.test/pdf/{arxiv_id}",
        source_version="latest",
        storage_key=key,
        content_type="application/pdf",
        byte_size=len(_VALID_PRIORITY_PDF),
    )
    db_session.add(latest_asset)
    await db_session.flush()
    latest_asset_id = str(latest_asset.id)
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
    assert await storage.get(storage.sources_bucket, resolved_key) == _VALID_PRIORITY_PDF
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
    assert str(pdf_assets[0].id) == latest_asset_id
    assert pdf_assets[0].source_version == "v1"
    assert pdf_assets[0].storage_key == resolved_key


async def test_ingest_explicit_version_does_not_reuse_latest_pdf_alias(
    db_session: AsyncSession,
    router: LLMRouter,
    seed_ingest_job: Any,
) -> None:
    base_arxiv_id = _arxiv_id()
    arxiv_id = f"{base_arxiv_id}v1"
    ids = await seed_ingest_job(db_session, arxiv_id=arxiv_id)
    storage = S3Storage()
    latest_key = StorageKeys.original_pdf(ids["paper_id"], "latest")
    await storage.put(
        storage.sources_bucket,
        latest_key,
        _VALID_PRIORITY_PDF,
        content_type="application/pdf",
    )
    latest_asset = SourceAsset(
        paper_id=ids["paper_id"],
        kind="pdf",
        source_url=f"http://arxiv.test/pdf/{base_arxiv_id}",
        source_version="latest",
        storage_key=latest_key,
        content_type="application/pdf",
        byte_size=len(_VALID_PRIORITY_PDF),
    )
    db_session.add(latest_asset)
    await db_session.flush()
    latest_asset_id = str(latest_asset.id)
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
    assert json.loads(job.error or "{}")["code"] == "source_not_found"
    assert calls == {"pdf": 1, "eprint": 0, "html": 0}
    await db_session.refresh(latest_asset)
    assert str(latest_asset.id) == latest_asset_id
    assert latest_asset.source_version == "latest"
    assert latest_asset.storage_key == latest_key


async def test_ingest_prefers_resolved_canonical_pdf_over_latest_alias(
    db_session: AsyncSession,
    router: LLMRouter,
    seed_ingest_job: Any,
) -> None:
    arxiv_id = _arxiv_id()
    ids = await seed_ingest_job(db_session, arxiv_id=arxiv_id)
    storage = S3Storage()
    resolved_key = StorageKeys.original_pdf(ids["paper_id"], "v1")
    latest_key = StorageKeys.original_pdf(ids["paper_id"], "latest")
    await storage.put(
        storage.sources_bucket,
        resolved_key,
        _VALID_PRIORITY_PDF,
        content_type="application/pdf",
    )
    await storage.put(
        storage.sources_bucket,
        latest_key,
        _VALID_MULTI_PARAGRAPH_PDF,
        content_type="application/pdf",
    )
    db_session.add(
        SourceAsset(
            paper_id=ids["paper_id"],
            kind="pdf",
            source_url=f"http://arxiv.test/pdf/{arxiv_id}",
            source_version="latest",
            storage_key=latest_key,
            content_type="application/pdf",
            byte_size=len(_VALID_MULTI_PARAGRAPH_PDF),
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

    assert calls["pdf"] == 0
    assert await storage.get(storage.sources_bucket, resolved_key) == _VALID_PRIORITY_PDF


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
    pdf_asset = SourceAsset(
        paper_id=ids["paper_id"],
        kind="pdf",
        source_url=f"http://arxiv.test/pdf/{arxiv_id}v1",
        source_version="v1",
        storage_key=key,
        content_type="application/pdf",
        byte_size=len(_VALID_PRIORITY_PDF),
    )
    db_session.add(pdf_asset)
    await db_session.flush()
    pdf_asset_id = str(pdf_asset.id)
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

        revision = (
            (
                await db_session.execute(
                    select(DocumentRevision).where(DocumentRevision.paper_id == ids["paper_id"])
                )
            )
            .scalars()
            .one()
        )
        revision.stats = {}
        await db_session.commit()

        for _attempt in range(2):
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
            pdf_assets = (
                (
                    await db_session.execute(
                        select(SourceAsset).where(
                            SourceAsset.paper_id == ids["paper_id"],
                            SourceAsset.kind == "pdf",
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert len(pdf_assets) == 1
            assert str(pdf_assets[0].id) == pdf_asset_id
            refreshed_revision = await db_session.get(DocumentRevision, revision.id)
            assert refreshed_revision is not None
            assert refreshed_revision.stats["completeness"]["accepted"] is True
            assert refreshed_revision.stats["candidate_failures"][0]["code"] == (
                "historical_diagnostics_unavailable"
            )

    revisions = (
        await db_session.execute(
            select(DocumentRevision).where(DocumentRevision.paper_id == ids["paper_id"])
        )
    ).scalars()
    assert len(revisions.all()) == 1


async def test_ingest_parsing_checkpoint_reuses_stored_candidate_without_reselection(
    db_session: AsyncSession,
    router: LLMRouter,
    seed_ingest_job: Any,
) -> None:
    arxiv_id = _arxiv_id()
    ids = await seed_ingest_job(db_session, arxiv_id=arxiv_id)
    storage = S3Storage()
    pdf_key = StorageKeys.original_pdf(ids["paper_id"], "v1")
    html_key = StorageKeys.arxiv_html(ids["paper_id"], "v1")
    stale_html_key = f"sources/{ids['paper_id']}/v1/000-stale.html"
    await storage.put(
        storage.sources_bucket,
        pdf_key,
        _VALID_PRIORITY_PDF,
        content_type="application/pdf",
    )
    await storage.put(
        storage.sources_bucket,
        html_key,
        _VALID_STORED_HTML,
        content_type="text/html; charset=utf-8",
    )
    await storage.put(
        storage.sources_bucket,
        stale_html_key,
        _VALID_STALE_HTML,
        content_type="text/html; charset=utf-8",
    )
    db_session.add_all(
        [
            SourceAsset(
                paper_id=ids["paper_id"],
                kind="pdf",
                source_url=f"http://arxiv.test/pdf/{arxiv_id}v1",
                source_version="v1",
                storage_key=pdf_key,
                content_type="application/pdf",
                byte_size=len(_VALID_PRIORITY_PDF),
            ),
            SourceAsset(
                paper_id=ids["paper_id"],
                kind="arxiv_html",
                source_url=f"http://arxiv.test/html/{arxiv_id}v1",
                source_version="v1",
                storage_key=html_key,
                content_type="text/html",
                byte_size=len(_VALID_STORED_HTML),
            ),
            SourceAsset(
                paper_id=ids["paper_id"],
                kind="arxiv_html",
                source_url=f"http://arxiv.test/html/{arxiv_id}v1",
                source_version="v1",
                storage_key=stale_html_key,
                content_type="text/html",
                byte_size=len(_VALID_STALE_HTML),
            ),
        ]
    )
    await db_session.commit()

    stored_failures = [
        {
            "format": "latex",
            "code": "source_not_found",
            "message": "arxiv e-print was unavailable",
        }
    ]
    store = JobStore(db_session)
    await store.checkpoint(ids["job_id"], "fetching", {"source_version": "v1"}, progress=10)
    await store.checkpoint(
        ids["job_id"],
        "parsing",
        {
            "source_format": "arxiv_html",
            "parser_version": HTML_PARSER_VERSION,
            "candidate_failures": stored_failures,
            "completeness": {"accepted": True, "code": None},
            "adopt_from_revision_id": None,
            "source_storage_key": html_key,
            "source_sha256": hashlib.sha256(_VALID_STORED_HTML).hexdigest(),
        },
        progress=20,
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

    assert calls == {"pdf": 0, "eprint": 0, "html": 0}
    revision = (
        (
            await db_session.execute(
                select(DocumentRevision).where(DocumentRevision.paper_id == ids["paper_id"])
            )
        )
        .scalars()
        .one()
    )
    assert revision.source_format == "arxiv_html"
    assert "The stored candidate" in json.dumps(revision.content)
    assert "The stale duplicate" not in json.dumps(revision.content)
    assert revision.stats["candidate_failures"] == stored_failures
    assert revision.stats["completeness"]["accepted"] is True


@pytest.mark.parametrize(
    "parsing_checkpoint",
    [
        {"parser_version": HTML_PARSER_VERSION},
        {
            "source_format": "arxiv_html",
            "parser_version": HTML_PARSER_VERSION,
            "candidate_failures": "not-a-list",
        },
    ],
    ids=["missing-source-format", "candidate-failures-not-list"],
)
async def test_ingest_rejects_malformed_parsing_checkpoint_without_reselection(
    db_session: AsyncSession,
    router: LLMRouter,
    seed_ingest_job: Any,
    parsing_checkpoint: dict[str, Any],
) -> None:
    arxiv_id = _arxiv_id()
    ids = await seed_ingest_job(db_session, arxiv_id=arxiv_id)
    storage = S3Storage()
    pdf_key = StorageKeys.original_pdf(ids["paper_id"], "v1")
    await storage.put(
        storage.sources_bucket,
        pdf_key,
        _VALID_PRIORITY_PDF,
        content_type="application/pdf",
    )
    db_session.add(
        SourceAsset(
            paper_id=ids["paper_id"],
            kind="pdf",
            source_url=f"http://arxiv.test/pdf/{arxiv_id}v1",
            source_version="v1",
            storage_key=pdf_key,
            content_type="application/pdf",
            byte_size=len(_VALID_PRIORITY_PDF),
        )
    )
    await db_session.commit()

    store = JobStore(db_session)
    await store.checkpoint(ids["job_id"], "fetching", {"source_version": "v1"}, progress=10)
    await store.checkpoint(
        ids["job_id"],
        "parsing",
        parsing_checkpoint,
        progress=20,
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
        with pytest.raises(FetchError) as error:
            await run_ingest(ctx, store, job)

    assert error.value.kind == "parse_error"
    assert calls == {"pdf": 0, "eprint": 0, "html": 0}


async def test_ingest_rejects_malformed_structuring_checkpoint_without_reselection(
    db_session: AsyncSession,
    router: LLMRouter,
    seed_ingest_job: Any,
) -> None:
    arxiv_id = _arxiv_id()
    ids = await seed_ingest_job(db_session, arxiv_id=arxiv_id)
    storage = S3Storage()
    pdf_key = StorageKeys.original_pdf(ids["paper_id"], "v1")
    await storage.put(
        storage.sources_bucket,
        pdf_key,
        _VALID_PRIORITY_PDF,
        content_type="application/pdf",
    )
    db_session.add(
        SourceAsset(
            paper_id=ids["paper_id"],
            kind="pdf",
            source_url=f"http://arxiv.test/pdf/{arxiv_id}v1",
            source_version="v1",
            storage_key=pdf_key,
            content_type="application/pdf",
            byte_size=len(_VALID_PRIORITY_PDF),
        )
    )
    await db_session.commit()
    store = JobStore(db_session)
    await store.checkpoint(ids["job_id"], "fetching", {"source_version": "v1"}, progress=10)
    await store.checkpoint(ids["job_id"], "structuring", {}, progress=35)

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
        with pytest.raises(FetchError) as error:
            await run_ingest(ctx, store, job)

    assert error.value.kind == "parse_error"
    assert calls == {"pdf": 0, "eprint": 0, "html": 0}


async def test_ingest_parsing_checkpoint_finds_exact_persisted_parser_revision(
    db_session: AsyncSession,
    router: LLMRouter,
    seed_ingest_job: Any,
) -> None:
    arxiv_id = _arxiv_id()
    ids = await seed_ingest_job(db_session, arxiv_id=arxiv_id)
    storage = S3Storage()
    pdf_key = StorageKeys.original_pdf(ids["paper_id"], "v1")
    await storage.put(
        storage.sources_bucket,
        pdf_key,
        _VALID_PRIORITY_PDF,
        content_type="application/pdf",
    )
    candidate = parse_html_candidate(_VALID_STORED_HTML, pdf_text="")
    revision = DocumentRevision(
        paper_id=ids["paper_id"],
        source_version="v1",
        parser_version="html-0.9.0",
        quality_level="A",
        source_format="arxiv_html",
        content=candidate.content.model_dump(),
        stats={"candidate_failures": [], "completeness": candidate.report.as_dict()},
    )
    db_session.add_all(
        [
            SourceAsset(
                paper_id=ids["paper_id"],
                kind="pdf",
                source_url=f"http://arxiv.test/pdf/{arxiv_id}v1",
                source_version="v1",
                storage_key=pdf_key,
                content_type="application/pdf",
                byte_size=len(_VALID_PRIORITY_PDF),
            ),
            revision,
        ]
    )
    await db_session.flush()
    revision_id = str(revision.id)
    await db_session.commit()

    store = JobStore(db_session)
    await store.checkpoint(ids["job_id"], "fetching", {"source_version": "v1"}, progress=10)
    await store.checkpoint(
        ids["job_id"],
        "parsing",
        {
            "source_format": "arxiv_html",
            "parser_version": "html-0.9.0",
            "candidate_failures": [],
            "completeness": candidate.report.as_dict(),
            "adopt_from_revision_id": None,
        },
        progress=20,
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

    assert calls == {"pdf": 0, "eprint": 0, "html": 0}
    revisions = (
        (
            await db_session.execute(
                select(DocumentRevision).where(DocumentRevision.paper_id == ids["paper_id"])
            )
        )
        .scalars()
        .all()
    )
    assert [str(item.id) for item in revisions] == [revision_id]


async def test_ingest_rejects_incomplete_legacy_structuring_checkpoint(
    db_session: AsyncSession,
    worker_ctx: dict[str, Any],
    seed_ingest_job: Any,
) -> None:
    arxiv_id = _arxiv_id()
    ids = await seed_ingest_job(db_session, arxiv_id=arxiv_id)
    storage = S3Storage()
    pdf_key = StorageKeys.original_pdf(ids["paper_id"], "v1")
    await storage.put(
        storage.sources_bucket,
        pdf_key,
        _VALID_PRIORITY_PDF,
        content_type="application/pdf",
    )
    db_session.add(
        SourceAsset(
            paper_id=ids["paper_id"],
            kind="pdf",
            source_url=f"http://arxiv.test/pdf/{arxiv_id}v1",
            source_version="v1",
            storage_key=pdf_key,
            content_type="application/pdf",
            byte_size=len(_VALID_PRIORITY_PDF),
        )
    )
    legacy_revision = DocumentRevision(
        paper_id=ids["paper_id"],
        source_version="v1",
        parser_version="html-1.0.0",
        quality_level="A",
        source_format="arxiv_html",
        content={"quality_level": "A", "sections": []},
        stats={},
    )
    db_session.add(legacy_revision)
    await db_session.flush()
    paper = await db_session.get(Paper, ids["paper_id"])
    assert paper is not None
    paper.latest_revision_id = legacy_revision.id
    await db_session.commit()

    store = JobStore(db_session)
    await store.checkpoint(ids["job_id"], "fetching", {"source_version": "v1"}, progress=10)
    await store.checkpoint(
        ids["job_id"],
        "structuring",
        {"revision_id": str(legacy_revision.id)},
        progress=35,
    )
    job = await store.claim(ids["job_id"])
    assert job is not None
    with pytest.raises(FetchError) as error:
        await run_ingest(worker_ctx, store, job)

    assert error.value.kind == "document_incomplete"
    await db_session.refresh(legacy_revision)
    assert legacy_revision.stats["completeness"]["accepted"] is False
    assert legacy_revision.stats["candidate_failures"][0]["code"] == (
        "historical_diagnostics_unavailable"
    )


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
    assert "cache=canonical_missing,asset_missing" in error["message"]
    assert calls == {"pdf": 1, "eprint": 0, "html": 0}
    revisions = (
        await db_session.execute(
            select(DocumentRevision).where(DocumentRevision.paper_id == ids["paper_id"])
        )
    ).scalars()
    assert revisions.all() == []


@pytest.mark.parametrize(
    (
        "eprint_status",
        "eprint_body",
        "html_status",
        "accepted_format",
        "failure_index",
        "expected_code",
    ),
    [
        (408, b"", 200, "arxiv_html", 0, "network_error"),
        (429, b"", 200, "arxiv_html", 0, "rate_limited"),
        (200, b"not a latex archive", 408, "pdf", 1, "network_error"),
        (200, b"not a latex archive", 429, "pdf", 1, "rate_limited"),
    ],
)
async def test_optional_candidate_http_status_records_retryable_diagnostic(
    db_session: AsyncSession,
    router: LLMRouter,
    seed_ingest_job: Any,
    eprint_status: int,
    eprint_body: bytes,
    html_status: int,
    accepted_format: str,
    failure_index: int,
    expected_code: str,
) -> None:
    arxiv_id = _arxiv_id()
    ids = await seed_ingest_job(db_session, arxiv_id=arxiv_id)
    transport = httpx.ASGITransport(
        app=_make_candidate_status_stub(
            eprint_status=eprint_status,
            eprint_body=eprint_body,
            html_status=html_status,
        )
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
    assert job.status == "succeeded"
    revision = (
        (
            await db_session.execute(
                select(DocumentRevision).where(DocumentRevision.paper_id == ids["paper_id"])
            )
        )
        .scalars()
        .one()
    )
    assert revision.source_format == accepted_format
    assert revision.stats["candidate_failures"][failure_index]["code"] == expected_code


async def test_all_candidate_failures_raise_first_retryable_error(
    db_session: AsyncSession,
    router: LLMRouter,
    seed_ingest_job: Any,
) -> None:
    arxiv_id = _arxiv_id()
    ids = await seed_ingest_job(db_session, arxiv_id=arxiv_id)
    transport = httpx.ASGITransport(
        app=_make_candidate_status_stub(
            eprint_status=408,
            eprint_body=b"",
            html_status=404,
            pdf_bytes=_CORRUPT_PDF_LIKE,
        )
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
        with pytest.raises(FetchError) as error:
            await run_ingest(ctx, store, job)

    assert error.value.kind == "network_error"
    diagnostics = json.loads(str(error.value))["candidates"]
    assert [item["format"] for item in diagnostics] == ["latex", "arxiv_html", "pdf"]
    assert diagnostics[0]["code"] == "network_error"


async def test_all_deterministic_candidate_failures_finish_job(
    db_session: AsyncSession,
    router: LLMRouter,
    seed_ingest_job: Any,
) -> None:
    arxiv_id = _arxiv_id()
    ids = await seed_ingest_job(db_session, arxiv_id=arxiv_id)
    transport = httpx.ASGITransport(
        app=_make_candidate_status_stub(
            eprint_status=404,
            eprint_body=b"",
            html_status=404,
            pdf_bytes=_CORRUPT_PDF_LIKE,
        )
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
    assert json.loads(job.error or "{}")["code"] == "document_incomplete"
