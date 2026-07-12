# Long-paper section selection implementation plan

## Task 1: Core selection contract

- Add failing tests for the strict >30-page gate, pending v1 plan, canonical subset construction,
  nested sections, appendix exclusion, duplicate/unknown IDs, and an empty selectable scope.
- Add bounded helpers for proposal detection, pending plan construction, and selecting sections from
  a stored plan. Keep `resolve_translation_plan` legacy fallback behavior unchanged.
- Export only the helpers required by Worker/API and run focused Core tests, Ruff, and mypy.

## Task 2: Durable ingest pause and resume

- Add a migration extending job status with `waiting_input` and keep the active-ingest uniqueness
  predicate aligned.
- Add Worker tests proving an enabled >30-page job stops after abstract/setup with zero body calls,
  while disabled/30-page/unknown-page jobs retain the current path.
- Persist a selection checkpoint, use a personal set for public opted-in selection (with a valid
  shared base when available), and resume from the checkpointed exact plan.
- Update active-job/progress/status projections and prove cancel/retry/finalization/PDF behavior.

## Task 3: Selection API and Viewer projection

- Add failing API tests for owner-only non-empty canonical selection, appendix policy, malformed
  IDs, shared/foreign set rejection, identical retry, conflicting retry, transaction rollback, and
  best-effort wakeup.
- Expose the selection contract in `ViewerInit`; pending selection reports 0%, never false complete.
- Add `PUT /api/translation-sets/{set_id}/section-selection`, atomically persist plan/checkpoint and
  requeue the parent ingest job.
- Generalize TOC on-demand state to selected-out translatable sections without including references.

## Task 4: Web flow and generated client

- Regenerate OpenAPI/client after the backend contract is stable.
- Add `LongPaperSectionSelection` tests first: default all, hierarchy cascade/indeterminate state,
  non-empty guard, dismiss/reopen, pending/error/retry, exact request, long-label/mobile bounds.
- Render it from `ViewerShell`; invalidate exact Viewer/job caches after acceptance.
- Generalize desktop/mobile/PDF TOC wording and click behavior for any on-demand section.

## Task 5: Review and verification

- Run focused Core/API/Worker/Web suites, migrations up/down/head checks, Ruff/format/mypy,
  generated-client typecheck, Web lint/typecheck, and `git diff --check`.
- Run independent specification and quality reviews; fix critical/important findings with regression
  tests.
- Leave all changes uncommitted and proceed to the remaining arXiv hardening/E2E phases.
