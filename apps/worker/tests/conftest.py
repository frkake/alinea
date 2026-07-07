"""apps/worker テスト用フィクスチャ(取り込みステートマシン M0-18)。

- arXiv は starlette ASGI スタブ + httpx.ASGITransport で決定的に差し替える(実通信なし)。
  packages/llm のモックサーバは /html を持たないため、LaTeXML フィクスチャを本スタブが提供する。
- LLM は決定的なスクリプトプロバイダ(translation_batch_v1 / summary_3line_v1)を注入する。
- DB は実 PostgreSQL、S3 は実 MinIO、Redis は in-memory フェイク(スロットルは no-op)。
  すべてユニーク UUID データで衝突を避ける(SQLite 代替禁止)。
"""

from __future__ import annotations

import json
import os
import re
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route
from yakudoku_core.arxiv.fetch import RedisLike
from yakudoku_core.db.models import LibraryItem, Paper, User
from yakudoku_core.jobs.store import JobStore
from yakudoku_core.settings import CoreSettings
from yakudoku_core.translation.placeholder import TOKEN_RE
from yakudoku_llm.router import LLMRouter
from yakudoku_llm.testing._assets import png_bytes
from yakudoku_llm.types import LLMRequest, LLMResponse, StreamEvent

os.environ.setdefault("NO_PROXY", "localhost,127.0.0.1")
os.environ.setdefault("no_proxy", "localhost,127.0.0.1")

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+asyncpg://yakudoku:yakudoku@localhost:5432/yakudoku",
)

# --------------------------------------------------------------------------- #
# LaTeXML フィクスチャ HTML(arXiv 公式 HTML と同じ ltx_* クラス体系)
# --------------------------------------------------------------------------- #

FIXTURE_HTML = """<!DOCTYPE html><html><body>
<article class="ltx_document">
<h1 class="ltx_title ltx_title_document">Mock Rectified Flow</h1>
<section class="ltx_section" id="S1">
  <h2 class="ltx_title ltx_title_section"><span class="ltx_tag ltx_tag_section">1 </span>Introduction</h2>
  <div class="ltx_para"><p class="ltx_p">We present a mock method for testing the ingest pipeline end to end.</p></div>
  <figure class="ltx_figure" id="S1.F1"><img class="ltx_graphics" src="fig1.png"/>
    <figcaption class="ltx_caption"><span class="ltx_tag ltx_tag_figure">Figure 1: </span>A mock figure.</figcaption></figure>
</section>
<section class="ltx_section" id="S2">
  <h2 class="ltx_title ltx_title_section"><span class="ltx_tag ltx_tag_section">2 </span>Method</h2>
  <div class="ltx_para"><p class="ltx_p">The method section describes the approach in detail for testing purposes here.</p></div>
</section>
<section class="ltx_appendix" id="A1">
  <h2 class="ltx_title ltx_title_appendix"><span class="ltx_tag ltx_tag_appendix">Appendix A </span>Proofs</h2>
  <div class="ltx_para"><p class="ltx_p">Appendix proof text that should not be auto translated at all.</p></div>
</section>
<section class="ltx_bibliography" id="bib">
  <h2 class="ltx_title ltx_title_bibliography">References</h2>
  <ul class="ltx_biblist"><li class="ltx_bibitem" id="bib.bib1">A. Author. Some Title. 2020.</li></ul>
</section>
</article></body></html>"""

_ATOM_XML = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/{id}v1</id>
    <published>2022-09-07T13:00:00Z</published>
    <title>Mock Rectified Flow</title>
    <summary>A deterministic mock abstract describing rectified flow for pipeline tests.</summary>
    <author><name>Xingchao Liu</name></author>
    <author><name>Chengyue Gong</name></author>
    <arxiv:doi>10.48550/arXiv.{id}</arxiv:doi>
    <arxiv:comment>ICLR 2023 (spotlight)</arxiv:comment>
    <arxiv:primary_category term="cs.LG"/>
    <category term="cs.LG"/>
    <category term="stat.ML"/>
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

_MINIMAL_PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


# --------------------------------------------------------------------------- #
# arXiv ASGI スタブ
# --------------------------------------------------------------------------- #


async def _query(request: Request) -> Response:
    id_list = request.query_params.get("id_list", "0000.00000")
    arxiv_id = re.sub(r"v\d+$", "", id_list)
    return Response(_ATOM_XML.format(id=arxiv_id), media_type="application/atom+xml")


async def _oai2(_request: Request) -> Response:
    return Response(_OAI_XML, media_type="text/xml")


async def _eprint(_request: Request) -> Response:
    return Response(b"", media_type="application/x-eprint-tar")


async def _html(request: Request) -> Response:
    path = request.path_params.get("path", "")
    if path.endswith((".png", ".svg")):
        return Response(png_bytes(64, 64), media_type="image/png")
    return Response(FIXTURE_HTML, media_type="text/html; charset=utf-8")


async def _pdf(_request: Request) -> Response:
    return Response(_MINIMAL_PDF, media_type="application/pdf")


def _make_arxiv_stub() -> Starlette:
    return Starlette(
        routes=[
            Route("/api/query", _query, methods=["GET"]),
            Route("/oai2", _oai2, methods=["GET"]),
            Route("/e-print/{arxiv_id:path}", _eprint, methods=["GET"]),
            Route("/html/{path:path}", _html, methods=["GET"]),
            Route("/pdf/{arxiv_id:path}", _pdf, methods=["GET"]),
        ]
    )


# --------------------------------------------------------------------------- #
# Fake Redis / Fake arq プール / スクリプト LLM プロバイダ
# --------------------------------------------------------------------------- #


class FakeRedis:
    """in-memory の最小 Redis(get / set NX PX EX / aclose)。TTL は無視する。"""

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


class FakeArqPool:
    """enqueue_job を記録するだけの arq プール代替(ジョブは走らせない)。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], str | None]] = []

    async def enqueue_job(
        self, function: str, *args: Any, _queue_name: str | None = None, **_kwargs: Any
    ) -> None:
        self.calls.append((function, args, _queue_name))


async def _noop_throttle(_redis: RedisLike) -> None:
    return None


_TARGET_RE = re.compile(r"^\[([^\]]+)\] \(([^)]+)\) (.*)$", re.MULTILINE)


def _echo_translate(encoded_text: str) -> str:
    """トークンを保ちつつ本文を固定日本語に置換した有効訳(検証を通過)。"""
    parts: list[str] = []
    pos = 0
    for m in TOKEN_RE.finditer(encoded_text):
        if encoded_text[pos : m.start()].strip():
            parts.append("これは訳文である。")
        parts.append(m.group(0))
        pos = m.end()
    if encoded_text[pos:].strip() or not parts:
        parts.append("これは訳文である。")
    return "".join(parts)


class ScriptProvider:
    """決定的 LLMProvider。translation_batch_v1 と summary_3line_v1 を返す。"""

    name = "fake"

    def __init__(self) -> None:
        self.calls = 0

    def _targets(self, req: LLMRequest) -> list[tuple[str, str, str]]:
        text = "".join(
            p.text or "" for msg in req.messages if msg.role == "user" for p in msg.parts
        )
        return _TARGET_RE.findall(text)

    async def generate_structured(self, req: LLMRequest) -> LLMResponse:
        self.calls += 1
        spec = req.json_schema
        if spec is not None and spec.name == "summary_3line_v1":
            data: dict[str, Any] = {
                "summary_lines": ["課題の要約行", "手法の要約行", "結果の要約行"],
                "suggested_tags": ["distillation", "solver"],
            }
        else:
            data = {
                "translations": [
                    {"id": bid, "ja": _echo_translate(txt)} for (bid, _t, txt) in self._targets(req)
                ]
            }
        return LLMResponse(
            text=json.dumps(data, ensure_ascii=False),
            parsed=data,
            provider=self.name,
            model=req.model,
            stop_reason="end",
        )

    async def generate(self, req: LLMRequest) -> LLMResponse:  # pragma: no cover
        raise NotImplementedError

    async def generate_stream(
        self, req: LLMRequest
    ) -> AsyncIterator[StreamEvent]:  # pragma: no cover
        raise NotImplementedError
        yield StreamEvent(type="end")

    async def count_tokens(self, req: LLMRequest) -> int:  # pragma: no cover
        return 1


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture
def settings() -> CoreSettings:
    # ASGITransport はホストを無視しパスでルーティングするため任意の URL でよい。
    return CoreSettings(yakudoku_arxiv_base_url="http://arxiv.test")


@pytest_asyncio.fixture
async def arxiv_http() -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=_make_arxiv_stub())
    async with httpx.AsyncClient(transport=transport, base_url="http://arxiv.test") as client:
        yield client


@pytest.fixture
def fake_redis() -> RedisLike:
    return FakeRedis()


@pytest.fixture
def script_provider() -> ScriptProvider:
    return ScriptProvider()


@pytest.fixture
def router(script_provider: ScriptProvider) -> LLMRouter:
    return LLMRouter([("fake", "deepseek-v4-flash", script_provider)])


@pytest_asyncio.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(DATABASE_URL, poolclass=None)
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as session:
        yield session
        await session.rollback()
    await engine.dispose()


@pytest.fixture
def worker_ctx(
    router: LLMRouter,
    arxiv_http: httpx.AsyncClient,
    fake_redis: RedisLike,
    settings: CoreSettings,
) -> dict[str, Any]:
    return {
        "router": router,
        "arxiv_http": arxiv_http,
        "redis": fake_redis,
        "settings": settings,
        "throttle": _noop_throttle,
    }


async def _seed_ingest_job(
    db: AsyncSession,
    *,
    arxiv_id: str,
    visibility: str = "public",
    with_user: bool = True,
) -> dict[str, str]:
    """Paper(仮タイトル)+ LibraryItem + ingest ジョブを作成し ID を返す。

    API(Task 18)が enqueue 時に行う最小セットアップを模す。
    """
    store = JobStore(db)
    user_id: str | None = None
    if with_user:
        user = User(id=str(uuid.uuid4()), email=f"{uuid.uuid4().hex}@t.test")
        db.add(user)
        await db.flush()
        user_id = user.id

    paper = Paper(
        id=str(uuid.uuid4()),
        arxiv_id=arxiv_id,
        title=f"arXiv:{arxiv_id}(取得中)",
        visibility=visibility,
        owner_user_id=user_id if visibility != "public" else None,
    )
    db.add(paper)
    await db.flush()

    library_item_id: str | None = None
    if user_id is not None:
        li = LibraryItem(id=str(uuid.uuid4()), user_id=user_id, paper_id=paper.id, status="planned")
        db.add(li)
        await db.flush()
        library_item_id = li.id
    await db.commit()

    job_id = await store.enqueue(
        kind="ingest",
        payload={
            "mode": "initial",
            "source": "arxiv",
            "arxiv_id": arxiv_id,
            "url": f"https://arxiv.org/abs/{arxiv_id}",
            "library_item_id": library_item_id,
        },
        priority="bulk",
        user_id=user_id,
        paper_id=paper.id,
        library_item_id=library_item_id,
    )
    return {
        "job_id": job_id,
        "paper_id": paper.id,
        "library_item_id": library_item_id or "",
        "user_id": user_id or "",
    }


# 型: seed ヘルパの呼び出しシグネチャ(テストからは fixture 経由で使う)。
SeedIngestJob = Any


@pytest.fixture
def seed_ingest_job() -> SeedIngestJob:
    """Paper + LibraryItem + ingest ジョブを作る async ヘルパを返す。"""
    return _seed_ingest_job


@pytest.fixture
def arq_pool() -> FakeArqPool:
    return FakeArqPool()
