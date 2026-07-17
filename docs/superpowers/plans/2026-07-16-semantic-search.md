# Semantic Search (S12 / M3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:test-driven-development for each code task (write the failing test first, then the implementation). Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add semantic (embedding-based) search to the cross-corpus search — similar-paper discovery + cross-language semantic queries — blended with the existing PGroonga lexical search, without regressing the current full-text behavior.

**Design doc:** `docs/superpowers/specs/2026-07-16-semantic-search-design.md` (read it first; §3 lists the infra decisions that gate Phases B+).

**Architecture:** Semantic search is **additive**. Query → (a) existing PGroonga CTE for lexical hits, (b) query embedding → pgvector ANN for semantic hits, (c) Reciprocal Rank Fusion (RRF, a pure function in `packages/py-core`) to blend the two ranked lists, (d) existing grouping/sort/paginate. Embeddings are produced by a new `EmbeddingProvider` abstraction (separate Protocol, mirroring `ImageProvider`) and stored in pgvector tables populated by a worker indexing job. A feature flag (`semantic_search_enabled`, default off) keeps `/api/search` byte-identical until the infra is in place.

**Tech Stack:** Python 3.12, uv workspace. `packages/llm` (provider abstraction, pydantic types), `packages/py-core` (pure search logic + SQLAlchemy models + Alembic-adjacent), `apps/api` (FastAPI search router), `apps/worker` (arq jobs). Tests: `pytest` + `pytest-asyncio`; real PostgreSQL for DB tests (SQLite forbidden — PGroonga/pgvector dependency), deterministic fakes for LLM.

## Global Constraints

- Run tests with `uv run pytest apps/api packages -q` from repo root (uv workspace). LLM-only slice: `uv run pytest packages/llm -q`.
- **No silent new dependencies and no silent DB extensions.** Every item in Phases B+ that adds a Python dep, a Docker image change, or a `CREATE EXTENSION` is gated on explicit user approval of the corresponding decision in the design doc §3.
- Tests MUST be deterministic. LLM embeddings in tests use `FakeEmbeddingProvider` only — never real network calls. DB tests use the real Postgres from docker-compose.
- **apps-cross-import is forbidden** (see `apps/worker/bootstrap.py` header): the worker reads LLM routes via raw SQL, not `apps/api` code. Keep this when wiring the indexing job.
- Follow existing patterns: provider abstraction mirrors `ImageProvider`/`FakeImageProvider`; pure search helpers live in `alinea_core.search` beside `pgroonga_query.py`; migrations are hand-written SQL via `op.execute` (see `0002_llm_routing_tables.py`); the search router keeps its `_HitRow`/`_Group` pipeline.
- Feature flag OFF = zero behavior change. This is the acceptance gate for Phase A.

---

## Phase A — Safe foundation (NO new deps, NO DB extension, flag OFF)

This phase is fully implementable now and is what the first PR delivers. It changes no search behavior.

### Task A1: Embedding abstraction + deterministic fake (`packages/llm`)

**Files:**
- Modify `packages/llm/src/alinea_llm/types.py` — add `EmbeddingRequest`, `EmbeddingResult`.
- Modify `packages/llm/src/alinea_llm/protocols.py` — add `EmbeddingProvider` (runtime_checkable Protocol with `name: str` and `async def embed(req) -> EmbeddingResult`).
- Modify `packages/llm/src/alinea_llm/testing/fake_provider.py` — add `FakeEmbeddingProvider`.
- Modify `packages/llm/src/alinea_llm/__init__.py` — re-export the three new names.
- Create `packages/llm/tests/test_embeddings.py`.

**Interfaces:**
```python
class EmbeddingRequest(BaseModel):
    model: str
    inputs: list[str]                # batch
    dimensions: int | None = None    # optional truncation (OpenAI-style)
    metadata: dict[str, str] = {}

class EmbeddingResult(BaseModel):
    vectors: list[list[float]]       # len == len(inputs), each len == dim
    model: str = ""
    provider: str = ""
    dim: int = 0
    usage: Usage = Usage()           # reuse existing Usage
    request_id: str | None = None

@runtime_checkable
class EmbeddingProvider(Protocol):
    name: str
    async def embed(self, req: EmbeddingRequest) -> EmbeddingResult: ...
```

`FakeEmbeddingProvider` (deterministic, offline):
- `__init__(self, *, dim: int = 8, name: str = "fake", fail: bool = False, error_kind=ErrorKind.MODEL_NOT_FOUND)`.
- For each input string: lowercase, split on non-alphanumerics into tokens; for each token, hash with `hashlib.sha256(token)` → seed a bag-of-words vector by adding `+1` at `int(hash) % dim` (sign from an extra hash bit). Then **L2-normalize**. Empty string → zero vector (documented degenerate case). This makes shared tokens raise cosine similarity deterministically and is stable across processes (unlike Python's salted `hash()`).
- `fail=True` raises `ProviderError` (mirror `FakeLLMProvider`).
- `usage` = deterministic token count via existing `_tokens(chars)` helper.

- [ ] **Step 1: Write failing tests** (`test_embeddings.py`):
  - determinism: two `embed` calls with same inputs → identical vectors.
  - shape: `len(vectors) == len(inputs)`, each `len == dim`; `result.dim == dim`.
  - L2 norm: non-empty input vector norm ≈ 1.0 (abs tol 1e-6); empty input → all-zeros.
  - semantic-ish sanity: cosine(("rectified flow model"), ("rectified flow method")) > cosine(("rectified flow"), ("banana bread recipe")) — shared tokens ⇒ higher similarity.
  - `isinstance(FakeEmbeddingProvider(), EmbeddingProvider)` is True (runtime_checkable).
  - `fail=True` raises `ProviderError`.
- [ ] **Step 2: Implement** the types, protocol, fake, and re-exports until green.
- [ ] **Step 3:** `uv run pytest packages/llm -q` green.

### Task A2: Pure fusion + similarity helpers (`packages/py-core`)

**Files:**
- Create `packages/py-core/src/alinea_core/search/fusion.py`.
- Modify `packages/py-core/src/alinea_core/search/__init__.py` — re-export.
- Create `packages/py-core/tests/test_fusion.py`.

**Interfaces:**
```python
def reciprocal_rank_fusion(
    ranked_lists: Sequence[Sequence[str]],   # each = ids in descending relevance
    *, k: int = 60, weights: Sequence[float] | None = None,
) -> list[tuple[str, float]]:                # (id, fused_score) desc, ties by id asc

def blend_lexical_semantic(
    lexical_ids: Sequence[str], semantic_ids: Sequence[str],
    *, k: int = 60, w_lexical: float = 1.0, w_semantic: float = 1.0,
) -> list[str]:                              # convenience wrapper → fused id order

def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float

def rank_by_similarity(
    query: Sequence[float], candidates: Mapping[str, Sequence[float]], *, top_k: int | None = None,
) -> list[tuple[str, float]]                  # (id, cosine) desc, ties by id asc
```
- RRF uses rank position only (scale-independent); `weights` default to all-1.0; length must match `ranked_lists` or raise `ValueError`.
- Deterministic tie-break: fused score desc, then id asc.
- `cosine_similarity` of a zero vector → 0.0 (no ZeroDivision).

- [ ] **Step 1: Write failing tests** (`test_fusion.py`, no DB, no network):
  - RRF hand-computed: `rrf([["a","b","c"],["b","c","a"]], k=60)` → `b` first (appears rank2+rank1), verify exact scores `1/61+1/62` etc.
  - scale-independence: fusion order is unchanged whether inputs are ranks derived from tiny or huge raw scores (RRF ignores magnitude by construction — assert order equals a lexical-only fallback shape for a known case).
  - weights: `w_semantic=0` ⇒ order == lexical order; `w_lexical=0` ⇒ order == semantic order.
  - ties broken by id ascending.
  - `blend_lexical_semantic` with one empty list returns the other's order.
  - both empty ⇒ `[]`.
  - `cosine_similarity` known values: identical unit vectors → 1.0; orthogonal → 0.0; zero vector → 0.0.
  - `rank_by_similarity` orders by cosine desc, respects `top_k`, ties by id asc.
- [ ] **Step 2: Implement** `fusion.py` + re-export until green.
- [ ] **Step 3:** `uv run pytest packages/py-core/tests/test_fusion.py -q` green.

### Task A3: Feature flag receptacle (`packages/py-core` settings)

**Files:**
- Modify `packages/py-core/src/alinea_core/settings.py` — add `semantic_search_enabled: bool = False` to `CoreSettings` (env `SEMANTIC_SEARCH_ENABLED`). It is inherited by `ApiSettings`.
- Add one test asserting the default is `False` (e.g. in an existing settings/units test, or a tiny new one) — deterministic, no DB.

- [ ] **Step 1:** add the field with a docstring noting it gates Phase B wiring; default off.
- [ ] **Step 2:** assert default False.
- [ ] **Step 3:** `uv run pytest apps/api packages -q` green (full regression; flag off ⇒ no behavior change).

**Phase A acceptance:** all tests green; `/api/search` behavior byte-identical (no router change); no new Python deps; no DB/Docker change.

---

## Phase B — Infra (GATED on user approval; do NOT start silently)

Each task below names the design-doc decision it depends on. Do not begin until the user approves that decision.

### Task B1: pgvector in the DB image + init (gate: design §3 D2)
- New `docker/db/Dockerfile` FROM `groonga/pgroonga:4.0.1-debian-16`, apt-install `postgresql-16-pgvector`. Point `docker-compose.yml` `db.build` at it (keep the debian base so PGroonga `stem.so`/`mecab.so` survive).
- Add `CREATE EXTENSION IF NOT EXISTS vector;` to `docker/db/init.sql`.
- Verify: `docker compose build db && docker compose up -d db`, then `SELECT extname FROM pg_extension` shows both `pgroonga` and `vector`.

### Task B2: Alembic — extension + embedding tables (gate: §3 D2, D3)
- `apps/api/alembic/versions/0010_semantic_search.py` (hand-written SQL, mirror `0002` style): `CREATE EXTENSION IF NOT EXISTS vector;` + `paper_embeddings` (Phase B first) with HNSW index (design §5). Add `block_embeddings` in a later migration when D3 phase-2 is approved.
- Add SQLAlchemy models for the new tables in `packages/py-core/db/models.py` (embedding column as a custom type or raw `pgvector.sqlalchemy.Vector` — note: that adds the `pgvector` Python dep, itself gated).

### Task B3: Real `EmbeddingProvider` implementation (gate: §3 D1)
- `packages/llm/src/alinea_llm/providers/embeddings/openai_embedding.py` (+ Google optional) using the already-present `openai`/`google-genai` SDKs (no new dep). Add `build_embedding_provider` factory mirroring `build_image_provider`.
- Register embedding model(s) in `models.yaml` and an `embedding` task in `routing.yaml`; extend the `llm_task_routes.task` CHECK constraint (migration) — note fallback must stay within one model/dim family (design §3 D1, §6.4).

### Task B4: Worker indexing job (gate: B1–B3)
- Paper-grain embed job hooked where `rebuild_block_search_index` runs (ingest/translate pipeline) + a full backfill entry (mirror `latex_pdf_backfill.py`). Upsert into `paper_embeddings`, skip when `source_hash` unchanged. Worker uses operator keys via raw SQL route resolution (no apps/api import).

### Task B5: Query-path wiring in `search.py` (gate: B1–B4)
- Behind `semantic_search_enabled`: after `_fetch_all_hits`, embed the query, run pgvector ANN scoped to the user's `library_items`, map both to `library_item_id`-ranked lists, `blend_lexical_semantic`, and feed the fused order into grouping. Embedding failure ⇒ swallow and fall back to lexical (P3). Add API tests with `FakeEmbeddingProvider` injected and a seeded `paper_embeddings` fixture.

### Task B6: "Similar papers" endpoint (gate: B1–B5)
- `GET /api/library-items/{id}/similar` — ANN from the target paper's stored vector, scoped to the user's library, pure-semantic (no lexical blend).

---

## Test & Verification Summary
- Phase A: `uv run pytest apps/api packages -q` — all green, deterministic, flag off.
- Phase B: add DB-backed tests (real Postgres + pgvector) with `FakeEmbeddingProvider`; verify ANN scoping by user, RRF blend ordering, and graceful degradation when the store is empty or the embedder fails.

## Rollout / Risks
- Embedding model choice fixes the vector space; switching models requires a full reindex (design §6.4). Store `model`/`dim` per row and ignore mismatches at query time.
- Cost: start paper-grain only (design §3 D3); measure before enabling block-grain.
- Regression safety: the flag and the "empty semantic list ⇒ lexical-only via RRF" degradation keep the current search intact throughout.
