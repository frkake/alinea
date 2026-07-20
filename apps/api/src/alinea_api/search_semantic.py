"""セマンティック検索の接続層(S12。docs/10 §5・spec 2026-07-16-semantic-search-design.md)。

``SEMANTIC_SEARCH_ENABLED`` が on のときだけ横断検索(routers/search.py)と「似た論文」
エンドポイントが使う。設計上の要点:

- **ANN は差し替え可能**にする。本番は pgvector(``PgVectorSemanticIndex``)だが、開発 DB は
  pgvector 非同梱(migration 0016 のコメント・T32 ゲート)。テストは in-memory
  (``InMemorySemanticIndex``)+ ``FakeEmbeddingProvider`` で決定的に検証する。
- **ユーザー境界**: ANN 候補は必ず ``library_items.user_id`` で自分のライブラリに絞る。共有
  revision の埋め込みでも、返るのは自分の library_item だけ(他ユーザーの近傍は絶対に返さない)。
- **BYOK**: クエリ埋め込みのキーは BYOK(ユーザー)→ 運営キーの順で解決する(T13/T19 の
  ``LLMKeyStore.resolve_or_none`` を流用)。どちらも無ければセマンティック経路を使わず縮退する。
- **縮退(fail-open)**: 埋め込みプロバイダ失敗・空 index・キー未設定はすべて「セマンティックを
  使わない」に落とし、PGroonga のみの結果(= フラグ off と同じ順序)を返す(P3: 検索は落とさない)。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from alinea_core.search.fusion import cosine_similarity
from alinea_llm.errors import ProviderError
from alinea_llm.protocols import EmbeddingProvider
from alinea_llm.providers.openai_embeddings import (
    DEFAULT_EMBEDDING_DIM,
    DEFAULT_EMBEDDING_MODEL,
    OpenAIEmbeddingProvider,
)
from alinea_llm.types import EmbeddingRequest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from alinea_api.llm.key_store import DbKeyStore
from alinea_api.settings import ApiSettings

# 埋め込みプロバイダ名(routing.yaml embedding: と一致。T19)。BYOK 解決の provider キーに使う。
EMBEDDING_PROVIDER_NAME = "openai"

# 融合前に各リストから取る上限(spec §6.2 / brief: lexical・semantic 各 top-100)。
SEMANTIC_TOP_K = 100

# 「似た論文」の上位件数(spec §6.3 / brief: top-10)。
SIMILAR_TOP_K = 10


@dataclass(slots=True, frozen=True)
class SemanticNeighbor:
    """ANN 近傍 1 件。``similarity`` は 0〜1 のコサイン類似度(``1 - cosine_distance``)。"""

    library_item_id: str
    similarity: float


class SemanticIndex(Protocol):
    """pgvector ANN の抽象(本番=raw SQL / テスト=in-memory)。

    どのメソッドも「自分のライブラリ内の論文だけ」を返すこと(ユーザー境界)。
    """

    async def query_neighbors(
        self, *, query_vector: list[float], user_id: str, top_k: int, model: str
    ) -> list[SemanticNeighbor]:
        """クエリベクトルに近い自分の library_item を類似度降順で返す(横断検索の semantic 側)。"""
        ...

    async def paper_neighbors(
        self,
        *,
        paper_id: str,
        user_id: str,
        top_k: int,
        model: str,
        exclude_library_item_id: str,
    ) -> list[SemanticNeighbor] | None:
        """対象論文に近い自分の他 library_item を返す(「似た論文」)。

        対象論文の埋め込みが無いときは ``None``(= indexing 中)。有るが近傍が無ければ ``[]``。
        自分自身(``exclude_library_item_id``)は必ず除外する。
        """
        ...


# --------------------------------------------------------------------------- #
# 本番実装: pgvector(raw SQL)。開発 DB は pgvector 非同梱のため T32 ゲートで検証する。
# --------------------------------------------------------------------------- #
class PgVectorSemanticIndex:
    """pgvector を実体とする :class:`SemanticIndex`。

    ``paper_embeddings``(論文粒度・D3 第一段)に対する HNSW cosine ANN。候補は
    ``library_items`` を JOIN して ``user_id`` で絞る(ユーザー境界)。model 不一致行は除外
    (ベクトル空間の混在防止)。
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @staticmethod
    def _vec_literal(vector: list[float]) -> str:
        return "[" + ",".join(repr(float(x)) for x in vector) + "]"

    async def query_neighbors(
        self, *, query_vector: list[float], user_id: str, top_k: int, model: str
    ) -> list[SemanticNeighbor]:
        rows = (
            await self._session.execute(
                text(
                    "SELECT li.id AS library_item_id, "
                    "  1 - (pe.embedding <=> CAST(:qvec AS vector)) AS similarity "
                    "FROM paper_embeddings pe "
                    "JOIN library_items li "
                    "  ON li.paper_id = pe.paper_id AND li.user_id = CAST(:uid AS uuid) "
                    "WHERE pe.model = :model "
                    "ORDER BY pe.embedding <=> CAST(:qvec AS vector) "
                    "LIMIT :k"
                ),
                {"qvec": self._vec_literal(query_vector), "uid": user_id, "model": model,
                 "k": top_k},
            )
        ).all()
        return [SemanticNeighbor(str(r.library_item_id), float(r.similarity)) for r in rows]

    async def paper_neighbors(
        self,
        *,
        paper_id: str,
        user_id: str,
        top_k: int,
        model: str,
        exclude_library_item_id: str,
    ) -> list[SemanticNeighbor] | None:
        seed = (
            await self._session.execute(
                text(
                    "SELECT 1 FROM paper_embeddings WHERE paper_id = CAST(:pid AS uuid) "
                    "AND model = :model"
                ),
                {"pid": paper_id, "model": model},
            )
        ).first()
        if seed is None:
            return None  # 対象論文の埋め込みが未生成 = indexing 中。
        rows = (
            await self._session.execute(
                text(
                    "WITH seed AS ("
                    "  SELECT embedding FROM paper_embeddings "
                    "  WHERE paper_id = CAST(:pid AS uuid) AND model = :model"
                    ") "
                    "SELECT li.id AS library_item_id, "
                    "  1 - (pe.embedding <=> (SELECT embedding FROM seed)) AS similarity "
                    "FROM paper_embeddings pe "
                    "JOIN library_items li "
                    "  ON li.paper_id = pe.paper_id AND li.user_id = CAST(:uid AS uuid) "
                    "WHERE pe.model = :model AND li.id <> CAST(:exclude AS uuid) "
                    "ORDER BY pe.embedding <=> (SELECT embedding FROM seed) "
                    "LIMIT :k"
                ),
                {"pid": paper_id, "uid": user_id, "model": model,
                 "exclude": exclude_library_item_id, "k": top_k},
            )
        ).all()
        return [SemanticNeighbor(str(r.library_item_id), float(r.similarity)) for r in rows]


# --------------------------------------------------------------------------- #
# テスト実装: in-memory コサイン(pgvector 非依存・決定的)。
# --------------------------------------------------------------------------- #
@dataclass(slots=True, frozen=True)
class _IndexedPaper:
    library_item_id: str
    paper_id: str
    user_id: str
    vector: tuple[float, ...]


class InMemorySemanticIndex:
    """seed 済みベクトルに対する純 Python ANN(``rank_by_similarity`` 相当)。

    行は ``(library_item_id, paper_id, user_id, vector)``。``query_neighbors`` /
    ``paper_neighbors`` はどちらも ``user_id`` で絞るため、他ユーザーの行は決して返らない。
    テスト専用(ネットワーク・pgvector 非依存)。
    """

    def __init__(self, rows: list[_IndexedPaper] | None = None) -> None:
        self._rows: list[_IndexedPaper] = list(rows or [])

    def add(self, *, library_item_id: str, paper_id: str, user_id: str,
            vector: list[float]) -> None:
        self._rows.append(
            _IndexedPaper(library_item_id, paper_id, user_id, tuple(float(x) for x in vector))
        )

    def _rank(
        self, query: tuple[float, ...] | list[float], rows: list[_IndexedPaper], top_k: int
    ) -> list[SemanticNeighbor]:
        scored = [
            SemanticNeighbor(r.library_item_id, cosine_similarity(query, r.vector)) for r in rows
        ]
        # 類似度降順・同点は library_item_id 昇順(決定的)。
        scored.sort(key=lambda n: (-n.similarity, n.library_item_id))
        return scored[:top_k]

    async def query_neighbors(
        self, *, query_vector: list[float], user_id: str, top_k: int, model: str
    ) -> list[SemanticNeighbor]:
        mine = [r for r in self._rows if r.user_id == user_id]
        return self._rank(query_vector, mine, top_k)

    async def paper_neighbors(
        self,
        *,
        paper_id: str,
        user_id: str,
        top_k: int,
        model: str,
        exclude_library_item_id: str,
    ) -> list[SemanticNeighbor] | None:
        seed = next((r for r in self._rows if r.paper_id == paper_id), None)
        if seed is None:
            return None
        mine = [
            r
            for r in self._rows
            if r.user_id == user_id and r.library_item_id != exclude_library_item_id
        ]
        return self._rank(seed.vector, mine, top_k)


# --------------------------------------------------------------------------- #
# クエリ埋め込み(BYOK → 運営キー解決 + プロバイダ呼び出し)
# --------------------------------------------------------------------------- #
# api_key → EmbeddingProvider。既定は実 OpenAI 実装。テストは Fake を注入する。
EmbeddingProviderFactory = Callable[[str], EmbeddingProvider]

# session → SemanticIndex。既定は pgvector 実装。テストは in-memory を注入する。
SemanticIndexFactory = Callable[[AsyncSession], SemanticIndex]


def default_embedding_provider_factory() -> EmbeddingProviderFactory:
    return lambda api_key: OpenAIEmbeddingProvider(api_key)


def default_semantic_index_factory() -> SemanticIndexFactory:
    return PgVectorSemanticIndex


async def embed_query(
    db: AsyncSession,
    settings: ApiSettings,
    user_id: str,
    query: str,
    *,
    provider_factory: EmbeddingProviderFactory,
    model: str = DEFAULT_EMBEDDING_MODEL,
    dim: int = DEFAULT_EMBEDDING_DIM,
) -> list[float] | None:
    """クエリを 1 ベクトル化する。キー未解決・プロバイダ失敗は ``None``(= 縮退)。"""
    key_store = DbKeyStore(db, settings)
    resolved = await key_store.resolve_or_none(user_id, EMBEDDING_PROVIDER_NAME)
    if resolved is None:
        return None  # BYOK も運営キーも無い → セマンティックを使わない。
    provider = provider_factory(resolved.api_key)
    try:
        result = await provider.embed(
            EmbeddingRequest(model=model, inputs=[query], dimensions=dim)
        )
    except ProviderError:
        return None  # プロバイダ落ち → PGroonga のみへ縮退(P3)。
    if not result.vectors:
        return None
    return result.vectors[0]


async def semantic_item_order(
    db: AsyncSession,
    settings: ApiSettings,
    user_id: str,
    query: str,
    *,
    provider_factory: EmbeddingProviderFactory,
    index_factory: SemanticIndexFactory,
    top_k: int = SEMANTIC_TOP_K,
    model: str = DEFAULT_EMBEDDING_MODEL,
) -> list[SemanticNeighbor] | None:
    """横断検索の semantic 側ランク(library_item 粒度)。

    フラグ off / キー未解決 / プロバイダ失敗のときは ``None`` を返し、呼び出し側は現行の
    PGroonga のみの挙動に落ちる(flag-off byte-identical)。空 index は ``[]``。
    """
    if not settings.semantic_search_enabled:
        return None
    vector = await embed_query(
        db, settings, user_id, query, provider_factory=provider_factory, model=model
    )
    if vector is None:
        return None
    index = index_factory(db)
    return await index.query_neighbors(
        query_vector=vector, user_id=user_id, top_k=top_k, model=model
    )


def match_type_for(
    library_item_id: str, lexical_ids: set[str], semantic_ids: set[str]
) -> str:
    """一致種別(spec §6.2 / plans/09 4e: 全文=lexical / 意味=semantic / 両方=both)。"""
    in_lex = library_item_id in lexical_ids
    in_sem = library_item_id in semantic_ids
    if in_lex and in_sem:
        return "both"
    if in_sem:
        return "semantic"
    return "lexical"


__all__ = [
    "EMBEDDING_PROVIDER_NAME",
    "SEMANTIC_TOP_K",
    "SIMILAR_TOP_K",
    "EmbeddingProviderFactory",
    "InMemorySemanticIndex",
    "PgVectorSemanticIndex",
    "SemanticIndex",
    "SemanticIndexFactory",
    "SemanticNeighbor",
    "default_embedding_provider_factory",
    "default_semantic_index_factory",
    "embed_query",
    "match_type_for",
    "semantic_item_order",
]
