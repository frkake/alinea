"""M2-01: arXiv 取り込みの取得優先順位 LaTeX > HTML > PDF(plans/05 §1.3・§5)。

- LaTeX ソース(e-print)が取得・解析できる場合、品質 A・`source_format='latex'`・
  `parser_version='latex-1.3.0'` で構造化され、HTML 取得(SourceAsset kind='arxiv_html')は
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
import inspect
import io
import json
import random
import re
import tarfile
import time
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
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
from alinea_worker import pipeline as worker_pipeline
from alinea_worker import source_candidates as source_candidate_module
from alinea_worker.figure_assets import html_asset_url
from alinea_worker.pipeline import IngestRun, run_ingest
from alinea_worker.source_candidates import (
    embedded_pdf_bytes,
    parse_html_candidate,
    parse_latex_candidate,
    parse_pdf_candidate,
)
from alinea_worker.tasks.ingest import ingest_paper
from botocore.exceptions import ClientError
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
_LATEX_COMPLETE_FIGURE_TEX = _LATEX_MAIN_TEX.replace(
    "The method section describes the approach in detail for testing purposes here.\n",
    "The method section describes the approach in detail for testing purposes here. "
    "It includes enough structured prose to remain a complete source candidate before its "
    "declared display assets are checked. The explanation covers inputs, transformations, "
    "evaluation, limitations, and reproducible observations without depending on a paper.\n",
)
_LATEX_MISSING_ASSET_TEX = _LATEX_COMPLETE_FIGURE_TEX.replace(
    "\\end{document}\n",
    "\\begin{figure}\n"
    "\\caption{A figure whose source is missing.}\n"
    "\\label{fig:missing}\n"
    "\\end{figure}\n"
    "\\end{document}\n",
)
_LATEX_TWO_VALID_FIGURES_TEX = _LATEX_MAIN_TEX.replace(
    "\\end{document}\n",
    "\\begin{figure}\n"
    "\\includegraphics{mock-figure}\n"
    "\\caption{A second valid synthetic figure.}\n"
    "\\label{fig:second}\n"
    "\\end{figure}\n"
    "\\end{document}\n",
)
_LATEX_MULTI_PANEL_MISSING_ASSET_TEX = _LATEX_COMPLETE_FIGURE_TEX.replace(
    "\\includegraphics{mock-figure}\n",
    "\\includegraphics{mock-figure}\n\\includegraphics {missing-panel}\n",
)
_LATEX_STANDALONE_MISSING_ASSET_TEX = _LATEX_COMPLETE_FIGURE_TEX.replace(
    "\\end{document}\n",
    "\\includegraphics{mock-figure}\n"
    "\\includegraphics [width=.5\\textwidth] {missing-standalone}\n"
    "\\end{document}\n",
)
_LATEX_TABLE_MISSING_ASSET_TEX = _LATEX_COMPLETE_FIGURE_TEX.replace(
    "\\end{document}\n",
    "\\begin{table}\n"
    "\\caption{An image-backed comparison table.}\n"
    "\\includegraphics{mock-figure}\n"
    "\\includegraphics {missing-table-panel}\n"
    "\\end{table}\n"
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
_NO_TEXT_LAYER_PDF = (_FIXTURES / "pdf_no_text_layer.pdf").read_bytes()
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
_VALID_INLINE_SVG_STYLE_ATTRIBUTE_HTML = _VALID_INLINE_SVG_HTML.replace(
    b'<rect width="20" height="10" fill="blue"></rect>',
    b'<rect width="20" height="10" style="fill:red"></rect>',
)
_VALID_EXTERNAL_FIGURE_HTML = b"""<!doctype html><html><body><article class="ltx_document">
<section class="ltx_section"><h2 class="ltx_title ltx_title_section">1 Method</h2>
<div class="ltx_para"><p class="ltx_p">This first synthetic paragraph contains enough
structured prose to establish a complete candidate before its external display asset is
validated, including method inputs, transformations, evidence, and limitations.</p></div>
<div class="ltx_para"><p class="ltx_p">This second synthetic paragraph describes generic
evaluation outcomes and reproducibility details without depending on any particular paper,
identifier, title, author, or source fragment.</p></div>
<figure id="S1.F1" class="ltx_figure"><img class="ltx_graphics" src="plot.png"
alt="external plot"/><figcaption class="ltx_caption">A synthetic plot.</figcaption></figure>
</section></article></body></html>"""
_VALID_MULTI_IMAGE_HTML = b"""<!doctype html><html><body><article class="ltx_document">
<section class="ltx_section"><h2 class="ltx_title ltx_title_section">1 Panels</h2>
<div class="ltx_para"><p class="ltx_p">This first synthetic paragraph contains enough
structured prose to make the multi-panel HTML source eligible before display assets are
validated, including method inputs, transformations, evidence, and limitations.</p></div>
<div class="ltx_para"><p class="ltx_p">This second synthetic paragraph describes generic
evaluation outcomes and reproducibility details without depending on a particular paper.</p></div>
<figure id="S1.F2" class="ltx_figure"><div class="ltx_flex_figure">
<img class="ltx_graphics" src="panel-a.png" alt="panel a"/>
<img class="ltx_graphics" src="panel-b.png" alt="panel b"/>
</div><figcaption class="ltx_caption"><span class="ltx_tag ltx_tag_figure">Figure 2:</span>
Shared panels.</figcaption></figure></section></article></body></html>"""
_VALID_INLINE_SVG_STYLE_ELEMENT_HTML = _VALID_INLINE_SVG_HTML.replace(
    b'<rect width="20" height="10" fill="blue"></rect>',
    b'<style>rect{fill:red}</style><rect width="20" height="10"></rect>',
)
_UNSAFE_INLINE_SVG_STYLE_HTML = _VALID_INLINE_SVG_HTML.replace(
    b'<rect width="20" height="10" fill="blue"></rect>',
    b"<style>@import url(https://example.org/tracker.css);</style>"
    b'<rect width="20" height="10"></rect>',
)
_VALID_STALE_HTML = _VALID_STORED_HTML.replace(b"The stored candidate", b"The stale duplicate")
_VALID_CHANGED_HTML = _VALID_STORED_HTML.replace(
    b"The stored candidate", b"The independently changed candidate"
)
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


def _make_oversized_source_stub(
    source: str,
    *,
    declared_length: int,
) -> Starlette:
    oversized = b"x" * 64

    async def eprint(_request: Request) -> Response:
        if source == "eprint":
            return Response(
                oversized,
                headers={"content-length": str(declared_length)},
                media_type="application/x-eprint-tar",
            )
        return Response("missing", status_code=404)

    async def html(_request: Request) -> Response:
        if source == "html":
            return Response(
                oversized,
                headers={"content-length": str(declared_length)},
                media_type="text/html; charset=utf-8",
            )
        return Response(_VALID_STORED_HTML, media_type="text/html; charset=utf-8")

    async def pdf(_request: Request) -> Response:
        headers = (
            {"content-length": str(declared_length)} if source == "pdf" else None
        )
        return Response(_VALID_PRIORITY_PDF, headers=headers, media_type="application/pdf")

    return Starlette(
        routes=[
            Route("/api/query", _query, methods=["GET"]),
            Route("/oai2", _oai2, methods=["GET"]),
            Route("/e-print/{arxiv_id:path}", eprint, methods=["GET"]),
            Route("/html/{path:path}", html, methods=["GET"]),
            Route("/pdf/{arxiv_id:path}", pdf, methods=["GET"]),
        ]
    )


def _make_html_figure_status_stub(status_code: int) -> Starlette:
    async def eprint(_request: Request) -> Response:
        return Response("missing", status_code=404)

    async def figure(_request: Request) -> Response:
        return Response("unavailable", status_code=status_code)

    async def html(_request: Request) -> Response:
        return Response(_VALID_EXTERNAL_FIGURE_HTML, media_type="text/html; charset=utf-8")

    async def pdf(_request: Request) -> Response:
        return Response(_incomplete_original_pdf(), media_type="application/pdf")

    return Starlette(
        routes=[
            Route("/api/query", _query, methods=["GET"]),
            Route("/oai2", _oai2, methods=["GET"]),
            Route("/e-print/{arxiv_id:path}", eprint, methods=["GET"]),
            Route("/html/{arxiv_id}/plot.png", figure, methods=["GET"]),
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
    pdf_candidate = parse_pdf_candidate(embedded_pdf, pdf_text="")
    stats: dict[str, Any] = {
        **pdf_candidate.parsed.stats,
        "marker": "existing revision must not be mutated",
        "candidate_failures": [],
        "completeness": pdf_candidate.report.as_dict(),
        "figure_asset_failures": [],
        "figure_materialization_version": worker_pipeline.FIGURE_MATERIALIZATION_VERSION,
        "selected_source": {
            "storage_key": latex_key,
            "sha256": hashlib.sha256(archive).hexdigest(),
        },
        "parsed_content_sha256": worker_pipeline._canonical_content_sha256(
            pdf_candidate.content.model_dump()
        ),
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
    revision = DocumentRevision(
        paper_id=ids["paper_id"],
        source_version="v1",
        parser_version=PDF_PARSER_VERSION,
        quality_level="B",
        source_format="pdf",
        content=pdf_candidate.content.model_dump(),
        stats=stats,
    )
    db.add(revision)
    await db.flush()
    revision_id = str(revision.id)
    parsed_pdf = pdf_candidate.parsed
    blocks_by_id = {block.id: block for block in parsed_pdf.blocks}
    manifest: list[dict[str, Any]] = []
    for block_id, png in sorted(parsed_pdf.figure_images.items()):
        block = blocks_by_id[block_id]
        key = StorageKeys.figure(ids["paper_id"], revision_id, block_id, "png")
        block.asset_key = key
        await storage.put(storage.assets_bucket, key, png, content_type="image/png")
        manifest.append(
            {
                "block_id": block_id,
                "key": key,
                "sha256": hashlib.sha256(png).hexdigest(),
                "byte_size": len(png),
            }
        )
    content = parsed_pdf.to_document_content().model_dump()
    stats = {
        **stats,
        "figure_asset_manifest": manifest,
        "revision_content_sha256": worker_pipeline._canonical_content_sha256(content),
    }
    revision.content = content
    revision.stats = stats
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


def test_parse_pdf_candidate_has_no_synchronous_ocr_isolation_bypass() -> None:
    parameters = inspect.signature(parse_pdf_candidate).parameters

    assert "use_ocr" not in parameters
    assert "ocr_language" not in parameters


async def test_pdf_text_evidence_does_not_call_mupdf_in_parent_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def parent_text_forbidden(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("PDF text evidence must execute in the isolated child")

    monkeypatch.setattr(fitz.Page, "get_text", parent_text_forbidden)
    run = object.__new__(IngestRun)
    run._pdf_bytes = _VALID_MULTI_PARAGRAPH_PDF
    run._pdf_text = None

    evidence = await run._ensure_pdf_text_evidence(run._pdf_bytes)

    assert len(evidence) > 100
    assert set(evidence) == {"x"}


async def test_pdf_text_evidence_is_initialized_off_the_event_loop_and_cached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[bytes] = []

    async def count(data: bytes) -> Any:
        calls.append(data)
        return SimpleNamespace(extracted_chars=3, pages=1)

    monkeypatch.setattr(worker_pipeline, "count_pdf_text_evidence_isolated", count)
    run = object.__new__(IngestRun)
    run._pdf_bytes = b"pdf"
    run._pdf_text = None

    evidence = await run._ensure_pdf_text_evidence(run._pdf_bytes)
    cached = await run._ensure_pdf_text_evidence(run._pdf_bytes)

    assert evidence == cached == "xxx"
    assert run._pdf_text == "xxx"
    assert calls == [b"pdf"]


def test_pdf_text_for_completeness_is_cache_only() -> None:
    run = object.__new__(IngestRun)
    run._pdf_bytes = _VALID_MULTI_PARAGRAPH_PDF
    run._pdf_text = None

    with pytest.raises(AssertionError):
        run._pdf_text_for_completeness()


async def test_pdf_text_evidence_defers_content_error_to_pdf_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail(_data: bytes) -> Any:
        raise worker_pipeline.CandidateUnavailable(
            "pdf", "pdf_open_error", "synthetic corrupt PDF"
        )

    monkeypatch.setattr(worker_pipeline, "count_pdf_text_evidence_isolated", fail)
    run = object.__new__(IngestRun)
    run._pdf_bytes = b"%PDF- corrupt"
    run._pdf_text = None

    assert await run._ensure_pdf_text_evidence(run._pdf_bytes) == ""
    assert run._pdf_text == ""


@pytest.mark.parametrize(
    "code",
    [
        "pdf_crashed",
        "pdf_lifecycle",
        "pdf_output_too_large",
        "pdf_platform_unsupported",
        "pdf_timeout",
    ],
)
async def test_pdf_text_evidence_defers_operational_failure_to_pdf_candidate(
    monkeypatch: pytest.MonkeyPatch,
    code: str,
) -> None:
    async def fail(_data: bytes) -> Any:
        raise worker_pipeline.CandidateUnavailable(
            "pdf", code, "synthetic child failure"
        )

    monkeypatch.setattr(worker_pipeline, "count_pdf_text_evidence_isolated", fail)
    run = object.__new__(IngestRun)
    run._pdf_bytes = b"%PDF- valid"
    run._pdf_text = None

    assert await run._ensure_pdf_text_evidence(run._pdf_bytes) == ""
    assert run._pdf_text == ""


def test_pdf_ocr_candidate_uses_bounded_ocr_evidence_instead_of_hidden_pdf_text() -> None:
    parsed = source_candidate_module.parse_pdf(_VALID_MULTI_PARAGRAPH_PDF)
    parsed.stats["ocr"] = True

    candidate = source_candidate_module._pdf_candidate_from_parsed(
        _VALID_MULTI_PARAGRAPH_PDF,
        pdf_text="x" * 10_000,
        parsed=parsed,
        ocr_language="eng",
    )

    assert candidate.report.accepted
    assert candidate.report.source_chars == parsed.stats["extracted_chars"]
    assert candidate.diagnostics == [
        {
            "kind": "pdf_ocr",
            "version": source_candidate_module.PDF_OCR_CANDIDATE_VERSION,
            "language": "eng",
        }
    ]


@pytest.mark.parametrize("stats_update", [{"ocr": False}, {"extracted_chars": None}])
def test_pdf_ocr_candidate_rejects_inconsistent_parser_stats(
    stats_update: dict[str, Any],
) -> None:
    parsed = source_candidate_module.parse_pdf(_VALID_MULTI_PARAGRAPH_PDF)
    parsed.stats["ocr"] = True
    parsed.stats.update(stats_update)

    with pytest.raises(worker_pipeline.CandidateUnavailable) as exc_info:
        source_candidate_module._pdf_candidate_from_parsed(
            _VALID_MULTI_PARAGRAPH_PDF,
            pdf_text="",
            parsed=parsed,
            ocr_language="eng",
        )

    assert exc_info.value.code == "ocr_crashed"


@pytest.mark.parametrize(
    ("attribute", "value", "stats_update"),
    [
        ("quality_level", "A", {}),
        ("source_format", "latex", {}),
        ("parser_version", "pdf-0.0.0", {}),
        (None, None, {"pages": "2"}),
        (None, None, {"extracted_chars": None}),
    ],
)
def test_normal_pdf_candidate_rejects_inconsistent_parser_identity(
    attribute: str | None,
    value: Any,
    stats_update: dict[str, Any],
) -> None:
    parsed = source_candidate_module.parse_pdf(_VALID_MULTI_PARAGRAPH_PDF)
    if attribute is not None:
        setattr(parsed, attribute, value)
    parsed.stats.update(stats_update)

    with pytest.raises(worker_pipeline.CandidateUnavailable) as exc_info:
        source_candidate_module._pdf_candidate_from_parsed(
            _VALID_MULTI_PARAGRAPH_PDF,
            pdf_text="",
            parsed=parsed,
            ocr_language=None,
        )

    assert exc_info.value.code == "parse_error"


@pytest.mark.parametrize(
    ("ocr_code", "expected"),
    [
        ("ocr_engine_unavailable", "ocr_engine_unavailable"),
        ("ocr_language_unavailable", "ocr_language_unavailable"),
        ("ocr_language_invalid", "ocr_language_invalid"),
        ("ocr_output_too_large", "ocr_output_too_large"),
        ("ocr_platform_unsupported", "ocr_platform_unsupported"),
        ("pdf_page_limit", "pdf_page_limit"),
        ("pdf_text_limit", "pdf_text_limit"),
        ("pdf_layout_limit", "pdf_layout_limit"),
        ("pdf_figure_bytes_limit", "pdf_figure_bytes_limit"),
        ("pdf_platform_unsupported", "pdf_platform_unsupported"),
        ("ocr_failed", "ocr_failed"),
        ("ocr_timeout", "ocr_timeout"),
    ],
)
def test_ocr_candidate_failure_classification_is_stable(
    ocr_code: str,
    expected: str,
) -> None:
    failures = [
        {"format": "pdf", "candidate": "pdf_text", "code": "no_text_layer"},
        {"format": "pdf_ocr", "candidate": "pdf_ocr", "code": ocr_code},
    ]

    assert worker_pipeline._candidate_failure_code(failures) == expected


async def test_pdf_candidate_sequence_does_not_ocr_after_accepted_text_pdf() -> None:
    class Runner:
        async def _parse_pdf_ocr_bytes(self, *_args: Any, **_kwargs: Any) -> Any:
            raise AssertionError("accepted text PDF must not invoke OCR")

    candidate, failures = await IngestRun._parse_pdf_candidate_sequence(
        Runner(),  # type: ignore[arg-type]
        _VALID_MULTI_PARAGRAPH_PDF,
        pdf_text="",
    )

    assert candidate is not None and candidate.report.accepted
    assert candidate.parsed.stats["ocr"] is False
    assert failures == []


async def test_pdf_candidate_sequence_does_not_ocr_after_unrelated_parse_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Runner:
        async def _parse_pdf_ocr_bytes(self, *_args: Any, **_kwargs: Any) -> Any:
            raise AssertionError("unrelated parse failures must not invoke OCR")

    async def unrelated_failure(*_args: Any, **_kwargs: Any) -> Any:
        raise worker_pipeline.CandidateUnavailable("pdf", "parse_error", "synthetic corruption")

    monkeypatch.setattr(worker_pipeline, "parse_pdf_candidate_async", unrelated_failure)

    with pytest.raises(worker_pipeline.CandidateUnavailable) as exc_info:
        await IngestRun._parse_pdf_candidate_sequence(
            Runner(),  # type: ignore[arg-type]
            _CORRUPT_PDF_LIKE,
            pdf_text="",
        )

    assert exc_info.value.code == "parse_error"


async def test_pdf_candidate_sequence_uses_ocr_before_broken_empty_text_layout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    accepted_ocr_candidate = parse_pdf_candidate(
        _VALID_MULTI_PARAGRAPH_PDF,
        pdf_text="",
    )
    original_get_text = fitz.Page.get_text
    dict_calls: list[int] = []
    ocr_calls: list[bytes] = []

    def empty_text_broken_dict(page: fitz.Page, *args: Any, **kwargs: Any) -> Any:
        mode = args[0] if args else "text"
        if mode == "dict":
            dict_calls.append(page.number)
            raise RuntimeError("layout extraction must not run without a text layer")
        if mode == "text":
            return ""
        return original_get_text(page, *args, **kwargs)

    class Runner:
        async def _parse_pdf_ocr_bytes(
            self,
            data: bytes,
            *,
            pdf_text: str,
            ocr_language: str = "eng",
        ) -> Any:
            del pdf_text, ocr_language
            ocr_calls.append(data)
            return accepted_ocr_candidate

    monkeypatch.setattr(fitz.Page, "get_text", empty_text_broken_dict)

    candidate, failures = await IngestRun._parse_pdf_candidate_sequence(
        Runner(),  # type: ignore[arg-type]
        _NO_TEXT_LAYER_PDF,
        pdf_text="",
    )

    assert candidate is accepted_ocr_candidate
    assert ocr_calls == [_NO_TEXT_LAYER_PDF]
    assert failures[0]["code"] == "no_text_layer"
    assert dict_calls == []


async def test_pdf_candidate_sequence_uses_ocr_after_insufficient_visible_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_parse_candidate = source_candidate_module.parse_pdf_candidate
    ocr_calls: list[bytes] = []

    class Runner:
        async def _parse_pdf_ocr_bytes(
            self,
            data: bytes,
            *,
            pdf_text: str,
            ocr_language: str = "eng",
        ) -> Any:
            del ocr_language
            ocr_calls.append(data)
            return original_parse_candidate(data, pdf_text=pdf_text)

    async def incomplete_text(data: bytes, *, pdf_text: str, **_kwargs: Any) -> Any:
        candidate = original_parse_candidate(data, pdf_text=pdf_text)
        candidate.report = _completeness_report("document_incomplete")
        return candidate

    monkeypatch.setattr(worker_pipeline, "parse_pdf_candidate_async", incomplete_text)

    candidate, failures = await IngestRun._parse_pdf_candidate_sequence(
        Runner(),  # type: ignore[arg-type]
        _VALID_MULTI_PARAGRAPH_PDF,
        pdf_text="",
    )

    assert candidate is not None and candidate.report.accepted
    assert ocr_calls == [_VALID_MULTI_PARAGRAPH_PDF]
    assert failures[0]["candidate"] == "pdf_text"
    assert failures[0]["code"] == "document_incomplete"


async def test_pdf_candidate_sequence_releases_incomplete_text_candidate_before_ocr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import gc
    import weakref

    class LargeImageSentinel:
        pass

    original_parse_candidate = source_candidate_module.parse_pdf_candidate
    accepted_ocr_candidate = original_parse_candidate(
        _VALID_MULTI_PARAGRAPH_PDF,
        pdf_text="",
    )
    candidate_ref: weakref.ReferenceType[Any] | None = None
    parsed_ref: weakref.ReferenceType[Any] | None = None
    image_ref: weakref.ReferenceType[Any] | None = None

    async def incomplete_text(data: bytes, *, pdf_text: str) -> Any:
        nonlocal candidate_ref, parsed_ref, image_ref
        candidate = original_parse_candidate(data, pdf_text=pdf_text)
        candidate.report = _completeness_report("document_incomplete")
        image = LargeImageSentinel()
        candidate.parsed.figure_images["large-sentinel"] = image  # type: ignore[assignment]
        candidate_ref = weakref.ref(candidate)
        parsed_ref = weakref.ref(candidate.parsed)
        image_ref = weakref.ref(image)
        return candidate

    class Runner:
        async def _parse_pdf_ocr_bytes(self, *_args: Any, **_kwargs: Any) -> Any:
            gc.collect()
            assert candidate_ref is not None and candidate_ref() is None
            assert parsed_ref is not None and parsed_ref() is None
            assert image_ref is not None and image_ref() is None
            return accepted_ocr_candidate

    monkeypatch.setattr(worker_pipeline, "parse_pdf_candidate_async", incomplete_text)

    candidate, failures = await IngestRun._parse_pdf_candidate_sequence(
        Runner(),  # type: ignore[arg-type]
        _VALID_MULTI_PARAGRAPH_PDF,
        pdf_text="",
    )

    assert candidate is accepted_ocr_candidate
    assert failures[0]["code"] == "document_incomplete"


def test_pdf_ocr_checkpoint_identity_rejects_missing_or_mismatched_provenance() -> None:
    identity = {
        "kind": "pdf_ocr",
        "version": source_candidate_module.PDF_OCR_CANDIDATE_VERSION,
        "language": "eng",
    }
    diagnostics = [dict(identity)]

    assert IngestRun._checkpoint_candidate_identity(
        {"candidate_identity": dict(identity)},
        diagnostics,
        source_format="pdf",
    ) == identity
    with pytest.raises(FetchError, match="OCR identity is missing"):
        IngestRun._checkpoint_candidate_identity({}, diagnostics, source_format="pdf")
    with pytest.raises(FetchError, match="OCR identity is invalid"):
        IngestRun._checkpoint_candidate_identity(
            {"candidate_identity": {**identity, "language": "deu"}},
            diagnostics,
            source_format="pdf",
        )

    assert IngestRun._checkpoint_candidate_identity(
        {},
        [],
        source_format="pdf",
    ) is None


@pytest.mark.parametrize(
    ("diagnostics", "stats"),
    [
        pytest.param(
            [
                {
                    "kind": "pdf_ocr",
                    "version": source_candidate_module.PDF_OCR_CANDIDATE_VERSION,
                    "language": "eng",
                }
            ],
            {"ocr": False, "extracted_chars": 100},
            id="identity-without-ocr",
        ),
        pytest.param([], {"ocr": True, "extracted_chars": 100}, id="ocr-without-identity"),
        pytest.param(
            [
                {
                    "kind": "pdf_ocr",
                    "version": source_candidate_module.PDF_OCR_CANDIDATE_VERSION,
                    "language": "eng",
                }
            ],
            {"ocr": True, "extracted_chars": None},
            id="missing-evidence",
        ),
    ],
)
def test_pdf_ocr_identity_requires_consistent_stats(
    diagnostics: list[dict[str, Any]],
    stats: dict[str, Any],
) -> None:
    with pytest.raises(FetchError, match="OCR.*stats"):
        worker_pipeline._validate_pdf_ocr_stats_identity(diagnostics, stats)


def test_pdf_ocr_identity_accepts_matching_stats() -> None:
    diagnostics = [
        {
            "kind": "pdf_ocr",
            "version": source_candidate_module.PDF_OCR_CANDIDATE_VERSION,
            "language": "eng",
        }
    ]

    identity = worker_pipeline._validate_pdf_ocr_stats_identity(
        diagnostics,
        {"ocr": True, "extracted_chars": 100},
    )

    assert identity == diagnostics[0]


def test_pdf_ocr_revision_provenance_rejects_tampered_parser_stats() -> None:
    identity = {
        "kind": "pdf_ocr",
        "version": source_candidate_module.PDF_OCR_CANDIDATE_VERSION,
        "language": "eng",
    }
    run = object.__new__(IngestRun)
    run._candidate_provenance_validation_required = True
    run._candidate_identity = identity
    run._candidate_diagnostics = []
    revision = SimpleNamespace(
        source_format="pdf",
        stats={
            "candidate_identity": dict(identity),
            "ocr": False,
            "extracted_chars": 100,
        },
    )

    with pytest.raises(FetchError, match="OCR.*stats"):
        run._validate_revision_candidate_provenance(revision)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("revision_update", "stats_update"),
    [
        ({"source_format": "latex"}, {}),
        ({"quality_level": "A"}, {}),
        ({"parser_version": "pdf-0.0.0"}, {}),
        ({}, {"ocr": True}),
        ({}, {"pages": 999}),
        ({}, {"extracted_chars": 1}),
    ],
)
def test_pdf_revision_provenance_requires_exact_fresh_parser_identity(
    revision_update: dict[str, Any],
    stats_update: dict[str, Any],
) -> None:
    parsed = source_candidate_module.parse_pdf(_VALID_MULTI_PARAGRAPH_PDF)
    run = object.__new__(IngestRun)
    run._candidate_provenance_validation_required = True
    run._candidate_identity = None
    run._candidate_diagnostics = []
    run.parsed_pdf = parsed
    revision_values: dict[str, Any] = {
        "source_format": "pdf",
        "quality_level": "B",
        "parser_version": PDF_PARSER_VERSION,
        "stats": {
            "ocr": False,
            "pages": parsed.stats["pages"],
            "extracted_chars": parsed.stats["extracted_chars"],
        },
    }
    revision_values.update(revision_update)
    revision_values["stats"] = {
        **revision_values["stats"],
        **stats_update,
    }

    with pytest.raises(FetchError, match="parser provenance"):
        run._validate_revision_candidate_provenance(  # type: ignore[arg-type]
            SimpleNamespace(**revision_values)
        )


def test_pdf_revision_provenance_accepts_exact_fresh_parser_identity() -> None:
    parsed = source_candidate_module.parse_pdf(_VALID_MULTI_PARAGRAPH_PDF)
    run = object.__new__(IngestRun)
    run._candidate_provenance_validation_required = True
    run._candidate_identity = None
    run._candidate_diagnostics = []
    run.parsed_pdf = parsed

    run._validate_revision_candidate_provenance(  # type: ignore[arg-type]
        SimpleNamespace(
            source_format="pdf",
            quality_level="B",
            parser_version=PDF_PARSER_VERSION,
            stats={
                "ocr": False,
                "pages": parsed.stats["pages"],
                "extracted_chars": parsed.stats["extracted_chars"],
            },
        )
    )


def _install_no_text_then_ocr_test_doubles(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[list[bytes], list[bytes]]:
    original_parse_candidate = source_candidate_module.parse_pdf_candidate
    text_candidate_bytes: list[bytes] = []
    ocr_candidate_bytes: list[bytes] = []

    async def no_text_candidate(data: bytes, *, pdf_text: str, **_kwargs: Any) -> Any:
        del pdf_text
        text_candidate_bytes.append(data)
        raise worker_pipeline.CandidateUnavailable(
            "pdf", "no_text_layer", "synthetic PDF has no text layer"
        )

    async def ocr_candidate(
        data: bytes,
        *,
        pdf_text: str,
        ocr_language: str = "eng",
        **_kwargs: Any,
    ) -> Any:
        ocr_candidate_bytes.append(data)
        candidate = original_parse_candidate(data, pdf_text=pdf_text)
        candidate.parsed.stats["ocr"] = True
        candidate.diagnostics = [
            {
                "kind": "pdf_ocr",
                "version": source_candidate_module.PDF_OCR_CANDIDATE_VERSION,
                "language": ocr_language,
            }
        ]
        return candidate

    monkeypatch.setattr(worker_pipeline, "parse_pdf_candidate_async", no_text_candidate)
    monkeypatch.setattr(worker_pipeline, "parse_pdf_ocr_candidate", ocr_candidate)
    return text_candidate_bytes, ocr_candidate_bytes


async def test_pdf_ocr_is_final_candidate_after_no_text_and_reuses_pdf_bytes_once(
    db_session: AsyncSession,
    router: LLMRouter,
    seed_ingest_job: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_materialize = IngestRun._materialize_candidate_figures
    text_candidate_bytes, ocr_candidate_bytes = _install_no_text_then_ocr_test_doubles(
        monkeypatch
    )
    materialized_candidates: list[str] = []

    async def track_materialization(
        self: IngestRun,
        candidate: Any,
        **kwargs: Any,
    ) -> None:
        marker = next(
            (
                item["kind"]
                for item in candidate.diagnostics
                if item.get("kind") == "pdf_ocr"
            ),
            "pdf_text",
        )
        materialized_candidates.append(marker)
        await original_materialize(self, candidate, **kwargs)

    monkeypatch.setattr(IngestRun, "_materialize_candidate_figures", track_materialization)
    ids = await seed_ingest_job(db_session, arxiv_id=_arxiv_id())
    transport = httpx.ASGITransport(
        app=_make_candidate_status_stub(
            eprint_status=404,
            eprint_body=b"",
            html_status=404,
            pdf_bytes=_VALID_MULTI_PARAGRAPH_PDF,
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

    assert len(text_candidate_bytes) == len(ocr_candidate_bytes) == 1
    assert text_candidate_bytes[0] is ocr_candidate_bytes[0]
    assert materialized_candidates == ["pdf_ocr"]
    revision = (
        (
            await db_session.execute(
                select(DocumentRevision).where(DocumentRevision.paper_id == ids["paper_id"])
            )
        )
        .scalars()
        .one()
    )
    identity = {
        "kind": "pdf_ocr",
        "version": source_candidate_module.PDF_OCR_CANDIDATE_VERSION,
        "language": "eng",
    }
    assert revision.stats["ocr"] is True
    assert revision.stats["candidate_identity"] == identity
    completed = await store.get(ids["job_id"])
    assert completed is not None
    parsing_checkpoint = JobStore.get_checkpoint(completed)["parsing"]
    assert parsing_checkpoint["candidate_identity"] == identity
    assert parsing_checkpoint["candidate_failures"][-1]["candidate"] == "pdf_text"
    assert parsing_checkpoint["candidate_failures"][-1]["code"] == "no_text_layer"

    completed.status = "queued"
    completed.stage = "structuring"
    completed.error = None
    completed.finished_at = None
    await db_session.commit()
    resumed = await store.claim(ids["job_id"])
    assert resumed is not None
    await run_ingest(ctx, store, resumed)

    assert len(text_candidate_bytes) == 1
    assert len(ocr_candidate_bytes) == 2
    assert materialized_candidates == ["pdf_ocr", "pdf_ocr"]
    revisions = (
        await db_session.execute(
            select(DocumentRevision).where(DocumentRevision.paper_id == ids["paper_id"])
        )
    ).scalars()
    assert [str(item.id) for item in revisions.all()] == [str(revision.id)]

    stored = await db_session.get(DocumentRevision, revision.id)
    assert stored is not None
    stored_content = json.loads(json.dumps(stored.content))
    stored.stats = {
        **stored.stats,
        "candidate_identity": {**identity, "language": "deu"},
    }
    await db_session.commit()
    completed = await store.get(ids["job_id"])
    assert completed is not None
    completed.status = "queued"
    completed.stage = "structuring"
    completed.error = None
    completed.finished_at = None
    await db_session.commit()
    tampered_resume = await store.claim(ids["job_id"])
    assert tampered_resume is not None
    with pytest.raises(FetchError) as tampered_error:
        await run_ingest(ctx, store, tampered_resume)

    assert tampered_error.value.kind == "parse_error"
    unchanged = await db_session.get(DocumentRevision, revision.id)
    assert unchanged is not None
    assert unchanged.content == stored_content
    assert unchanged.stats["candidate_identity"]["language"] == "deu"


async def test_scanned_embedded_pdf_uses_ocr_before_falling_back_to_original_pdf(
    db_session: AsyncSession,
    router: LLMRouter,
    seed_ingest_job: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    embedded_pdf = _VALID_MULTI_PARAGRAPH_PDF
    archive = _build_embedded_pdf_archive(embedded_pdf)
    original_pdf = _incomplete_original_pdf()
    original_parse_candidate = source_candidate_module.parse_pdf_candidate
    text_calls: list[bytes] = []
    ocr_calls: list[bytes] = []

    async def parse_text(data: bytes, *, pdf_text: str, **_kwargs: Any) -> Any:
        text_calls.append(data)
        if data == embedded_pdf:
            raise worker_pipeline.CandidateUnavailable(
                "pdf", "no_text_layer", "synthetic embedded PDF has no text layer"
            )
        return original_parse_candidate(data, pdf_text=pdf_text)

    async def parse_ocr(
        data: bytes,
        *,
        pdf_text: str,
        ocr_language: str = "eng",
        **_kwargs: Any,
    ) -> Any:
        assert data == embedded_pdf
        ocr_calls.append(data)
        candidate = original_parse_candidate(data, pdf_text=pdf_text)
        candidate.parsed.stats["ocr"] = True
        candidate.diagnostics = [
            {
                "kind": "pdf_ocr",
                "version": source_candidate_module.PDF_OCR_CANDIDATE_VERSION,
                "language": ocr_language,
            }
        ]
        return candidate

    monkeypatch.setattr(worker_pipeline, "parse_pdf_candidate_async", parse_text)
    monkeypatch.setattr(worker_pipeline, "parse_pdf_ocr_candidate", parse_ocr)
    ids = await seed_ingest_job(db_session, arxiv_id=_arxiv_id())
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

    assert calls == {"pdf": 1, "eprint": 1, "html": 0}
    assert text_calls == [embedded_pdf]
    assert ocr_calls == [embedded_pdf]
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
    assert revision.stats["ocr"] is True
    assert revision.stats["embedded_pdf_source"] == "body.pdf"
    assert revision.stats["candidate_identity"] == {
        "kind": "pdf_ocr",
        "version": source_candidate_module.PDF_OCR_CANDIDATE_VERSION,
        "language": "eng",
    }
    completed = await store.get(ids["job_id"])
    assert completed is not None
    diagnostics = JobStore.get_checkpoint(completed)["parsing"]["candidate_diagnostics"]
    assert {item["kind"] for item in diagnostics} == {"embedded_pdf", "pdf_ocr"}

    completed.status = "queued"
    completed.stage = "structuring"
    completed.error = None
    completed.finished_at = None
    await db_session.commit()
    resumed = await store.claim(ids["job_id"])
    assert resumed is not None
    await run_ingest(ctx, store, resumed)

    assert calls == {"pdf": 1, "eprint": 1, "html": 0}
    assert text_calls == [embedded_pdf]
    assert ocr_calls == [embedded_pdf, embedded_pdf]


async def test_pdf_figure_materialization_failure_does_not_trigger_ocr(
    db_session: AsyncSession,
    router: LLMRouter,
    seed_ingest_job: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ocr_calls = 0

    async def unexpected_ocr(*_args: Any, **_kwargs: Any) -> Any:
        nonlocal ocr_calls
        ocr_calls += 1
        raise AssertionError("figure failures must not trigger OCR")

    async def fail_pdf_figure(
        _self: IngestRun,
        candidate: Any,
        **_kwargs: Any,
    ) -> None:
        candidate.figure_asset_failures = [
            {"code": "image_invalid", "figure_id": "synthetic", "source": "pdf"}
        ]
        report = candidate.report
        candidate.report = DocumentCompleteness(
            accepted=False,
            code="figure_asset_unresolved",
            source_chars=report.source_chars,
            structured_chars=report.structured_chars,
            paragraph_count=report.paragraph_count,
            figure_count=report.figure_count,
            unresolved_figures=1,
        )

    monkeypatch.setattr(worker_pipeline, "parse_pdf_ocr_candidate", unexpected_ocr)
    monkeypatch.setattr(IngestRun, "_materialize_candidate_figures", fail_pdf_figure)
    ids = await seed_ingest_job(db_session, arxiv_id=_arxiv_id())
    transport = httpx.ASGITransport(
        app=_make_candidate_status_stub(
            eprint_status=404,
            eprint_body=b"",
            html_status=404,
            pdf_bytes=_VALID_MULTI_PARAGRAPH_PDF,
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
        with pytest.raises(FetchError) as exc_info:
            await run_ingest(ctx, store, job)

    assert exc_info.value.kind == "figure_asset_unresolved"
    assert ocr_calls == 0
    failure = json.loads(str(exc_info.value))["candidates"][-1]
    assert failure["candidate"] == "pdf_text"
    assert failure["code"] == "figure_asset_unresolved"


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
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    materialized_sources: list[str] = []
    original_materialize = worker_pipeline._materialize_figure_payload

    async def counting_materialize(
        data: bytes,
        source_name: str,
        content_type: str | None = None,
        **kwargs: Any,
    ) -> Any:
        materialized_sources.append(source_name)
        return await original_materialize(data, source_name, content_type, **kwargs)

    monkeypatch.setattr(worker_pipeline, "_materialize_figure_payload", counting_materialize)
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
    assert rev.parser_version == "latex-1.3.0"
    assert rev.quality_level == "A"
    assert rev.stats["candidate_failures"] == []
    assert rev.stats["completeness"]["accepted"] is True
    assert rev.stats["figure_asset_failures"] == []
    parsed_candidate, _binary_files, _main_tex_name = parse_latex_candidate(
        stored_latex, pdf_text=""
    )
    assert rev.stats["parsed_content_sha256"] == worker_pipeline._canonical_content_sha256(
        parsed_candidate.content.model_dump()
    )
    assert rev.stats["latex_source"]["main_tex"] == "paper/main.tex"
    assert rev.stats["latex_source"]["graphicspaths"] == ["../images/"]
    content = DocumentContent.model_validate(rev.content)
    fig = next(block for _sec, block in content.iter_blocks() if block.type == "figure")
    assert fig.asset_key is not None
    assert fig.asset_key.startswith(f"figures/{ids['paper_id']}/{rev.id}/{fig.id}.")
    assert fig.asset_key.endswith(".png")
    assert materialized_sources == ["images/mock-figure.pdf"]
    stored_figure = await storage.get(storage.assets_bucket, fig.asset_key)
    assert rev.stats["figure_materialization_version"] == (
        worker_pipeline.FIGURE_MATERIALIZATION_VERSION
    )
    assert rev.stats["figure_asset_manifest"] == [
        {
            "block_id": fig.id,
            "key": fig.asset_key,
            "sha256": hashlib.sha256(stored_figure).hexdigest(),
            "byte_size": len(stored_figure),
        }
    ]

    kinds = await _source_asset_kinds(db_session, ids["paper_id"])
    assert "arxiv_latex" in kinds
    assert "arxiv_html" not in kinds  # 優先順位: LaTeX 成功時は HTML を取得しない
    assert "pdf" in kinds

    timeline = build_timeline(job.log)
    assert timeline
    assert "LaTeX ソース取得" in timeline[0]["label"]


@pytest.mark.parametrize("selected_format", ["latex", "pdf"])
async def test_large_candidate_buffers_are_released_before_translation_and_job_completes(
    selected_format: str,
    db_session: AsyncSession,
    router: LLMRouter,
    seed_ingest_job: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ids = await seed_ingest_job(db_session, arxiv_id=_arxiv_id())
    app = (
        _make_latex_arxiv_stub()
        if selected_format == "latex"
        else _make_candidate_status_stub(
            eprint_status=404,
            eprint_body=b"",
            html_status=404,
            pdf_bytes=_VALID_MULTI_PARAGRAPH_PDF,
        )
    )
    original_translate_abstract = IngestRun._stage_translating_abstract
    release_checks: list[str] = []

    async def assert_released_then_translate(run: IngestRun) -> None:
        assert run.source_format == selected_format
        assert run.content is not None
        assert run.parsed is None
        assert run.parsed_pdf is None
        assert run._candidate_materialized_figures == {}
        assert run.latex_binary_files == {}
        assert run._latex_archive_bytes is None
        assert run._pdf_bytes is None
        assert run._pdf_text is None
        release_checks.append(run.source_format)
        await original_translate_abstract(run)

    async def skip_latex_pdf_build(_run: IngestRun) -> None:
        return None

    monkeypatch.setattr(IngestRun, "_stage_translating_abstract", assert_released_then_translate)
    monkeypatch.setattr(IngestRun, "_build_latex_translation_pdf", skip_latex_pdf_build)

    transport = httpx.ASGITransport(app=app)
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

    completed = await store.get(ids["job_id"])
    assert completed is not None
    assert completed.status == "succeeded", completed.error
    assert release_checks == [selected_format]


async def test_reingest_repairs_missing_manifest_asset_before_reusing_revision(
    db_session: AsyncSession,
    latex_worker_ctx: dict[str, Any],
    seed_ingest_job: Any,
) -> None:
    arxiv_id = _arxiv_id()
    ids = await seed_ingest_job(db_session, arxiv_id=arxiv_id)
    store = JobStore(db_session)
    first_job = await store.claim(ids["job_id"])
    assert first_job is not None
    await ingest_paper(latex_worker_ctx, store, first_job)

    revision = (
        (
            await db_session.execute(
                select(DocumentRevision).where(DocumentRevision.paper_id == ids["paper_id"])
            )
        )
        .scalars()
        .one()
    )
    manifest = revision.stats["figure_asset_manifest"]
    assert len(manifest) == 1
    missing_key = manifest[0]["key"]
    storage = S3Storage()
    await storage.delete_many(storage.assets_bucket, [missing_key])

    reingest_job_id = await store.enqueue(
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
    reingest_job = await store.claim(reingest_job_id)
    assert reingest_job is not None
    await ingest_paper(latex_worker_ctx, store, reingest_job)

    completed = await store.get(reingest_job_id)
    assert completed is not None
    assert completed.status == "succeeded", completed.error
    repaired = await storage.get(storage.assets_bucket, missing_key)
    assert len(repaired) == manifest[0]["byte_size"]
    assert hashlib.sha256(repaired).hexdigest() == manifest[0]["sha256"]
    revisions = (
        await db_session.execute(
            select(DocumentRevision).where(DocumentRevision.paper_id == ids["paper_id"])
        )
    ).scalars()
    assert [str(item.id) for item in revisions.all()] == [str(revision.id)]

    await storage.delete_many(storage.assets_bucket, [missing_key])
    completed = await store.get(reingest_job_id)
    assert completed is not None
    payload = json.loads(json.dumps(completed.payload))
    payload["_checkpoint"].pop("structuring", None)
    completed.payload = payload
    completed.status = "queued"
    completed.stage = "parsing"
    completed.error = None
    completed.finished_at = None
    await db_session.commit()
    resumed = await store.claim(reingest_job_id)
    assert resumed is not None
    await ingest_paper(latex_worker_ctx, store, resumed)

    repaired_after_parsing_resume = await storage.get(storage.assets_bucket, missing_key)
    assert hashlib.sha256(repaired_after_parsing_resume).hexdigest() == manifest[0]["sha256"]

    await storage.delete_many(storage.assets_bucket, [missing_key])
    completed = await store.get(reingest_job_id)
    assert completed is not None
    completed.status = "queued"
    completed.stage = "structuring"
    completed.error = None
    completed.finished_at = None
    await db_session.commit()
    structuring_resume = await store.claim(reingest_job_id)
    assert structuring_resume is not None
    await ingest_paper(latex_worker_ctx, store, structuring_resume)

    repaired_after_structuring_resume = await storage.get(storage.assets_bucket, missing_key)
    assert hashlib.sha256(repaired_after_structuring_resume).hexdigest() == manifest[0]["sha256"]


async def test_legacy_revision_without_figure_identity_rolls_to_new_parser_revision(
    db_session: AsyncSession,
    latex_worker_ctx: dict[str, Any],
    seed_ingest_job: Any,
) -> None:
    ids = await seed_ingest_job(db_session, arxiv_id=_arxiv_id())
    legacy = DocumentRevision(
        paper_id=ids["paper_id"],
        source_version="v1",
        parser_version="latex-1.2.0",
        quality_level="A",
        source_format="latex",
        content=DocumentContent(quality_level="A", sections=[]).model_dump(),
        stats={"legacy": True},
    )
    db_session.add(legacy)
    await db_session.commit()
    store = JobStore(db_session)
    job = await store.claim(ids["job_id"])
    assert job is not None
    await ingest_paper(latex_worker_ctx, store, job)

    completed = await store.get(ids["job_id"])
    assert completed is not None
    assert completed.status == "succeeded", completed.error
    revisions = (
        await db_session.execute(
            select(DocumentRevision)
            .where(DocumentRevision.paper_id == ids["paper_id"])
            .order_by(DocumentRevision.created_at, DocumentRevision.id)
        )
    ).scalars()
    revision_list = revisions.all()
    assert {item.parser_version for item in revision_list} == {
        "latex-1.2.0",
        "latex-1.3.0",
    }
    current = next(item for item in revision_list if item.parser_version == "latex-1.3.0")
    assert current.stats["figure_materialization_version"] == (
        worker_pipeline.FIGURE_MATERIALIZATION_VERSION
    )
    paper = await db_session.get(Paper, ids["paper_id"])
    assert paper is not None
    assert str(paper.latest_revision_id) == str(current.id)


async def test_changed_fresh_source_rejects_existing_revision_without_overwrite(
    db_session: AsyncSession,
    router: LLMRouter,
    seed_ingest_job: Any,
) -> None:
    arxiv_id = _arxiv_id()
    ids = await seed_ingest_job(db_session, arxiv_id=arxiv_id)
    storage = S3Storage()
    html_key = StorageKeys.arxiv_html(ids["paper_id"], "v1")
    store = JobStore(db_session)

    first_transport = httpx.ASGITransport(
        app=_make_candidate_status_stub(
            eprint_status=404,
            eprint_body=b"",
            html_status=200,
            html_body=_VALID_STORED_HTML,
            pdf_bytes=_VALID_PRIORITY_PDF,
        )
    )
    async with httpx.AsyncClient(transport=first_transport, base_url="http://arxiv.test") as http:
        first_ctx = {
            "router": router,
            "arxiv_http": http,
            "redis": _FakeRedis(),
            "settings": CoreSettings(alinea_arxiv_base_url="http://arxiv.test"),
            "throttle": _noop_throttle,
        }
        first_job = await store.claim(ids["job_id"])
        assert first_job is not None
        await ingest_paper(first_ctx, store, first_job)

    revision = (
        (
            await db_session.execute(
                select(DocumentRevision).where(DocumentRevision.paper_id == ids["paper_id"])
            )
        )
        .scalars()
        .one()
    )
    assert revision.stats["selected_source"] == {
        "storage_key": html_key,
        "sha256": hashlib.sha256(_VALID_STORED_HTML).hexdigest(),
    }
    assert revision.stats["revision_content_sha256"] == (
        worker_pipeline._canonical_content_sha256(revision.content)
    )

    reingest_job_id = await store.enqueue(
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
    second_transport = httpx.ASGITransport(
        app=_make_candidate_status_stub(
            eprint_status=404,
            eprint_body=b"",
            html_status=200,
            html_body=_VALID_CHANGED_HTML,
            pdf_bytes=_VALID_PRIORITY_PDF,
        )
    )
    async with httpx.AsyncClient(transport=second_transport, base_url="http://arxiv.test") as http:
        second_ctx = {
            "router": router,
            "arxiv_http": http,
            "redis": _FakeRedis(),
            "settings": CoreSettings(alinea_arxiv_base_url="http://arxiv.test"),
            "throttle": _noop_throttle,
        }
        reingest_job = await store.claim(reingest_job_id)
        assert reingest_job is not None
        with pytest.raises(FetchError) as error:
            await run_ingest(second_ctx, store, reingest_job)

    assert error.value.kind == "parse_error"
    assert await storage.get(storage.sources_bucket, html_key) == _VALID_STORED_HTML
    revisions = (
        await db_session.execute(
            select(DocumentRevision).where(DocumentRevision.paper_id == ids["paper_id"])
        )
    ).scalars()
    assert [str(item.id) for item in revisions.all()] == [str(revision.id)]


async def test_fresh_reparse_rejects_content_mismatch_with_matching_stored_identity(
    db_session: AsyncSession,
    router: LLMRouter,
    seed_ingest_job: Any,
) -> None:
    arxiv_id = _arxiv_id()
    ids = await seed_ingest_job(db_session, arxiv_id=arxiv_id)
    store = JobStore(db_session)
    first_transport = httpx.ASGITransport(
        app=_make_candidate_status_stub(
            eprint_status=404,
            eprint_body=b"",
            html_status=200,
            html_body=_VALID_STORED_HTML,
            pdf_bytes=_VALID_PRIORITY_PDF,
        )
    )
    async with httpx.AsyncClient(transport=first_transport, base_url="http://arxiv.test") as http:
        first_ctx = {
            "router": router,
            "arxiv_http": http,
            "redis": _FakeRedis(),
            "settings": CoreSettings(alinea_arxiv_base_url="http://arxiv.test"),
            "throttle": _noop_throttle,
        }
        first_job = await store.claim(ids["job_id"])
        assert first_job is not None
        await ingest_paper(first_ctx, store, first_job)

    revision = (
        (
            await db_session.execute(
                select(DocumentRevision).where(DocumentRevision.paper_id == ids["paper_id"])
            )
        )
        .scalars()
        .one()
    )
    original_content = json.loads(json.dumps(revision.content))
    original_parsed = parse_html_candidate(_VALID_STORED_HTML, pdf_text="")
    changed_parsed = parse_html_candidate(_VALID_CHANGED_HTML, pdf_text="")
    original_parsed_sha256 = worker_pipeline._canonical_content_sha256(
        original_parsed.content.model_dump()
    )
    assert revision.stats["parsed_content_sha256"] == original_parsed_sha256
    assert original_parsed_sha256 != worker_pipeline._canonical_content_sha256(
        changed_parsed.content.model_dump()
    )

    html_key = StorageKeys.arxiv_html(ids["paper_id"], "v1")
    changed_source_sha256 = hashlib.sha256(_VALID_CHANGED_HTML).hexdigest()
    revision.stats = {
        **revision.stats,
        "selected_source": {
            "storage_key": html_key,
            "sha256": changed_source_sha256,
        },
        "parsed_content_sha256": original_parsed_sha256,
        "revision_content_sha256": worker_pipeline._canonical_content_sha256(revision.content),
    }
    assert revision.stats["figure_asset_manifest"] == []
    html_asset = (
        (
            await db_session.execute(
                select(SourceAsset).where(
                    SourceAsset.paper_id == ids["paper_id"],
                    SourceAsset.kind == "arxiv_html",
                )
            )
        )
        .scalars()
        .one()
    )
    html_asset.byte_size = len(_VALID_CHANGED_HTML)
    html_asset.sha256 = changed_source_sha256
    storage = S3Storage()
    await storage.put(
        storage.sources_bucket,
        html_key,
        _VALID_CHANGED_HTML,
        content_type="text/html; charset=utf-8",
    )
    await db_session.commit()

    reingest_job_id = await store.enqueue(
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
    second_transport = httpx.ASGITransport(
        app=_make_candidate_status_stub(
            eprint_status=404,
            eprint_body=b"",
            html_status=200,
            html_body=_VALID_CHANGED_HTML,
            pdf_bytes=_VALID_PRIORITY_PDF,
        )
    )
    async with httpx.AsyncClient(transport=second_transport, base_url="http://arxiv.test") as http:
        second_ctx = {
            "router": router,
            "arxiv_http": http,
            "redis": _FakeRedis(),
            "settings": CoreSettings(alinea_arxiv_base_url="http://arxiv.test"),
            "throttle": _noop_throttle,
        }
        reingest_job = await store.claim(reingest_job_id)
        assert reingest_job is not None
        with pytest.raises(FetchError) as error:
            await run_ingest(second_ctx, store, reingest_job)

    assert error.value.kind == "parse_error"
    stored = await db_session.get(DocumentRevision, revision.id)
    assert stored is not None
    assert stored.content == original_content
    assert stored.stats["parsed_content_sha256"] == original_parsed_sha256


async def test_structuring_resume_rejects_mutated_revision_content(
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
            html_body=_VALID_STORED_HTML,
            pdf_bytes=_VALID_PRIORITY_PDF,
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
        first_job = await store.claim(ids["job_id"])
        assert first_job is not None
        await ingest_paper(ctx, store, first_job)

        revision = (
            (
                await db_session.execute(
                    select(DocumentRevision).where(DocumentRevision.paper_id == ids["paper_id"])
                )
            )
            .scalars()
            .one()
        )
        original_digest = revision.stats["revision_content_sha256"]
        mutated = json.loads(json.dumps(revision.content))
        paragraph = next(
            block
            for section in mutated["sections"]
            for block in section["blocks"]
            if block["type"] == "paragraph"
        )
        paragraph["inlines"][0]["v"] = "Tampered but structurally valid stored text."
        revision.content = mutated
        await db_session.commit()
        assert worker_pipeline._canonical_content_sha256(mutated) != original_digest

        completed = await store.get(ids["job_id"])
        assert completed is not None
        completed.status = "queued"
        completed.stage = "structuring"
        completed.error = None
        completed.finished_at = None
        await db_session.commit()
        resumed = await store.claim(ids["job_id"])
        assert resumed is not None
        with pytest.raises(FetchError) as error:
            await run_ingest(ctx, store, resumed)

    assert error.value.kind == "parse_error"
    stored = await db_session.get(DocumentRevision, revision.id)
    assert stored is not None
    assert stored.content == mutated
    assert stored.stats["revision_content_sha256"] == original_digest


async def test_candidate_with_missing_latex_figure_falls_back_to_pdf_extraction(
    db_session: AsyncSession,
    router: LLMRouter,
    seed_ingest_job: Any,
) -> None:
    ids = await seed_ingest_job(db_session, arxiv_id=_arxiv_id())
    transport = httpx.ASGITransport(
        app=_make_candidate_status_stub(
            eprint_status=200,
            eprint_body=_build_latex_archive(_LATEX_MISSING_ASSET_TEX),
            html_status=404,
            pdf_bytes=_VALID_MULTI_PARAGRAPH_PDF,
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
    assert revision.source_format == "pdf"
    assert revision.stats["candidate_failures"][0]["format"] == "latex"
    assert revision.stats["candidate_failures"][0]["code"] == "figure_asset_unresolved", (
        revision.stats["candidate_failures"]
    )
    assert revision.stats["candidate_failures"][0]["unresolved_figures"] == 1
    content = DocumentContent.model_validate(revision.content)
    figures = [block for _section, block in content.iter_blocks() if block.type == "figure"]
    assert figures
    assert all(
        block.asset_key and block.asset_key.startswith(f"figures/{ids['paper_id']}/{revision.id}/")
        for block in figures
    )
    assert revision.stats["figure_asset_failures"] == []


async def test_candidate_with_missing_second_panel_falls_back_to_pdf_extraction(
    db_session: AsyncSession,
    router: LLMRouter,
    seed_ingest_job: Any,
) -> None:
    ids = await seed_ingest_job(db_session, arxiv_id=_arxiv_id())
    transport = httpx.ASGITransport(
        app=_make_candidate_status_stub(
            eprint_status=200,
            eprint_body=_build_latex_archive(_LATEX_MULTI_PANEL_MISSING_ASSET_TEX),
            html_status=404,
            pdf_bytes=_VALID_MULTI_PARAGRAPH_PDF,
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
    assert revision.source_format == "pdf"
    latex_failure = revision.stats["candidate_failures"][0]
    assert latex_failure["format"] == "latex"
    assert latex_failure["code"] == "figure_asset_unresolved"
    assert latex_failure["unresolved_figures"] == 1
    assert latex_failure["figure_asset_failures"][0]["figure_id"]


@pytest.mark.parametrize(
    "latex_source",
    [_LATEX_STANDALONE_MISSING_ASSET_TEX, _LATEX_TABLE_MISSING_ASSET_TEX],
    ids=["standalone-graphics", "image-backed-table"],
)
async def test_candidate_with_missing_nonfigure_environment_asset_falls_back_to_pdf(
    latex_source: str,
    db_session: AsyncSession,
    router: LLMRouter,
    seed_ingest_job: Any,
) -> None:
    ids = await seed_ingest_job(db_session, arxiv_id=_arxiv_id())
    transport = httpx.ASGITransport(
        app=_make_candidate_status_stub(
            eprint_status=200,
            eprint_body=_build_latex_archive(latex_source),
            html_status=404,
            pdf_bytes=_VALID_MULTI_PARAGRAPH_PDF,
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
    assert revision.source_format == "pdf"
    latex_failure = revision.stats["candidate_failures"][0]
    assert latex_failure["format"] == "latex"
    assert latex_failure["code"] == "figure_asset_unresolved"
    assert latex_failure["unresolved_figures"] == 1


async def test_html_candidate_with_missing_second_image_falls_back_to_pdf_extraction(
    db_session: AsyncSession,
    router: LLMRouter,
    seed_ingest_job: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fetched: list[str] = []

    async def fetch_panel(
        _http: Any,
        *,
        source: str,
        payload_loader: Any,
        **_kwargs: Any,
    ) -> Any:
        del payload_loader
        fetched.append(source)
        if source == "panel-a.png":
            return worker_pipeline.FigureAssetPayload(
                _PNG_1X1,
                "png",
                "image/png",
                1,
                1,
                len(_PNG_1X1),
            )
        raise worker_pipeline.FigureAssetError(
            "asset_http_status", "synthetic missing second panel"
        )

    monkeypatch.setattr(worker_pipeline, "fetch_html_asset", fetch_panel)
    ids = await seed_ingest_job(db_session, arxiv_id=_arxiv_id())
    transport = httpx.ASGITransport(
        app=_make_candidate_status_stub(
            eprint_status=404,
            eprint_body=b"",
            html_status=200,
            html_body=_VALID_MULTI_IMAGE_HTML,
            pdf_bytes=_VALID_MULTI_PARAGRAPH_PDF,
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

    assert fetched == ["panel-a.png", "panel-b.png"]
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
    html_failure = next(
        failure
        for failure in revision.stats["candidate_failures"]
        if failure["format"] == "arxiv_html"
    )
    assert html_failure["code"] == "figure_asset_unresolved"
    assert html_failure["unresolved_figures"] == 1


async def test_candidate_materialization_deadline_does_not_starve_pdf_fallback(
    db_session: AsyncSession,
    router: LLMRouter,
    seed_ingest_job: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ExpiredDeadline:
        def remaining(self, _operation_limit_s: float | None = None) -> float:
            raise worker_pipeline.FigureAssetError(
                "materialization_timeout", "candidate deadline expired"
            )

    class LiveDeadline:
        def remaining(self, operation_limit_s: float | None = None) -> float:
            return 30.0 if operation_limit_s is None else min(30.0, operation_limit_s)

    starts = 0

    def start_deadline(
        _cls: type[worker_pipeline.MaterializationDeadline],
        *,
        timeout_s: float,
        **_kwargs: Any,
    ) -> Any:
        nonlocal starts
        assert timeout_s == worker_pipeline.MAX_DOCUMENT_MATERIALIZATION_SECONDS
        starts += 1
        return ExpiredDeadline() if starts == 1 else LiveDeadline()

    monkeypatch.setattr(
        worker_pipeline.MaterializationDeadline,
        "start",
        classmethod(start_deadline),
    )
    ids = await seed_ingest_job(db_session, arxiv_id=_arxiv_id())
    transport = httpx.ASGITransport(
        app=_make_candidate_status_stub(
            eprint_status=200,
            eprint_body=_build_latex_archive(_LATEX_MISSING_ASSET_TEX),
            html_status=404,
            pdf_bytes=_VALID_MULTI_PARAGRAPH_PDF,
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

    completed = await store.get(ids["job_id"])
    assert completed is not None
    assert completed.status == "succeeded", completed.error
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
    assert revision.stats["candidate_failures"][0]["code"] == "figure_asset_unresolved"
    assert revision.stats["candidate_failures"][0]["figure_asset_failures"][0]["code"] == (
        "materialization_timeout"
    )
    assert starts >= 3


async def test_validated_cache_persistence_failure_rolls_back_revision(
    db_session: AsyncSession,
    router: LLMRouter,
    seed_ingest_job: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class LiveDeadline:
        def remaining(self, operation_limit_s: float | None = None) -> float:
            return 30.0 if operation_limit_s is None else min(30.0, operation_limit_s)

    class ExpireOnSecondAsset:
        def __init__(self) -> None:
            self.calls = 0

        def remaining(self, operation_limit_s: float | None = None) -> float:
            self.calls += 1
            if self.calls == 2:
                raise worker_pipeline.FigureAssetError(
                    "materialization_timeout", "synthetic persistence deadline expired"
                )
            return 30.0 if operation_limit_s is None else min(30.0, operation_limit_s)

    starts = 0

    def start_deadline(
        _cls: type[worker_pipeline.MaterializationDeadline],
        **_kwargs: Any,
    ) -> Any:
        nonlocal starts
        starts += 1
        return LiveDeadline() if starts == 1 else ExpireOnSecondAsset()

    monkeypatch.setattr(
        worker_pipeline.MaterializationDeadline,
        "start",
        classmethod(start_deadline),
    )
    ids = await seed_ingest_job(db_session, arxiv_id=_arxiv_id())
    transport = httpx.ASGITransport(
        app=_make_latex_arxiv_stub(_build_latex_archive(_LATEX_TWO_VALID_FIGURES_TEX))
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
        with pytest.raises(worker_pipeline.FigureAssetError, match="deadline expired"):
            await run_ingest(ctx, store, job)

    revisions = (
        await db_session.execute(
            select(DocumentRevision).where(DocumentRevision.paper_id == ids["paper_id"])
        )
    ).scalars()
    assert revisions.all() == []


async def test_nested_figure_fetch_timeout_remains_retryable_when_all_candidates_fail(
    db_session: AsyncSession,
    router: LLMRouter,
    seed_ingest_job: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def timeout_asset(*_args: Any, **_kwargs: Any) -> Any:
        raise worker_pipeline.FigureAssetError(
            "asset_fetch_timeout", "synthetic figure fetch timeout"
        )

    monkeypatch.setattr(worker_pipeline, "fetch_html_asset", timeout_asset)
    ids = await seed_ingest_job(db_session, arxiv_id=_arxiv_id())
    transport = httpx.ASGITransport(
        app=_make_candidate_status_stub(
            eprint_status=200,
            eprint_body=b"not a latex archive",
            html_status=200,
            html_body=_VALID_EXTERNAL_FIGURE_HTML,
            pdf_bytes=_incomplete_original_pdf(),
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

    assert error.value.kind == "asset_fetch_timeout"
    diagnostics = json.loads(str(error.value))
    html_failure = next(
        item for item in diagnostics["candidates"] if item["format"] == "arxiv_html"
    )
    assert html_failure["code"] == "figure_asset_unresolved"
    assert html_failure["figure_asset_failures"][0]["code"] == "asset_fetch_timeout"
    revisions = (
        await db_session.execute(
            select(DocumentRevision).where(DocumentRevision.paper_id == ids["paper_id"])
        )
    ).scalars()
    assert revisions.all() == []


@pytest.mark.parametrize(
    ("raised_code", "expected_code"),
    [
        ("conversion_crashed", "conversion_crashed"),
        ("conversion_lifecycle", "conversion_lifecycle"),
        ("conversion_timeout", "conversion_timeout"),
        ("materialization_timeout", "materialization_timeout"),
        (None, "figure_asset_error"),
    ],
    ids=[
        "conversion-crashed",
        "conversion-lifecycle",
        "conversion-timeout",
        "materialization-timeout",
        "generic-child-error",
    ],
)
async def test_fresh_candidate_operational_figure_failure_remains_retryable(
    raised_code: str | None,
    expected_code: str,
    db_session: AsyncSession,
    router: LLMRouter,
    seed_ingest_job: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail_asset(*_args: Any, **_kwargs: Any) -> Any:
        if raised_code is None:
            raise RuntimeError("synthetic child worker failure")
        raise worker_pipeline.FigureAssetError(raised_code, "synthetic operational failure")

    monkeypatch.setattr(worker_pipeline, "fetch_html_asset", fail_asset)
    ids = await seed_ingest_job(db_session, arxiv_id=_arxiv_id())
    transport = httpx.ASGITransport(
        app=_make_candidate_status_stub(
            eprint_status=200,
            eprint_body=b"not a latex archive",
            html_status=200,
            html_body=_VALID_EXTERNAL_FIGURE_HTML,
            pdf_bytes=_incomplete_original_pdf(),
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

    assert error.value.kind == expected_code
    revisions = (
        await db_session.execute(
            select(DocumentRevision).where(DocumentRevision.paper_id == ids["paper_id"])
        )
    ).scalars()
    assert revisions.all() == []


async def test_all_deterministic_figure_gate_failures_preserve_unresolved_code(
    db_session: AsyncSession,
    router: LLMRouter,
    seed_ingest_job: Any,
) -> None:
    ids = await seed_ingest_job(db_session, arxiv_id=_arxiv_id())
    transport = httpx.ASGITransport(
        app=_make_candidate_status_stub(
            eprint_status=200,
            eprint_body=_build_latex_archive(_LATEX_MISSING_ASSET_TEX),
            html_status=200,
            html_body=_VALID_EXTERNAL_FIGURE_HTML,
            pdf_bytes=_incomplete_original_pdf(),
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

    assert error.value.kind == "figure_asset_unresolved"
    diagnostics = json.loads(str(error.value))
    unresolved = [
        failure
        for failure in diagnostics["candidates"]
        if failure.get("code") == "figure_asset_unresolved"
    ]
    assert [failure["format"] for failure in unresolved] == ["latex", "arxiv_html"]
    assert all(failure["figure_asset_failures"] for failure in unresolved)


@pytest.mark.parametrize(
    ("status_code", "expected_code"),
    [
        (408, "asset_fetch_timeout"),
        (429, "rate_limited"),
        (503, "upstream_5xx"),
    ],
)
async def test_html_figure_http_status_remains_retryable_after_candidate_exhaustion(
    db_session: AsyncSession,
    router: LLMRouter,
    seed_ingest_job: Any,
    status_code: int,
    expected_code: str,
) -> None:
    ids = await seed_ingest_job(db_session, arxiv_id=_arxiv_id())
    transport = httpx.ASGITransport(app=_make_html_figure_status_stub(status_code))
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

    assert error.value.kind == expected_code
    diagnostics = json.loads(str(error.value))
    html_failure = next(
        failure for failure in diagnostics["candidates"] if failure["format"] == "arxiv_html"
    )
    assert html_failure["figure_asset_failures"][0]["code"] == expected_code


async def test_candidate_with_broken_html_figure_falls_back_to_pdf_extraction(
    db_session: AsyncSession,
    router: LLMRouter,
    seed_ingest_job: Any,
) -> None:
    ids = await seed_ingest_job(db_session, arxiv_id=_arxiv_id())
    transport = httpx.ASGITransport(
        app=_make_candidate_status_stub(
            eprint_status=200,
            eprint_body=b"not a latex archive",
            html_status=200,
            html_body=_VALID_EXTERNAL_FIGURE_HTML,
            pdf_bytes=_VALID_MULTI_PARAGRAPH_PDF,
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

    completed = await store.get(ids["job_id"])
    assert completed is not None
    assert completed.status == "succeeded", completed.error
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
    html_failure = next(
        item for item in revision.stats["candidate_failures"] if item["format"] == "arxiv_html"
    )
    assert html_failure["code"] == "figure_asset_unresolved"
    assert html_failure["unresolved_figures"] == 1
    assert html_failure["figure_asset_failures"][0]["figure_id"]
    content = DocumentContent.model_validate(revision.content)
    figures = [block for _section, block in content.iter_blocks() if block.type == "figure"]
    assert figures
    assert all(
        block.asset_key and block.asset_key.startswith(f"figures/{ids['paper_id']}/{revision.id}/")
        for block in figures
    )


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

    original_structure_pdf = IngestRun._structure_pdf

    async def structure_with_container_provenance(run: IngestRun, data: bytes) -> None:
        assert data == _VALID_MULTI_PARAGRAPH_PDF
        assert run._latex_archive_bytes == archive
        assert run.latex_binary_files["body.pdf"] == _VALID_MULTI_PARAGRAPH_PDF
        assert run.latex_main_tex_name == "main.tex"
        await original_structure_pdf(run, data)

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
        with monkeypatch.context() as patch:
            patch.setattr(IngestRun, "_structure_pdf", structure_with_container_provenance)
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
            ["embedded_pdf_wrapper", "pdf_open_error"],
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
    assert revision.stats["completeness"]["figure_count"] == 0
    assert revision.stats["figure_asset_manifest"] == []
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


@pytest.mark.parametrize(
    "html_body",
    [
        _VALID_INLINE_SVG_HTML,
        _VALID_INLINE_SVG_STYLE_ATTRIBUTE_HTML,
        _VALID_INLINE_SVG_STYLE_ELEMENT_HTML,
    ],
    ids=["presentation-attribute", "style-attribute", "style-element"],
)
async def test_html_inline_svg_is_rasterized_and_raw_is_not_persisted(
    db_session: AsyncSession,
    router: LLMRouter,
    seed_ingest_job: Any,
    html_body: bytes,
) -> None:
    ids = await seed_ingest_job(db_session, arxiv_id=_arxiv_id())
    transport = httpx.ASGITransport(
        app=_make_candidate_status_stub(
            eprint_status=404,
            eprint_body=b"",
            html_status=200,
            html_body=html_body,
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


async def test_unsafe_inline_svg_css_failure_does_not_persist_raw(
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
            html_body=_UNSAFE_INLINE_SVG_STYLE_HTML,
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
    assert revision.source_format == "pdf"
    html_failure = next(
        failure
        for failure in revision.stats["candidate_failures"]
        if failure["format"] == "arxiv_html"
    )
    assert html_failure["code"] == "figure_asset_unresolved"
    assert html_failure["figure_asset_failures"][0]["code"] == "unsafe_inline_figure"
    assert "@import" not in json.dumps(revision.content)
    assert revision.stats["figure_asset_failures"] == []


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
    revision.stats = {
        **revision.stats,
        "legacy": True,
    }
    revision.stats.pop("candidate_failures", None)
    revision.stats.pop("completeness", None)
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
    revision.stats = {**revision.stats, "legacy": True}
    revision.stats.pop("completeness", None)
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
    assert json.loads(job.error or "{}")["code"] == "parse_error"
    await db_session.refresh(revision)
    assert "completeness" not in revision.stats


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
    assert rev.parser_version == "pdf-1.2.0"
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
        revision.stats = {
            key: value
            for key, value in revision.stats.items()
            if key not in {"candidate_failures", "completeness"}
        }
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
            assert refreshed_revision.stats["candidate_failures"] == []

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


async def test_parsing_checkpoint_revalidates_figures_before_creating_revision(
    db_session: AsyncSession,
    router: LLMRouter,
    seed_ingest_job: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ids = await seed_ingest_job(db_session, arxiv_id=_arxiv_id())
    storage = S3Storage()
    pdf_key = StorageKeys.original_pdf(ids["paper_id"], "v1")
    html_key = StorageKeys.arxiv_html(ids["paper_id"], "v1")
    await storage.put(
        storage.sources_bucket,
        pdf_key,
        _VALID_PRIORITY_PDF,
        content_type="application/pdf",
    )
    await storage.put(
        storage.sources_bucket,
        html_key,
        _VALID_INLINE_SVG_HTML,
        content_type="text/html; charset=utf-8",
    )
    store = JobStore(db_session)
    await store.checkpoint(ids["job_id"], "fetching", {"source_version": "v1"}, progress=10)
    await store.checkpoint(
        ids["job_id"],
        "parsing",
        {
            "source_format": "arxiv_html",
            "parser_version": HTML_PARSER_VERSION,
            "candidate_failures": [],
            "completeness": {"accepted": True, "code": None},
            "adopt_from_revision_id": None,
            "source_storage_key": html_key,
            "source_sha256": hashlib.sha256(_VALID_INLINE_SVG_HTML).hexdigest(),
        },
        progress=20,
    )
    materialized: list[str] = []

    async def reject_asset(
        _data: bytes,
        source_name: str,
        _content_type: str | None = None,
        **_kwargs: Any,
    ) -> Any:
        materialized.append(source_name)
        raise worker_pipeline.FigureAssetError("image_invalid", "synthetic invalid image")

    monkeypatch.setattr(worker_pipeline, "_materialize_figure_payload", reject_asset)
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

    completed = await store.get(ids["job_id"])
    assert completed is not None
    assert completed.status == "failed"
    assert json.loads(completed.error or "{}")["code"] == "figure_asset_unresolved"
    assert materialized == ["inline.svg"]
    assert calls == {"pdf": 0, "eprint": 0, "html": 0}
    revisions = (
        await db_session.execute(
            select(DocumentRevision).where(DocumentRevision.paper_id == ids["paper_id"])
        )
    ).scalars()
    assert revisions.all() == []


@pytest.mark.parametrize(
    "failure_code",
    [
        "asset_fetch_timeout",
        "asset_fetch_failed",
        "rate_limited",
        "upstream_5xx",
        "conversion_crashed",
        "conversion_lifecycle",
        "conversion_timeout",
        "materialization_timeout",
        "figure_asset_error",
    ],
)
async def test_parsing_checkpoint_preserves_retryable_nested_figure_failure(
    db_session: AsyncSession,
    router: LLMRouter,
    seed_ingest_job: Any,
    monkeypatch: pytest.MonkeyPatch,
    failure_code: str,
) -> None:
    ids = await seed_ingest_job(db_session, arxiv_id=_arxiv_id())
    storage = S3Storage()
    pdf_key = StorageKeys.original_pdf(ids["paper_id"], "v1")
    html_key = StorageKeys.arxiv_html(ids["paper_id"], "v1")
    await storage.put(
        storage.sources_bucket,
        pdf_key,
        _VALID_PRIORITY_PDF,
        content_type="application/pdf",
    )
    await storage.put(
        storage.sources_bucket,
        html_key,
        _VALID_EXTERNAL_FIGURE_HTML,
        content_type="text/html; charset=utf-8",
    )
    store = JobStore(db_session)
    await store.checkpoint(ids["job_id"], "fetching", {"source_version": "v1"}, progress=10)
    await store.checkpoint(
        ids["job_id"],
        "parsing",
        {
            "source_format": "arxiv_html",
            "parser_version": HTML_PARSER_VERSION,
            "candidate_failures": [],
            "completeness": {"accepted": True, "code": None},
            "adopt_from_revision_id": None,
            "source_storage_key": html_key,
            "source_sha256": hashlib.sha256(_VALID_EXTERNAL_FIGURE_HTML).hexdigest(),
        },
        progress=20,
    )

    async def fail_asset(*_args: Any, **_kwargs: Any) -> Any:
        raise worker_pipeline.FigureAssetError(failure_code, "synthetic transient failure")

    monkeypatch.setattr(worker_pipeline, "fetch_html_asset", fail_asset)
    transport = httpx.ASGITransport(
        app=_make_counting_arxiv_stub(
            {"pdf": 0, "eprint": 0, "html": 0},
            pdf_status=500,
            latex_available=True,
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

    assert error.value.kind == failure_code
    diagnostics = json.loads(str(error.value))
    failure = diagnostics["candidates"][0]
    assert failure["code"] == "figure_asset_unresolved"
    assert failure["figure_asset_failures"][0]["code"] == failure_code
    revisions = (
        await db_session.execute(
            select(DocumentRevision).where(DocumentRevision.paper_id == ids["paper_id"])
        )
    ).scalars()
    assert revisions.all() == []


@pytest.mark.parametrize(
    "parsing_checkpoint",
    [
        {"parser_version": HTML_PARSER_VERSION},
        {
            "source_format": "arxiv_html",
            "parser_version": HTML_PARSER_VERSION,
            "candidate_failures": "not-a-list",
        },
        {
            "source_format": "arxiv_html",
            "parser_version": HTML_PARSER_VERSION,
            "candidate_failures": [],
            "completeness": {"accepted": True},
        },
        {
            "source_format": "arxiv_html",
            "parser_version": "html-999.0.0",
            "candidate_failures": [],
            "completeness": {"accepted": True},
        },
        {
            "source_format": "arxiv_html",
            "parser_version": worker_pipeline.LATEX_PARSER_VERSION,
            "candidate_failures": [],
            "completeness": {"accepted": True},
        },
        {
            "source_format": "arxiv_html",
            "parser_version": "html-not-semver",
            "candidate_failures": [],
            "completeness": {"accepted": True},
        },
    ],
    ids=[
        "missing-source-format",
        "candidate-failures-not-list",
        "current-missing-source-identity",
        "newer-version-is-not-stale",
        "other-family-is-not-stale",
        "malformed-version-is-not-stale",
    ],
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


@pytest.mark.parametrize(
    "resume_stage", ["parsing", "structuring"], ids=["parsing", "structuring"]
)
async def test_stale_parser_checkpoint_reselects_current_parser_and_completes(
    resume_stage: str,
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
    if resume_stage == "structuring":
        await store.checkpoint(
            ids["job_id"],
            "structuring",
            {"revision_id": revision_id, "adopt_from_revision_id": None},
            progress=35,
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

    completed = await store.get(ids["job_id"])
    assert completed is not None
    assert completed.status == "succeeded", completed.error
    assert calls == {"pdf": 0, "eprint": 1, "html": 0}
    revisions = (
        (
            await db_session.execute(
                select(DocumentRevision)
                .where(DocumentRevision.paper_id == ids["paper_id"])
                .order_by(DocumentRevision.created_at, DocumentRevision.id)
            )
        )
        .scalars()
        .all()
    )
    assert [str(item.id) for item in revisions if item.parser_version == "html-0.9.0"] == [
        revision_id
    ]
    current = [
        item for item in revisions if item.parser_version == worker_pipeline.LATEX_PARSER_VERSION
    ]
    assert len(current) == 1
    paper = await db_session.get(Paper, ids["paper_id"])
    assert paper is not None
    assert str(paper.latest_revision_id) == str(current[0].id)


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

    assert error.value.kind == "parse_error"
    await db_session.refresh(legacy_revision)
    assert legacy_revision.stats == {}


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


async def test_pdf_evidence_platform_failure_does_not_block_html_candidate(
    db_session: AsyncSession,
    router: LLMRouter,
    seed_ingest_job: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def unsupported(_data: bytes) -> Any:
        raise worker_pipeline.CandidateUnavailable(
            "pdf",
            "pdf_platform_unsupported",
            "synthetic unsupported platform",
        )

    monkeypatch.setattr(
        worker_pipeline,
        "count_pdf_text_evidence_isolated",
        unsupported,
    )
    ids = await seed_ingest_job(db_session, arxiv_id=_arxiv_id())
    transport = httpx.ASGITransport(
        app=_make_candidate_status_stub(
            eprint_status=404,
            eprint_body=b"",
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


@pytest.mark.parametrize("length_mode", ["declared", "lying"])
@pytest.mark.parametrize("source", ["eprint", "html", "pdf"])
async def test_arxiv_source_download_bounds_declared_and_actual_lengths_without_partial_persistence(
    source: str,
    length_mode: str,
    db_session: AsyncSession,
    router: LLMRouter,
    seed_ingest_job: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    limit = 32
    declared_length = 128 if length_mode == "declared" else 1
    if source == "eprint":
        monkeypatch.setattr(worker_pipeline, "MAX_ARXIV_EPRINT_BYTES", limit)
    elif source == "html":
        monkeypatch.setattr(worker_pipeline, "MAX_ARXIV_HTML_BYTES", limit)
    else:
        monkeypatch.setattr(worker_pipeline, "MAX_ARXIV_PDF_BYTES", limit)

    ids = await seed_ingest_job(db_session, arxiv_id=_arxiv_id())
    transport = httpx.ASGITransport(
        app=_make_oversized_source_stub(source, declared_length=declared_length)
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

    completed = await store.get(ids["job_id"])
    assert completed is not None
    storage = S3Storage()
    if source == "pdf":
        assert completed.status == "failed"
        assert json.loads(completed.error or "{}")["code"] == "source_too_large"
        revisions = (
            await db_session.execute(
                select(DocumentRevision).where(DocumentRevision.paper_id == ids["paper_id"])
            )
        ).scalars()
        assert revisions.all() == []
        oversized_key = StorageKeys.original_pdf(ids["paper_id"], "v1")
        oversized_kind = "pdf"
    else:
        assert completed.status == "succeeded", completed.error
        revision = (
            (
                await db_session.execute(
                    select(DocumentRevision).where(DocumentRevision.paper_id == ids["paper_id"])
                )
            )
            .scalars()
            .one()
        )
        expected_format = "arxiv_html" if source == "eprint" else "pdf"
        assert revision.source_format == expected_format
        failed_format = "latex" if source == "eprint" else "arxiv_html"
        failure = next(
            item
            for item in revision.stats["candidate_failures"]
            if item["format"] == failed_format
        )
        assert failure["code"] == "source_too_large"
        oversized_key = (
            StorageKeys.latex_tar(ids["paper_id"], "v1")
            if source == "eprint"
            else StorageKeys.arxiv_html(ids["paper_id"], "v1")
        )
        oversized_kind = "arxiv_latex" if source == "eprint" else "arxiv_html"

    source_assets = (
        await db_session.execute(
            select(SourceAsset).where(
                SourceAsset.paper_id == ids["paper_id"],
                SourceAsset.kind == oversized_kind,
            )
        )
    ).scalars()
    assert source_assets.all() == []
    with pytest.raises(ClientError):
        await storage.get(storage.sources_bucket, oversized_key)


async def test_all_deterministic_candidate_failures_preserve_pdf_error(
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
    assert json.loads(job.error or "{}")["code"] == "pdf_open_error"
