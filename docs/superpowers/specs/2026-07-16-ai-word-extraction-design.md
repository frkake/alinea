# AI Word Extraction (AI単語抽出) — Feature S7 Design

## Goal

Automatically propose important/difficult vocabulary candidates from a paper and present them to
the user for one-tap adding to the vocabulary notebook (語彙帳). Today the only path into the
notebook is the **manual** "語彙に追加" selection flow in the viewer
(`use-annotation-selection.tsx` → `POST /api/vocab` → `generate_vocab_ai` worker). S7 adds an
**AI pass over the paper's document content** that surfaces candidates the user reviews and
accepts. Accepting a candidate creates a real `VocabEntry` through the **existing** create + AI
field-generation flow, so all downstream behaviour (9-field generation, SRS, export) is unchanged.

This is the 語彙帳 subsystem (docs/11), **not** the translation glossary (`glossary_terms`,
`papers.extracted_terms`, docs/03 §1). The two stay strictly separate (docs/11 §1). S7 never
touches `glossary_terms`.

## Design decisions (recommended) — with the one flagged for the user

1. **Trigger: on-demand, not automatic post-ingest.** Extraction runs when the user asks for it
   (a "AI候補を抽出" action from the viewer / vocab UI), not silently at ingest. Rationale mirrors
   docs/12 §5 (official-repo suggestion): the product principle is *propose, do not auto-add* (P6).
   On-demand also avoids spending an LLM call on papers the user never studies for vocab, and keeps
   ingest latency unchanged.
   - **⚑ FLAG FOR USER**: on-demand vs. automatic-at-ingest is a genuine product choice. This spec
     and implementation take **on-demand**. If automatic post-ingest is preferred, the same job can
     be enqueued from the ingest pipeline instead of an endpoint — the storage model and worker are
     identical either way.

2. **Storage: a lightweight `vocab_candidates` table (not transient, not reusing an existing
   table).** Candidates must survive across page loads and be dismissable/acceptable idempotently,
   and must not be re-proposed once dismissed — a transient/derive-on-read model (as resources uses
   for its single suggestion) cannot express per-term dismissal at scale. A dedicated table also
   keeps candidates cleanly separate from committed `vocab_entries` (a candidate is a proposal, not
   a notebook entry). This is a small table modelled on the `vocab_entries` context columns.

3. **Number of candidates: bounded, default max 20 per extraction.** Enough to be useful, small
   enough to review in one pass (P2). The LLM prompt requests up to `MAX_CANDIDATES`; the worker
   additionally truncates after validation/dedup.

4. **LLM routing: reuse the existing `vocab` use-case (task string `"vocab"`).** No new DB route
   seeding, no `bootstrap.TEXT_ROUTER_TASKS` change, and it reuses the small/cheap model tier
   docs/11 §8 already mandates for vocab. Only the **structured-output schema** is new
   (`vocab_candidates_v1`), following the `generate_vocab_ai.py` schema pattern. Tests inject the
   candidate list via `FakeLLMProvider(structured={...})`; a default is also added to
   `_DEFAULT_STRUCTURED` so the `ALINEA_FAKE_LLM=1` dev/E2E path never crashes with
   `SCHEMA_VALIDATION`.

## Data model

New table `vocab_candidates` (per library item; owned by the item's user):

| column | type | notes |
|---|---|---|
| `id` | UUID pk | `gen_random_uuid()` |
| `user_id` | UUID FK users CASCADE | owner (redundant with item.user_id, kept for query scoping) |
| `library_item_id` | UUID FK library_items CASCADE | the paper being read |
| `term` | TEXT | proposed headword / collocation / idiom |
| `kind` | TEXT default `word` | CHECK `word|collocation|idiom` (docs/11 §3) |
| `context_anchor` | JSONB | `{revision_id, block_id, start, end, quote, side}` (common anchor) |
| `context_sentence` | TEXT | sentence containing the term (server-derived, not LLM-trusted) |
| `context_hl_start` / `context_hl_end` | INT | term offsets within `context_sentence` |
| `reason` | TEXT default `''` | short why-it-matters note from the model (optional, display-only) |
| `status` | TEXT default `pending` | CHECK `pending|accepted|dismissed` |
| `vocab_entry_id` | UUID FK vocab_entries SET NULL | set when accepted (idempotent accept) |
| `created_at` / `updated_at` | TIMESTAMPTZ | `set_updated_at()` trigger, as all tables |

Indexes / constraints:
- `uq_vocab_candidates_item_term` UNIQUE `(library_item_id, lower(term))` — idempotent
  re-extraction and idempotent dismiss/accept; a term proposed once is never duplicated for the
  same paper.
- `ix_vocab_candidates_item_status` `(library_item_id, status)` — list query.

Migration `0010_vocab_candidates` chains from `0009_user_scoped_ingest` (this branch's head). It
also extends `ck_jobs_kind` to allow the new `vocab_extract` job kind. Migration authored in the
raw-`op.execute` style of `0003`/`0008`/`0009`.

> Cross-branch note: a sibling branch has an unrelated `0010_import_job_kind` (adds `import`). Both
> legitimately branch off `0009`; this is resolved at merge time. On the shared dev DB the
> `ck_jobs_kind` set is applied as a superset so neither branch's job kind is rejected.

## Extraction job (worker) — `kind='vocab_extract'`

New handler `run_extract_vocab_candidates(ctx, store, job)` in
`apps/worker/src/alinea_worker/tasks/extract_vocab_candidates.py`, registered
`HANDLERS["vocab_extract"] = ...` and routed to `INTERACTIVE_QUEUE` (like `vocab`). Payload:
`{"library_item_id": "<uuid>"}`.

Pipeline:
1. Load `LibraryItem → Paper → latest DocumentRevision`; parse `DocumentContent`. Missing revision
   → `store.succeed({"candidates_created": 0, "skipped": "no_revision"})` (best-effort, P3).
2. Render a bounded plaintext of paragraph-like blocks as `[block_id] text` lines (reusing
   `alinea_core.document.plaintext.block_to_plain`; capped by `MAX_CONTEXT_CHARS`). Worker cannot
   import apps/api, so this small renderer lives in the task (same policy as `fetch_resource_meta`).
3. `router.complete("vocab", request=..., mode="structured")` with schema `vocab_candidates_v1`:
   `{"candidates":[{"term","kind","block_id","reason?"}]}` (maxItems bounded). On
   `ProviderChainExhausted`: mark the job failed with a friendly message (P3), create nothing.
4. **Validate fail-closed** each proposed candidate: `block_id` must exist in the document; `term`
   must actually occur (case-insensitive) in that block's text; `kind` in the enum. Derive
   `context_sentence` + highlight offsets server-side from the block text (never trust LLM offsets).
   Invalid candidates are dropped, not guessed.
5. **Dedup**: skip a term already in the user's `vocab_entries` (normalized `lower(trim)`,
   user-wide — the notebook is one book, docs/11 §1) and skip a term already present in
   `vocab_candidates` for this item (any status — so dismissed terms are never re-proposed).
6. Insert up to `MAX_CANDIDATES` `vocab_candidates` rows (status `pending`). Per-row
   `IntegrityError` on the unique index is swallowed (idempotent concurrent runs).
7. `store.succeed({"candidates_created": n})`.

## API (apps/api) — router `vocab_candidates.py`

All under session auth + ownership checks, mirroring `vocab.py` / `resources.py`.

| Method / path | operation_id | behaviour |
|---|---|---|
| `POST /api/library-items/{item_id}/vocab-candidates/extract` | `vocab_candidates_extract` | `check_quota(task="vocab")`; if an active (`queued`/`running`) `vocab_extract` job exists for this item, return it (202); else enqueue + wakeup. Returns `{job_id}`. |
| `GET /api/library-items/{item_id}/vocab-candidates` | `vocab_candidates_list` | Returns `pending` candidates (with server-derived source `display`) + counts. |
| `POST /api/vocab-candidates/{candidate_id}/accept` | `vocab_candidates_accept` | Creates a real `VocabEntry` from the candidate's stored term/anchor/context, enqueues the existing `kind='vocab'` generation job, marks candidate `accepted` + links `vocab_entry_id`. Idempotent: an already-accepted candidate returns its linked entry; if the term was meanwhile saved manually, returns that existing entry. |
| `POST /api/vocab-candidates/{candidate_id}/dismiss` | `vocab_candidates_dismiss` | Marks `dismissed` (idempotent). The row stays so the term is never re-proposed. |

Accept reuses the vocab create building blocks (`_find_duplicate`, `today_jst`, the `VocabEntry`
constructor shape, `JobStore.enqueue(kind="vocab")`, the vocab job wakeup dep) so the generated
entry is identical to a manually added one and `generate_vocab_ai` fills all 9 fields.

Wakeup + job enqueue follow `vocab.py` exactly (`get_vocab_job_wakeup`, interactive queue). The
router is registered in `apps/api/src/alinea_api/main.py`.

## Frontend (minimal / follow-up)

Backend is the unambiguous core and is delivered fully with TDD. A minimal viewer-side panel
(`VocabExtractPanel`) that calls extract → polls list → accept/dismiss is the intended UI, but it
depends on regenerating `@alinea/api-client` (openapi-ts) and wiring a new side-panel tab; per the
time-box this UI is a follow-up. The endpoints and DTOs are shaped so the panel is a thin client
(list pending, one-tap accept/dismiss, "候補を抽出" button), consistent with the resources tab's
suggestion card (docs/12 §6) and the vocab detail screen (docs/11 §5).

## Verification (TDD)

Worker (`apps/worker/tests/test_extract_vocab_candidates.py`), fake router only:
- happy path: valid candidates → `vocab_candidates` rows created, job succeeded with count.
- fail-closed: nonexistent `block_id`, term-not-in-block, bad kind → dropped.
- dedup: term already in `vocab_entries` (user-wide) and term already a candidate (incl. dismissed)
  → not re-created; idempotent second run creates nothing new.
- `MAX_CANDIDATES` truncation.
- `ProviderChainExhausted` → job `failed`, no rows created.

API (`apps/api/tests/test_vocab_candidates.py`), single-router app like `test_vocab.py`:
- extract enqueues a `vocab_extract` job (wakeup captured); re-extract returns the active job.
- list returns pending candidates with source display; dismissed/accepted excluded.
- accept creates a `VocabEntry` (+ vocab generation job enqueued), marks candidate accepted, links
  the entry; second accept is idempotent.
- accept when term already saved manually → returns existing entry, no duplicate.
- dismiss marks dismissed (idempotent) and it drops out of the list.
- ownership: other users get 404.

All tests use the deterministic `FakeLLMProvider` / injected router — never a live LLM.
