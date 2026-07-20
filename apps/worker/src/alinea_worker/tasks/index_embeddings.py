"""``index_embeddings`` ジョブ: 論文/ブロックの埋め込みインデクシング(S12・Task 19)。

セマンティック検索(docs/10 §5 / spec 2026-07-16-semantic-search-design.md §5-6)の埋め込みを
生成し、pgvector の ``paper_embeddings`` / ``block_embeddings`` に upsert する。

方針:
- **論文粒度**: title + abstract(原文=言語非依存)を 1 ベクトル化する。
- **ブロック粒度**: ``block_search_index.source_text``(訳文ではなく原文)を埋める。多言語
  モデルなら日本語クエリが英語 source にヒットする(spec §D3)。
- **skip 判定**: 既存行の ``model`` と ``source_hash`` が一致するなら再計算しない。モデル切替
  (ベクトル空間変更)や source 変更のときだけ埋め直す(spec §6.4)。
- **共有 revision**: block_embeddings は revision 単位で 1:1。共有 revision は一度埋めれば
  全ユーザー共用で足りるため、2 回目以降は全 skip になる。
- **BYOK**: ユーザーの BYOK を運営キーより優先して解決する(:func:`resolve_embedding_provider`)。
- **フィーチャーフラグ**: ``settings.semantic_search_enabled`` が off のときは何もしない
  (既存挙動を一切変えない)。
- **派生データ**: 埋め込みはバックアップに含めない。BYOK 秘密鍵は埋め込みテーブルに保存しない。

DB 実体(pgvector テーブル + HNSW index)の統合検証は Task 32 のゲートで行う(本環境の DB
イメージは pgvector 非同梱)。本モジュールの純ロジックと skip/upsert・BYOK 解決は
FakeEmbeddingProvider + in-memory store で単体テストする。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

import xxhash
from alinea_core.db.models import DocumentRevision, Paper
from alinea_core.document.blocks import DocumentContent
from alinea_core.document.plaintext import block_to_plain
from alinea_core.jobs.store import JobStore
from alinea_llm.errors import ProviderError
from alinea_llm.protocols import EmbeddingProvider
from alinea_llm.providers.openai_embeddings import (
    DEFAULT_EMBEDDING_DIM,
    DEFAULT_EMBEDDING_MODEL,
)
from alinea_llm.types import EmbeddingRequest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# jobs.kind の値。Alembic 0016 は kind CHECK を触らない(段階導入)ため、統合時に
# ck_jobs_kind へ 'index_embeddings' を union する後続 migration が必要(spec §9)。
EMBEDDING_JOB_KIND = "index_embeddings"

# 埋め込みの既定プロバイダ/モデル/次元(routing.yaml embedding: と一致)。
DEFAULT_EMBEDDING_PROVIDER = "openai"


def source_hash(text_value: str) -> str:
    """埋め込んだ原文の安定ハッシュ(xxhash64 hex)。再計算スキップ判定に使う。

    translation_units.source_hash / stable_id.content_hash と同じ xxhash64 系列。
    """
    return xxhash.xxh64(text_value.encode("utf-8")).hexdigest()


def paper_embedding_text(paper: Paper) -> str:
    """論文粒度の埋め込み入力(title + abstract。原文=言語非依存)。"""
    title = (paper.title or "").strip()
    abstract = (paper.abstract or "").strip()
    if abstract:
        return f"{title}\n\n{abstract}".strip()
    return title


# --------------------------------------------------------------------------- #
# 埋め込みストア(pgvector 実体の抽象。テストは in-memory で差し替える)
# --------------------------------------------------------------------------- #
class EmbeddingStore(Protocol):
    """paper_embeddings / block_embeddings への読み書き。"""

    async def get_paper_embedding_meta(self, paper_id: str) -> tuple[str, str] | None:
        """(model, source_hash) を返す。未登録は None。"""
        ...

    async def upsert_paper_embedding(
        self, paper_id: str, *, model: str, dim: int, vector: list[float], source_hash: str
    ) -> None: ...

    async def get_block_embedding_meta(self, revision_id: str) -> dict[str, tuple[str, str]]:
        """block_id -> (model, source_hash)。"""
        ...

    async def upsert_block_embedding(
        self,
        revision_id: str,
        block_id: str,
        *,
        model: str,
        dim: int,
        vector: list[float],
        source_hash: str,
    ) -> None: ...


class PgVectorEmbeddingStore:
    """pgvector を実体とする :class:`EmbeddingStore`(raw SQL)。

    Task 32 の DB 統合ゲートで検証する(本環境の DB は pgvector 非同梱)。ベクトルは
    pgvector が受理する ``[f1,f2,...]`` テキスト表現で渡す(asyncpg 経由でも安全)。
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @staticmethod
    def _vec_literal(vector: list[float]) -> str:
        return "[" + ",".join(repr(float(x)) for x in vector) + "]"

    async def get_paper_embedding_meta(self, paper_id: str) -> tuple[str, str] | None:
        row = (
            await self._session.execute(
                text("SELECT model, source_hash FROM paper_embeddings WHERE paper_id = :pid"),
                {"pid": paper_id},
            )
        ).first()
        return (row[0], row[1]) if row is not None else None

    async def upsert_paper_embedding(
        self, paper_id: str, *, model: str, dim: int, vector: list[float], source_hash: str
    ) -> None:
        await self._session.execute(
            text(
                "INSERT INTO paper_embeddings (paper_id, model, dim, embedding, source_hash) "
                "VALUES (:pid, :model, :dim, CAST(:emb AS vector), :sh) "
                "ON CONFLICT (paper_id) DO UPDATE SET "
                "model = EXCLUDED.model, dim = EXCLUDED.dim, "
                "embedding = EXCLUDED.embedding, source_hash = EXCLUDED.source_hash, "
                "updated_at = now()"
            ),
            {"pid": paper_id, "model": model, "dim": dim,
             "emb": self._vec_literal(vector), "sh": source_hash},
        )

    async def get_block_embedding_meta(self, revision_id: str) -> dict[str, tuple[str, str]]:
        rows = (
            await self._session.execute(
                text(
                    "SELECT block_id, model, source_hash FROM block_embeddings "
                    "WHERE revision_id = :rid"
                ),
                {"rid": revision_id},
            )
        ).fetchall()
        return {row[0]: (row[1], row[2]) for row in rows}

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
        await self._session.execute(
            text(
                "INSERT INTO block_embeddings "
                "(revision_id, block_id, model, dim, embedding, source_hash) "
                "VALUES (:rid, :bid, :model, :dim, CAST(:emb AS vector), :sh) "
                "ON CONFLICT (revision_id, block_id) DO UPDATE SET "
                "model = EXCLUDED.model, dim = EXCLUDED.dim, "
                "embedding = EXCLUDED.embedding, source_hash = EXCLUDED.source_hash, "
                "updated_at = now()"
            ),
            {"rid": revision_id, "bid": block_id, "model": model, "dim": dim,
             "emb": self._vec_literal(vector), "sh": source_hash},
        )


# --------------------------------------------------------------------------- #
# BYOK 解決(ユーザー優先・運営キーフォールバック)
# --------------------------------------------------------------------------- #
def resolve_embedding_provider(
    *,
    provider_name: str,
    user_api_key: str | None,
    operator_api_key: str | None,
    provider_factory: Callable[[str, str], EmbeddingProvider],
) -> tuple[EmbeddingProvider | None, str | None]:
    """埋め込みプロバイダを BYOK 優先で構築する(Task 13 のキー解決規則を踏襲)。

    返り値は ``(provider, source)``。source は "user" | "operator"。どちらのキーも無ければ
    ``(None, None)``(呼び出し側は index を skip して可視ログを残す)。
    """
    if user_api_key:
        return provider_factory(provider_name, user_api_key), "user"
    if operator_api_key:
        return provider_factory(provider_name, operator_api_key), "operator"
    return None, None


# --------------------------------------------------------------------------- #
# 埋め込み本体(純ロジック)
# --------------------------------------------------------------------------- #
async def index_paper(
    paper: Paper,
    *,
    provider: EmbeddingProvider,
    store: EmbeddingStore,
    model: str = DEFAULT_EMBEDDING_MODEL,
    dim: int = DEFAULT_EMBEDDING_DIM,
) -> str:
    """論文の title+abstract を埋め込み、paper_embeddings に upsert する。

    既存行の (model, source_hash) が一致するなら再計算しない。返り値 "indexed" | "skipped"。
    """
    doc_text = paper_embedding_text(paper)
    if not doc_text:
        return "skipped"
    sh = source_hash(doc_text)
    existing = await store.get_paper_embedding_meta(str(paper.id))
    if existing is not None and existing == (model, sh):
        return "skipped"
    result = await provider.embed(EmbeddingRequest(model=model, inputs=[doc_text], dimensions=dim))
    await store.upsert_paper_embedding(
        str(paper.id), model=model, dim=result.dim or dim, vector=result.vectors[0], source_hash=sh
    )
    return "indexed"


async def index_revision_blocks(
    session: AsyncSession,
    revision_id: str,
    *,
    provider: EmbeddingProvider,
    store: EmbeddingStore,
    model: str = DEFAULT_EMBEDDING_MODEL,
    dim: int = DEFAULT_EMBEDDING_DIM,
) -> dict[str, int]:
    """revision の全ブロック source_text を埋め込み、block_embeddings に upsert する。

    source_text は block_search_index と同じ原文平文(``block_to_plain``)。既存行の
    (model, source_hash) 一致ブロックは skip する(共有 revision は 2 回目以降 全 skip)。
    """
    revision = await session.get(DocumentRevision, revision_id)
    if revision is None:
        return {"indexed": 0, "skipped": 0}
    content = DocumentContent.model_validate(revision.content)
    # block_id -> source_text(原文平文)。空文字ブロックは索引対象外。
    block_text: dict[str, str] = {}
    for _sec, blk in content.iter_blocks():
        plain = block_to_plain(blk)
        if plain:
            block_text[blk.id] = plain
    if not block_text:
        return {"indexed": 0, "skipped": 0}

    existing = await store.get_block_embedding_meta(revision_id)
    to_embed: list[tuple[str, str, str]] = []  # (block_id, text, source_hash)
    skipped = 0
    for block_id, plain in block_text.items():
        sh = source_hash(plain)
        if existing.get(block_id) == (model, sh):
            skipped += 1
            continue
        to_embed.append((block_id, plain, sh))

    if not to_embed:
        return {"indexed": 0, "skipped": skipped}

    result = await provider.embed(
        EmbeddingRequest(model=model, inputs=[t for _bid, t, _sh in to_embed], dimensions=dim)
    )
    for (block_id, _plain, sh), vector in zip(to_embed, result.vectors, strict=True):
        await store.upsert_block_embedding(
            revision_id, block_id, model=model, dim=result.dim or dim, vector=vector, source_hash=sh
        )
    return {"indexed": len(to_embed), "skipped": skipped}


# --------------------------------------------------------------------------- #
# ジョブハンドラ
# --------------------------------------------------------------------------- #
def _feature_enabled(ctx: dict[str, Any]) -> bool:
    settings = ctx.get("settings")
    return bool(getattr(settings, "semantic_search_enabled", False))


async def run_index_embeddings_job(ctx: dict[str, Any], store: JobStore, job: Any) -> None:
    """``kind='index_embeddings'`` ハンドラ。

    payload: ``{"scope": "revision"|"paper", "revision_id": ..., "paper_id": ...}``。
    フラグ off のときは何もせず succeed する(既存挙動を変えない)。埋め込みプロバイダが
    未解決(キー無し)のときも可視 skip する。
    """
    session = store.session
    if not _feature_enabled(ctx):
        await store.succeed(str(job.id), {"skipped": "semantic_search_disabled"})
        return

    provider: EmbeddingProvider | None = ctx.get("embedding_provider")
    if provider is None:
        await store.succeed(str(job.id), {"skipped": "no_embedding_provider"})
        return

    embed_store: EmbeddingStore = ctx.get("embedding_store") or PgVectorEmbeddingStore(session)
    model = ctx.get("embedding_model") or DEFAULT_EMBEDDING_MODEL
    dim = ctx.get("embedding_dim") or DEFAULT_EMBEDDING_DIM
    payload = job.payload or {}
    summary: dict[str, Any] = {}

    try:
        paper_id = payload.get("paper_id")
        if paper_id:
            paper = await session.get(Paper, str(paper_id))
            if paper is not None:
                summary["paper"] = await index_paper(
                    paper, provider=provider, store=embed_store, model=model, dim=dim
                )
        revision_id = payload.get("revision_id")
        if revision_id:
            summary["blocks"] = await index_revision_blocks(
                session,
                str(revision_id),
                provider=provider,
                store=embed_store,
                model=model,
                dim=dim,
            )
        await session.commit()
    except ProviderError as exc:
        # 埋め込み失敗は保存しない(fail-closed)。検索は落とさない設計のため、ジョブ自体は
        # 部分失敗として記録し可視化する(P3)。
        await session.rollback()
        await store.record_partial_failure(
            str(job.id), "index_embeddings", {"code": "embedding_failed", "detail": str(exc)}
        )

    await store.succeed(str(job.id), {"summary": summary})


__all__ = [
    "DEFAULT_EMBEDDING_DIM",
    "DEFAULT_EMBEDDING_MODEL",
    "DEFAULT_EMBEDDING_PROVIDER",
    "EMBEDDING_JOB_KIND",
    "EmbeddingStore",
    "PgVectorEmbeddingStore",
    "index_paper",
    "index_revision_blocks",
    "paper_embedding_text",
    "resolve_embedding_provider",
    "run_index_embeddings_job",
    "source_hash",
]
