# Translation Plan Persistence Phase A Implementation Plan

> **For agentic workers:** Execute inline with strict RED/GREEN checkpoints. Do not commit; the task owner explicitly prohibited commits.

**Goal:** Persist one validated translation target plan per `TranslationSet` so Worker, API, Viewer, retries, and reading-session logic use identical targets, while fixing generic appendix/reference classification and translated TOC titles.

**Architecture:** Add a nullable JSONB `translation_sets.plan` column for backward compatibility. Core translation helpers build, validate, safely fall back to full scope, filter scopes, and monotonically merge plans in canonical document order. Every consumer resolves target IDs through those helpers; Viewer additionally maps each section's translated heading block from the effective translation set into `title_ja`.

**Tech Stack:** Python 3.12, Pydantic, SQLAlchemy 2, PostgreSQL JSONB, Alembic, FastAPI, pytest, React/TypeScript.

---

### Task 1: Generic classification and persisted plan contract

**Files:**
- Create: `apps/api/alembic/versions/0006_translation_set_plan.py`
- Modify: `packages/py-core/src/alinea_core/db/models.py`
- Modify: `packages/py-core/src/alinea_core/translation/pipeline.py`
- Modify: `packages/py-core/src/alinea_core/translation/__init__.py`
- Test: `packages/py-core/tests/test_translation.py`
- Test: `packages/py-core/tests/test_schema.py`

- [ ] Add failing tests for Roman main-section numbering; attached English/Japanese appendix headings; nested reference descendants; exclusive, complete, duplicate-free DFS IDs.
- [ ] Add failing tests for plan JSON shape, strict validation, missing/invalid full-scope fallback, canonical subset filtering, and monotonic subset-to-full merge without full-to-subset shrinkage.
- [ ] Add a failing ORM/schema test for nullable JSONB `TranslationSet.plan` and migration head `0006_translation_set_plan`.
- [ ] Run the focused tests and record expected failures caused by the absent contract and current classification.
- [ ] Implement the migration, ORM column, plan model/helpers, and generic classification changes.
- [ ] Run focused tests until green without paper IDs or titles in production logic.

### Task 2: Worker creation, reuse, progress, and finalization

**Files:**
- Modify: `apps/worker/src/alinea_worker/pipeline.py`
- Modify: `apps/worker/src/alinea_worker/tasks/translate.py`
- Modify: `packages/py-core/src/alinea_core/translation/pipeline.py`
- Modify: `packages/py-core/src/alinea_core/ingest/progress.py`
- Test: `apps/worker/tests/test_ingest.py`
- Test: `apps/worker/tests/test_translate_units.py`
- Test: `packages/py-core/tests/test_translation.py`
- Test: `packages/py-core/tests/test_core_units_coverage.py`

- [ ] Add failing default and explicit-opt-out tests proving the stored plan controls scheduling, `_refresh_set_status`, finalization, and reported progress.
- [ ] Add a failing public shared-set reuse test: subset expands to full, while an existing full plan never shrinks for an opt-out request.
- [ ] Build the requested plan before set creation, persist it for new sets, row-lock and monotonically merge reused sets, and derive readable/body jobs from the stored plan.
- [ ] Resolve `_refresh_set_status` and finalization target IDs from the stored plan with safe legacy fallback.
- [ ] Run focused Worker/core tests until green.

### Task 3: API and reading-session consumers

**Files:**
- Modify: `apps/api/src/alinea_api/routers/translations.py`
- Modify: `apps/api/src/alinea_api/routers/viewer.py`
- Modify: `apps/api/src/alinea_api/services/reading_sessions.py`
- Modify: `packages/py-core/src/alinea_core/translation/glossary.py`
- Modify: `apps/api/tests/factories.py`
- Test: `apps/api/tests/test_viewer_api.py`
- Test: `apps/api/tests/test_retranslate.py`
- Test: `apps/api/tests/test_reading_sessions_service.py`
- Test: `packages/py-core/tests/test_glossary_crud.py`

- [ ] Add failing tests reproducing Worker 100% versus Viewer/list 50% for opt-out, plus full-scope and legacy-plan behavior.
- [ ] Add failing retry and reached-end tests proving excluded targets are not retried or counted.
- [ ] Resolve list/viewer/retry/read-end targets from the effective set plan; copy a shared plan when creating a new personal fork.
- [ ] Run focused API/core tests until green.

### Task 4: Effective translated TOC titles

**Files:**
- Modify: `apps/api/src/alinea_api/routers/viewer.py`
- Test: `apps/api/tests/test_viewer_api.py`

- [ ] Add failing tests for a translated section heading, missing heading, blocked heading, natural/literal style switching, and personal-over-shared effective resolution.
- [ ] Retain effective display units rather than only their IDs; map each section's own heading block translation to `TocNode.title_ja` while preserving `title_en`.
- [ ] Run Viewer tests until green.

### Task 5: Verification

**Files:** all files above.

- [ ] Run all translation tests and focused Worker/API/Web regressions.
- [ ] Run `uv run ruff check apps packages`.
- [ ] Run `uv run mypy packages/py-core/src apps/api/src apps/worker/src`.
- [ ] Run `pnpm --filter @alinea/web typecheck` and relevant Viewer tests.
- [ ] Run Alembic upgrade/downgrade/upgrade verification, `git diff --check`, and production hardcode scans.
- [ ] Review the diff against P1 #3/#4/#5 only; do not implement selection UI or table-cell behavior and do not commit.

### Re-review Task 6: Target-aware TOC state and safe heading-title mapping

**Files:**
- Modify: `apps/api/src/alinea_api/routers/viewer.py`
- Test: `apps/api/tests/test_viewer_api.py`

- [ ] Add a failing integration test proving a full-plan appendix has `on_demand=false` and an opt-out-plan appendix has `on_demand=true`.
- [ ] Add failing cases where a later heading block and a first-block heading whose normalized title differs from `Section.heading.title` must not populate `title_ja`.
- [ ] Derive `on_demand` from appendix membership minus persisted target sections; accept a heading translation only for the first block with an NFKC/casefold/whitespace-normalized matching title.
- [ ] Run focused Viewer tests until green.

### Re-review Task 7: Effective base-plus-personal translation units

**Files:**
- Modify: `packages/py-core/src/alinea_core/translation/pipeline.py`
- Modify: `packages/py-core/src/alinea_core/translation/__init__.py`
- Modify: `apps/api/src/alinea_api/routers/translations.py`
- Test: `packages/py-core/tests/test_translation.py`
- Test: `apps/api/tests/test_viewer_api.py`

- [ ] Add failing DB tests for base-only units, personal overrides, blocked overrides, and equal source hashes on different block IDs.
- [ ] Implement one core resolver that loads the exact `base_set_id` units and overlays personal units by block ID; make Viewer resolution, list progress, and `_refresh_set_status` share it.
- [ ] Add an integration assertion that personal list progress, Worker status/progress, and Viewer progress agree.
- [ ] Run focused core/API tests until green.

### Re-review Task 8: Persist and merge literal translation plans

**Files:**
- Modify: `apps/api/src/alinea_api/routers/translations.py`
- Test: `apps/api/tests/test_viewer_api.py`

- [ ] Add failing tests for a new literal set created under appendix opt-out, including revision pages and only planned targets in queued jobs.
- [ ] Add failing shared-reuse tests for subset-to-full expansion and full-to-opt-out non-shrinkage.
- [ ] Build the requested plan from user settings, row-lock reused sets, monotonically merge, mark an expanded complete set partial, and enqueue from `translation_scope_from_plan`.
- [ ] Run focused literal API tests until green.

### Re-review Task 9: Generic attached headings and duplicate-ID fail-closed guard

**Files:**
- Modify: `packages/py-core/src/alinea_core/translation/pipeline.py`
- Test: `packages/py-core/tests/test_translation.py`

- [ ] Add failing table-driven tests for `AppendixA`, `Appendix A`, `付録Ａ`, `付録一`, `附録B：証明`, and the `AppendixAnalysis` negative case while retaining valid Roman numerals 1..3999 as main.
- [ ] Add failing tests for duplicate section IDs and duplicate block IDs across the full tree in both scope and plan generation.
- [ ] Tighten generic heading recognition and raise stable `ValueError` messages before scope/plan construction when an ID repeats.
- [ ] Run focused core tests until green.

### Re-review Task 10: Repeat complete verification

**Files:** all Phase A files.

- [ ] Run focused RED/GREEN cases and the Phase A broad core/Worker/API/Web suites.
- [ ] Verify Alembic current/head and downgrade/upgrade.
- [ ] Run Ruff, mypy, Web typecheck, diff check, and paper-specific hardcode scans.
- [ ] Leave all changes uncommitted.

### Security Re-review Task 11: Enforce same-paper revision boundaries

**Files:**
- Create or modify: a shared API revision-resolution service
- Modify: Viewer, article, chat, resource, library, export, search, paper/PDF, and ingest routes
- Test: the corresponding API and Worker suites

- [ ] Reproduce cross-private-paper leakage through a corrupt `latest_revision_id` and a legacy foreign `reading_position.revision_id` before changing production code.
- [ ] Resolve current and reading-position revisions only when `DocumentRevision.paper_id == Paper.id`; invalid, missing, malformed, or foreign identifiers must fail closed without a 500.
- [ ] Apply the same invariant to SQL joins used by global search, so source and translated snippets can never cross a library item's paper boundary.
- [ ] Prove article and chat inputs never contain the foreign revision's text, resource validation cannot use foreign section IDs, and summaries cannot inherit foreign quality metadata.
- [ ] Run focused security reproductions and broad API/Worker regressions.

### Security Re-review Task 12: Validate personal base-set relationships

**Files:**
- Modify: `packages/py-core/src/alinea_core/translation/pipeline.py`
- Test: core translation and API unit-list suites

- [ ] Reproduce a personal set inheriting a different private revision/user's translation through a forged `base_set_id`.
- [ ] Load base units only when the base exists, is shared, and has the exact same revision and style; otherwise resolve only direct personal units.
- [ ] Cover cross-revision, cross-style, personal-as-base, missing-base, valid overlay, and API non-disclosure cases.

### Semantics Re-review Task 13: Separate initial and on-demand targets

**Files:**
- Modify: `packages/py-core/src/alinea_core/translation/pipeline.py`
- Modify: `apps/api/src/alinea_api/routers/translations.py`
- Modify: `apps/api/src/alinea_api/routers/viewer.py`
- Test: core translation and Viewer/API suites

- [ ] Add failing tests proving a completed on-demand appendix no longer remains actionable and a failed on-demand unit remains retryable, while the initial progress denominator does not grow.
- [ ] Extend the bounded persisted contract with canonical, disjoint auxiliary/on-demand block IDs; preserve existing versioned plans and keep `target_block_ids` as the progress denominator.
- [ ] Row-lock and monotonically merge auxiliary targets when an on-demand request is accepted; preserve them across shared reuse and personal forks.
- [ ] Use initial-plus-auxiliary targets for execution, retry eligibility, and TOC requested state, but initial targets only for progress and ingest completion.
- [ ] Include the section/block identity in idempotency keys and validate that a requested block belongs to the requested section and expected block type.
- [ ] Resolve a personal set's effective execution work from its own plan plus valid base-set work, without importing the base plan into the personal progress denominator.
- [ ] Keep this auxiliary list limited to ordinary block translation. Table-cell work needs a typed table-cell state because caption and cells share one block ID; long-paper selection needs a user/ingest checkpoint because a shared cache target cannot represent independent users' denominators.
- [ ] Make plan persistence and job creation one transaction under the set-row lock, and generate a new idempotency generation only after a prior request reached a terminal incomplete state.
- [ ] Validate explicit Worker payload IDs against a reason-specific allowed scope; missing or out-of-scope on-demand/retry payloads must call no model.

### Safety Re-review Task 14: Strict plans and fail-closed section dispatch

**Files:**
- Modify: core plan validation and API translation routes
- Test: core, API, and Worker translation suites

- [ ] Reject a mapping with no explicit `version`; it must take the documented legacy/invalid full-scope fallback rather than being accepted as v1.
- [ ] Enforce aggregate size, uniqueness, canonical order, and document membership across initial and auxiliary target IDs before Pydantic copies untrusted lists.
- [ ] Remove the `section_map.get(...) or all_section_blocks` fallback: an empty or non-target plan with an omitted `block_ids` payload must execute zero blocks.
- [ ] Make translation APIs validate revision-global section/block ID uniqueness before selecting a section, returning a stable client error instead of choosing the first duplicate.
- [ ] Cover versionless subsets, empty plans, non-target sections, duplicate section IDs, and duplicate block IDs end to end.
