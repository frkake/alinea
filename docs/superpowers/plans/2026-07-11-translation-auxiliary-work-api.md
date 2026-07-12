# Translation Auxiliary Work API Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make auxiliary translation work validated, persistent, retryable, concurrent-safe, and atomic with job creation.

**Architecture:** The translations router owns document/work validation, translation-set row locking,
plan mutation, and work reuse/generation selection. `JobStore` gains a separate flush-only enqueue
method so callers can commit plan and jobs together without changing existing enqueue behavior.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy async, PostgreSQL JSONB, Pydantic, pytest.

---

### Task 1: Add flush-only JobStore enqueue

**Files:**
- Modify: `packages/py-core/src/alinea_core/jobs/store.py`
- Test: `apps/api/tests/test_jobs.py`

- [ ] **Step 1: Write failing transaction tests**

Add tests that call `enqueue_uncommitted`, verify the job is visible inside the transaction but not
committed by the method, and verify a caller rollback removes it. Preserve the existing test proving
`enqueue` commits and reuses an idempotency key.

- [ ] **Step 2: Run RED**

Run: `uv run pytest apps/api/tests/test_jobs.py -q`

Expected: failure because `enqueue_uncommitted` does not exist.

- [ ] **Step 3: Implement the separate method**

Factor job construction and pre-existing idempotency lookup into `enqueue_uncommitted`. It must call
`flush()` only and must not catch/rollback an unexpected `IntegrityError`. Keep `enqueue` as the
commit/rollback wrapper around the new method.

- [ ] **Step 4: Run GREEN**

Run: `uv run pytest apps/api/tests/test_jobs.py -q`

Expected: all JobStore tests pass.

### Task 2: Fail closed on ambiguous documents

**Files:**
- Modify: `apps/api/src/alinea_api/routers/translations.py`
- Create: `apps/api/tests/test_translation_auxiliary_work.py`

- [ ] **Step 1: Seed duplicate section and block IDs in API tests**

Exercise list sets, list units, literal start, section translate, retry failed, and save position.
Assert each returns `422` with code `validation_error` and creates no job or position mutation.

- [ ] **Step 2: Run RED**

Run the new test file and confirm the current router either accepts the first duplicate or errors
non-deterministically.

- [ ] **Step 3: Centralize validated content loading**

Make `_as_content` validate `DocumentContent` and call `compute_translation_scope`. Convert
`ValueError`/Pydantic validation failures into one stable `ProblemException("validation_error")`.
Use this helper at every translations-router document-content access.

- [ ] **Step 4: Run GREEN**

Run the duplicate/invalid document tests and existing translation API tests.

### Task 3: Validate and persist section/table auxiliary work

**Files:**
- Modify: `apps/api/src/alinea_api/routers/translations.py`
- Test: `apps/api/tests/test_translation_auxiliary_work.py`

- [ ] **Step 1: Write section-work RED tests**

Cover full-section direct translatable blocks, one table block, unknown/cross-section/non-table/
reference/empty rejection, table/full distinct identities, canonical auxiliary ordering, and unchanged
primary progress/status.

- [ ] **Step 2: Run RED**

Expected failures: invalid block requests are accepted, empty jobs are created, and plans remain
unchanged.

- [ ] **Step 3: Implement work validation and monotonic auxiliary merge**

Lock the set row, resolve the stored and effective plans with the new core APIs, validate against the
full core scope, and write only canonical missing auxiliary IDs. Exclude valid inherited base work
from a personal plan's own auxiliary list.

- [ ] **Step 4: Run GREEN**

Run the focused API tests and assert exact plan JSON includes `auxiliary_block_ids`.

### Task 4: Add work reuse and generation scheduling

**Files:**
- Modify: `apps/api/src/alinea_api/routers/translations.py`
- Test: `apps/api/tests/test_translation_auxiliary_work.py`

- [ ] **Step 1: Write lifecycle RED tests**

Cover active reuse, succeeded/displayable reuse, succeeded/incomplete regeneration, failed and
canceled regeneration through at least generation 2, legacy exact-job reuse, and legacy mismatch
non-reuse. Assert payload `request_key` and `generation`.

- [ ] **Step 2: Run RED**

Expected: permanent static keys return terminal failed/incomplete jobs.

- [ ] **Step 3: Implement exact matching and generation selection**

Build stable request keys from set/section/work-kind/canonical hash. Inspect set-scoped translation
jobs, match new or exact legacy payloads, resolve effective units, exclude `BLOCKING_FLAGS`, and
select reuse or `max(generation)+1` before `enqueue_uncommitted`.

- [ ] **Step 4: Run GREEN**

Run lifecycle tests and existing on-demand idempotency tests without editing viewer tests.

### Task 5: Make plan and jobs atomic under concurrency

**Files:**
- Modify: `apps/api/src/alinea_api/routers/translations.py`
- Test: `apps/api/tests/test_translation_auxiliary_work.py`

- [ ] **Step 1: Write rollback and concurrent RED tests**

Monkeypatch flush-only enqueue to fail and assert neither plan nor job persists. Send two concurrent
requests using separate API sessions and assert one canonical auxiliary addition and one work job.

- [ ] **Step 2: Run RED**

Expected: current plan commits independently or duplicate work appears.

- [ ] **Step 3: Commit once after all uncommitted jobs**

Wrap scheduling in explicit try/rollback, commit plan plus jobs once, and perform wakeups only after
commit. Reuse the translation-set row lock as the concurrency serialization boundary.

- [ ] **Step 4: Run GREEN**

Run rollback/concurrency tests repeatedly to detect race instability.

### Task 6: Extend retry to effective auxiliary and legacy work

**Files:**
- Modify: `apps/api/src/alinea_api/routers/translations.py`
- Test: `apps/api/tests/test_translation_auxiliary_work.py`
- Test: `apps/api/tests/test_retranslate.py`

- [ ] **Step 1: Write retry RED tests**

Cover primary plus auxiliary blocking units, valid shared-base overlay units, legacy plan-external
blocking unit backfill, section membership/type revalidation, multiple section atomic jobs, and
failed/incomplete retry generations.

- [ ] **Step 2: Run RED**

Expected: auxiliary/base/legacy units are skipped and static retry keys cannot retry twice.

- [ ] **Step 3: Implement effective retry selection**

Use `resolve_effective_translation_plan`, `translation_execution_scope_from_plan`, and
`resolve_translation_set_units`. Revalidate legacy units against the full scope and actual section,
backfill only valid missing IDs, schedule section jobs uncommitted, and commit once.

- [ ] **Step 4: Run GREEN**

Run focused retry tests and existing retranslate tests.

### Task 7: Update literal scheduling and compatibility assertions

**Files:**
- Modify: `apps/api/src/alinea_api/routers/translations.py`
- Test: `apps/api/tests/test_literal_style.py`
- Test: `apps/worker/tests/test_ingest.py`
- Test: `apps/worker/tests/test_translate_units.py`

- [ ] **Step 1: Add literal terminal-incomplete RED tests**

Preserve same active/displayable job reuse, then mark work terminal incomplete and assert a new
generation. Assert shared-plan expansion plus all jobs commit atomically.

- [ ] **Step 2: Run RED**

Expected: the static literal key returns the terminal job.

- [ ] **Step 3: Route literal jobs through the scheduler**

Keep primary plan merge/status rules, schedule all section jobs with generation identities using
flush-only enqueue, then commit once. Keep empty-primary status `complete` and response `200`.

- [ ] **Step 4: Update exact JSON assertions**

Add `auxiliary_block_ids: []` to legacy-compatible plan assertions while keeping initial worker
`block_ids`, progress, and finalization primary-only.

- [ ] **Step 5: Run GREEN**

Run literal and worker translation-focused tests.

### Task 8: Verify the integration

**Files:** all files above.

- [ ] **Step 1: Focused tests**

Run JobStore, auxiliary-work, literal, retranslate, and worker translation tests.

- [ ] **Step 2: Broad tests**

Run complete py-core, API, and worker suites after concurrent Core work is stable.

- [ ] **Step 3: Static and diff audits**

Run Ruff format/check, mypy on changed source files, `git diff --check`, and search changed production
files for test paper IDs, titles, section IDs, or block IDs.

- [ ] **Step 4: Report without committing**

Report exact commands and counts. Do not create a commit.
