"""``index_embeddings`` ジョブ + 埋め込みインデクシングロジックのテスト(S12・Task 19)。

セマンティック検索(docs/10 §5 / spec 2026-07-16-semantic-search-design.md §5-6)の
埋め込みインデクシングを検証する。実 OpenAI へは接続せず ``FakeEmbeddingProvider`` を使う。
埋め込みストア(pgvector 実体)は **本テストでは差し替える**(``paper_embeddings`` /
``block_embeddings`` テーブル + HNSW index の実体は Task 32 の統合ゲートで検証する。
本環境の DB イメージは pgvector 非同梱)。

検証:
- paper は title+abstract、block は source_text を埋め込む。
- model / source_hash が一致するときは再計算しない(skip)。
- model 変更・source_text 変更時は再計算する。
- 共有 revision は一度だけ埋め込む(2 回目は全 skip)。
- ユーザー別 BYOK を運営キーより優先して解決する。
- フィーチャーフラグ off のときは何も埋め込まない。
"""

from __future__ import annotations

import uuid
from typing import Any

from alinea_core.db.models import DocumentRevision, LibraryItem, Paper, User
from alinea_core.document.blocks import Block, DocumentContent, Section, SectionHeading
from alinea_core.document.inlines import Inline
from alinea_core.jobs.store import JobStore
from alinea_core.search.rebuild import rebuild_block_search_index
from alinea_llm.errors import ErrorKind, ProviderError
from alinea_llm.testing.fake_provider import FakeEmbeddingProvider
from alinea_worker.tasks.index_embeddings import (
    EMBEDDING_JOB_KIND,
    index_paper,
    index_revision_blocks,
    paper_embedding_text,
    resolve_embedding_provider,
    run_index_embeddings_job,
    source_hash,
)
from sqlalchemy.ext.asyncio import AsyncSession

_MODEL = "text-embedding-3-small"
_DIM = 8  # FakeEmbeddingProvider 既定次元(実 1536 は Task 32 の DB 統合で検証)


# --------------------------------------------------------------------------- #
# In-memory 埋め込みストア(pgvector 実体の代替。EmbeddingStore プロトコル準拠)
# --------------------------------------------------------------------------- #
class InMemoryEmbeddingStore:
    def __init__(self) -> None:
        self.papers: dict[str, dict[str, Any]] = {}
        self.blocks: dict[tuple[str, str], dict[str, Any]] = {}

    async def get_paper_embedding_meta(self, paper_id: str) -> tuple[str, str] | None:
        row = self.papers.get(paper_id)
        return (row["model"], row["source_hash"]) if row else None

    async def upsert_paper_embedding(
        self, paper_id: str, *, model: str, dim: int, vector: list[float], source_hash: str
    ) -> None:
        self.papers[paper_id] = {
            "model": model,
            "dim": dim,
            "vector": vector,
            "source_hash": source_hash,
        }

    async def get_block_embedding_meta(self, revision_id: str) -> dict[str, tuple[str, str]]:
        return {
            block_id: (row["model"], row["source_hash"])
            for (rev, block_id), row in self.blocks.items()
            if rev == revision_id
        }

    async def upsert_block_embedding(
        self,
        revision_id: str,
        block_id: str,
        *,
        model: str,
        dim: int,
        vector: list[float],
        source_hash: str,
    ) -> None:
        self.blocks[(revision_id, block_id)] = {
            "model": model,
            "dim": dim,
            "vector": vector,
            "source_hash": source_hash,
        }


def _content() -> DocumentContent:
    return DocumentContent(
        quality_level="A",
        sections=[
            Section(
                id="sec-1",
                heading=SectionHeading(number="1", title="Introduction"),
                blocks=[
                    Block(
                        id="blk-a",
                        type="paragraph",
                        inlines=[Inline(t="text", v="Rectified flow straightens transport paths.")],
                    ),
                    Block(
                        id="blk-b",
                        type="paragraph",
                        inlines=[Inline(t="text", v="The model learns a velocity field over time.")],
                    ),
                ],
            )
        ],
    )


async def _seed_paper_revision(
    db: AsyncSession,
    *,
    title: str = "Rectified Flow",
    abstract: str = "A method for straight-line transport in generative models.",
) -> dict[str, str]:
    user = User(id=str(uuid.uuid4()), email=f"{uuid.uuid4().hex}@t.test")
    db.add(user)
    await db.flush()
    paper = Paper(
        id=str(uuid.uuid4()),
        title=title,
        abstract=abstract,
        visibility="private",
        owner_user_id=user.id,
    )
    db.add(paper)
    await db.flush()
    content = _content()
    revision = DocumentRevision(
        id=str(uuid.uuid4()),
        paper_id=paper.id,
        parser_version="test-1",
        quality_level="A",
        source_format="latex",
        content=content.model_dump(),
    )
    db.add(revision)
    await db.flush()
    paper.latest_revision_id = revision.id
    await rebuild_block_search_index(db, str(revision.id), content)
    li = LibraryItem(id=str(uuid.uuid4()), user_id=user.id, paper_id=paper.id, status="reading")
    db.add(li)
    await db.commit()
    return {
        "user_id": str(user.id),
        "paper_id": str(paper.id),
        "revision_id": str(revision.id),
        "library_item_id": str(li.id),
    }


# --------------------------------------------------------------------------- #
# source_hash / paper_embedding_text の純ロジック
# --------------------------------------------------------------------------- #
def test_source_hash_is_deterministic_and_content_sensitive() -> None:
    assert source_hash("hello world") == source_hash("hello world")
    assert source_hash("hello world") != source_hash("hello worlds")


def test_paper_embedding_text_combines_title_and_abstract() -> None:
    paper = Paper(id=str(uuid.uuid4()), title="T", abstract="A", visibility="private")
    text = paper_embedding_text(paper)
    assert "T" in text and "A" in text
    # 空 abstract でも title だけで有効な入力になる。
    paper2 = Paper(id=str(uuid.uuid4()), title="OnlyTitle", abstract="", visibility="private")
    assert "OnlyTitle" in paper_embedding_text(paper2)


# --------------------------------------------------------------------------- #
# index_paper
# --------------------------------------------------------------------------- #
async def test_index_paper_embeds_title_and_abstract(db_session: AsyncSession) -> None:
    ids = await _seed_paper_revision(db_session)
    paper = await db_session.get(Paper, ids["paper_id"])
    assert paper is not None
    provider = FakeEmbeddingProvider(dim=_DIM)
    store = InMemoryEmbeddingStore()

    result = await index_paper(paper, provider=provider, store=store, model=_MODEL, dim=_DIM)

    assert result == "indexed"
    assert provider.calls == 1
    saved = store.papers[ids["paper_id"]]
    assert saved["model"] == _MODEL
    assert saved["dim"] == _DIM
    assert len(saved["vector"]) == _DIM
    assert saved["source_hash"] == source_hash(paper_embedding_text(paper))


async def test_index_paper_skips_when_model_and_hash_unchanged(db_session: AsyncSession) -> None:
    ids = await _seed_paper_revision(db_session)
    paper = await db_session.get(Paper, ids["paper_id"])
    assert paper is not None
    provider = FakeEmbeddingProvider(dim=_DIM)
    store = InMemoryEmbeddingStore()

    first = await index_paper(paper, provider=provider, store=store, model=_MODEL, dim=_DIM)
    second = await index_paper(paper, provider=provider, store=store, model=_MODEL, dim=_DIM)

    assert first == "indexed"
    assert second == "skipped"
    assert provider.calls == 1  # 2 回目は埋め込み API を呼ばない


async def test_index_paper_recomputes_on_model_change(db_session: AsyncSession) -> None:
    ids = await _seed_paper_revision(db_session)
    paper = await db_session.get(Paper, ids["paper_id"])
    assert paper is not None
    provider = FakeEmbeddingProvider(dim=_DIM)
    store = InMemoryEmbeddingStore()

    await index_paper(paper, provider=provider, store=store, model=_MODEL, dim=_DIM)
    changed = await index_paper(
        paper, provider=provider, store=store, model="text-embedding-3-large", dim=_DIM
    )

    assert changed == "indexed"
    assert provider.calls == 2
    assert store.papers[ids["paper_id"]]["model"] == "text-embedding-3-large"


# --------------------------------------------------------------------------- #
# index_revision_blocks
# --------------------------------------------------------------------------- #
async def test_index_revision_blocks_embeds_source_text(db_session: AsyncSession) -> None:
    ids = await _seed_paper_revision(db_session)
    provider = FakeEmbeddingProvider(dim=_DIM)
    store = InMemoryEmbeddingStore()

    summary = await index_revision_blocks(
        db_session, ids["revision_id"], provider=provider, store=store, model=_MODEL, dim=_DIM
    )

    assert summary["indexed"] == 2
    assert summary["skipped"] == 0
    assert (ids["revision_id"], "blk-a") in store.blocks
    assert (ids["revision_id"], "blk-b") in store.blocks
    assert store.blocks[(ids["revision_id"], "blk-a")]["dim"] == _DIM


async def test_index_revision_blocks_shared_revision_indexed_once(
    db_session: AsyncSession,
) -> None:
    ids = await _seed_paper_revision(db_session)
    provider = FakeEmbeddingProvider(dim=_DIM)
    store = InMemoryEmbeddingStore()

    first = await index_revision_blocks(
        db_session, ids["revision_id"], provider=provider, store=store, model=_MODEL, dim=_DIM
    )
    second = await index_revision_blocks(
        db_session, ids["revision_id"], provider=provider, store=store, model=_MODEL, dim=_DIM
    )

    assert first["indexed"] == 2
    assert second["indexed"] == 0
    assert second["skipped"] == 2
    assert provider.calls == 1  # 共有 revision は一度だけ埋め込む


# --------------------------------------------------------------------------- #
# per-user BYOK 解決
# --------------------------------------------------------------------------- #
def test_resolve_embedding_provider_prefers_user_byok() -> None:
    built: list[tuple[str, str]] = []

    def factory(provider_name: str, api_key: str) -> Any:
        built.append((provider_name, api_key))
        return FakeEmbeddingProvider(dim=_DIM)

    provider, source = resolve_embedding_provider(
        provider_name="openai",
        user_api_key="sk-user",
        operator_api_key="sk-operator",
        provider_factory=factory,
    )
    assert provider is not None
    assert source == "user"
    assert built == [("openai", "sk-user")]


def test_resolve_embedding_provider_falls_back_to_operator() -> None:
    built: list[tuple[str, str]] = []

    def factory(provider_name: str, api_key: str) -> Any:
        built.append((provider_name, api_key))
        return FakeEmbeddingProvider(dim=_DIM)

    provider, source = resolve_embedding_provider(
        provider_name="openai",
        user_api_key=None,
        operator_api_key="sk-operator",
        provider_factory=factory,
    )
    assert provider is not None
    assert source == "operator"
    assert built == [("openai", "sk-operator")]


def test_resolve_embedding_provider_none_when_no_key() -> None:
    provider, source = resolve_embedding_provider(
        provider_name="openai",
        user_api_key=None,
        operator_api_key=None,
        provider_factory=lambda p, k: FakeEmbeddingProvider(),
    )
    assert provider is None
    assert source is None


# --------------------------------------------------------------------------- #
# ジョブハンドラ(フィーチャーフラグ)
# --------------------------------------------------------------------------- #
class _Settings:
    def __init__(self, *, enabled: bool) -> None:
        self.semantic_search_enabled = enabled


async def test_job_indexes_paper_and_blocks_when_flag_on(db_session: AsyncSession) -> None:
    ids = await _seed_paper_revision(db_session)
    store_backend = InMemoryEmbeddingStore()
    provider = FakeEmbeddingProvider(dim=_DIM)
    job_store = JobStore(db_session)
    job_id = await job_store.enqueue(
        kind="ingest",  # 既存の許可 kind で行を作り、ハンドラだけ index_embeddings を回す
        payload={"scope": "revision", "revision_id": ids["revision_id"], "paper_id": ids["paper_id"]},
        priority="bulk",
        user_id=ids["user_id"],
        paper_id=ids["paper_id"],
    )
    job = await job_store.claim(job_id)

    ctx = {
        "settings": _Settings(enabled=True),
        "embedding_store": store_backend,
        "embedding_provider": provider,
        "embedding_model": _MODEL,
        "embedding_dim": _DIM,
    }
    await run_index_embeddings_job(ctx, job_store, job)

    assert ids["paper_id"] in store_backend.papers
    assert (ids["revision_id"], "blk-a") in store_backend.blocks
    done = await job_store.get(job_id)
    assert done is not None
    assert done.status == "succeeded"


async def test_job_is_noop_when_flag_off(db_session: AsyncSession) -> None:
    ids = await _seed_paper_revision(db_session)
    store_backend = InMemoryEmbeddingStore()
    provider = FakeEmbeddingProvider(dim=_DIM)
    job_store = JobStore(db_session)
    job_id = await job_store.enqueue(
        kind="ingest",
        payload={"scope": "revision", "revision_id": ids["revision_id"], "paper_id": ids["paper_id"]},
        priority="bulk",
        user_id=ids["user_id"],
        paper_id=ids["paper_id"],
    )
    job = await job_store.claim(job_id)

    ctx = {
        "settings": _Settings(enabled=False),
        "embedding_store": store_backend,
        "embedding_provider": provider,
        "embedding_model": _MODEL,
        "embedding_dim": _DIM,
    }
    await run_index_embeddings_job(ctx, job_store, job)

    assert store_backend.papers == {}
    assert store_backend.blocks == {}
    assert provider.calls == 0


def test_embedding_job_kind_constant() -> None:
    assert EMBEDDING_JOB_KIND == "index_embeddings"


# --------------------------------------------------------------------------- #
# fail-closed: 埋め込み失敗 → 部分失敗を記録して succeed(ハングさせない)
# --------------------------------------------------------------------------- #
class _FailingProvider:
    name = "openai"

    def __init__(self) -> None:
        self.calls = 0

    async def embed(self, req: Any) -> Any:
        self.calls += 1
        raise ProviderError(ErrorKind.SERVER, self.name, req.model, "boom")


async def test_job_records_partial_failure_and_still_succeeds(db_session: AsyncSession) -> None:
    """埋め込み失敗(ProviderError)は保存せず部分失敗を記録し、ジョブは succeed 確定する。"""
    ids = await _seed_paper_revision(db_session)
    store_backend = InMemoryEmbeddingStore()
    job_store = JobStore(db_session)
    job_id = await job_store.enqueue(
        kind="ingest",
        payload={"scope": "revision", "revision_id": ids["revision_id"], "paper_id": ids["paper_id"]},
        priority="bulk",
        user_id=ids["user_id"],
        paper_id=ids["paper_id"],
    )
    job = await job_store.claim(job_id)

    ctx = {
        "settings": _Settings(enabled=True),
        "embedding_store": store_backend,
        "embedding_provider": _FailingProvider(),
        "embedding_model": _MODEL,
        "embedding_dim": _DIM,
    }
    await run_index_embeddings_job(ctx, job_store, job)

    # 失敗ベクトルは保存しない(fail-closed)。
    assert store_backend.papers == {}
    assert store_backend.blocks == {}
    done = await job_store.get(job_id)
    assert done is not None
    assert done.status == "succeeded"
    # 部分失敗が可視化されている。
    assert done.log and any(e.get("level") == "partial_failure" for e in done.log)


async def test_job_succeeds_even_if_record_partial_failure_raises(
    db_session: AsyncSession,
) -> None:
    """record_partial_failure(ログ用途)が例外を投げても succeed へ必ず進む(ハング防止)。"""
    ids = await _seed_paper_revision(db_session)
    store_backend = InMemoryEmbeddingStore()
    job_store = JobStore(db_session)
    job_id = await job_store.enqueue(
        kind="ingest",
        payload={"scope": "revision", "revision_id": ids["revision_id"], "paper_id": ids["paper_id"]},
        priority="bulk",
        user_id=ids["user_id"],
        paper_id=ids["paper_id"],
    )
    job = await job_store.claim(job_id)

    async def _boom(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("record failed")

    # ログ記録を故障させる(succeed 遷移を隔離できているか検証)。
    job_store.record_partial_failure = _boom  # type: ignore[method-assign]

    ctx = {
        "settings": _Settings(enabled=True),
        "embedding_store": store_backend,
        "embedding_provider": _FailingProvider(),
        "embedding_model": _MODEL,
        "embedding_dim": _DIM,
    }
    await run_index_embeddings_job(ctx, job_store, job)

    done = await job_store.get(job_id)
    assert done is not None
    assert done.status == "succeeded"
    assert done.result["summary"]["partial_failure_log"] == "record_failed"
