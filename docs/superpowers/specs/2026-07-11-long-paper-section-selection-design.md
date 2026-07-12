# Long-paper section selection design

## Goal

When the explicit setting `suggest_section_selection_over_30_pages` is enabled and the parsed
paper is over 30 pages, stop before body translation and let the owning user choose sections from
the Web viewer. All selectable sections are checked initially, so accepting the default still
starts full translation. Papers at 30 pages or fewer, unknown page counts, and users with the
setting disabled retain the existing automatic full-scope path.

The implementation is revision-local and structural. It never branches on an arXiv ID, title,
category, or paper-specific section name.

## Lifecycle

1. Fetch, parse, structure, and abstract/summary generation complete normally.
2. Core builds a strict, empty primary `TranslationPlan` only when the long-paper proposal applies.
3. The ingest job creates or resolves its translation set, stores a checkpoint, enters
   `status=waiting_input` / `stage=selecting_sections`, publishes progress, and returns without
   starting body LLM calls.
4. Viewer init exposes an explicit selection contract containing `required`, the exact selectable
   section IDs, and the currently selected IDs. The document TOC supplies titles and hierarchy.
5. Web opens a dismissible, accessible dialog with all selectable sections checked. The user may
   submit that default (full translation), submit a non-empty subset, or close and return later.
6. The selection endpoint validates ownership, revision/set/job relationships, canonical section
   membership/order, appendix policy, and the waiting checkpoint. It persists the exact primary
   plan and requeues the same ingest job atomically, then sends a best-effort worker wakeup.
7. The resumed worker reads the checkpointed plan, translates only its primary sections, computes
   progress against only those sections, finalizes the ingest, and builds the translated PDF.
8. Unselected translatable sections remain visible and are marked on-demand. Opening one uses the
   existing auxiliary-work endpoint, so it does not change the initial progress denominator.

## Public-paper isolation

A shared `TranslationSet.plan` is a service-wide cache target and cannot represent different users'
progress denominators. Therefore an opted-in long-paper selection uses a personal selection set for
the owner. If a compatible shared set already exists it is retained as `base_set_id`, so completed
shared units are reused. Missing selected units are written to the personal set; this avoids
cross-user parent-job/idempotency races while preserving correctness and existing cache reuse.
Users who do not opt in keep the existing shared full-translation path.

## Contracts and invariants

- Threshold is strictly `pages > 30` and requires a real integer page count.
- Pending selection is a valid v1 plan with empty primary targets, the proposal flag set, and the
  original appendix/table-cell policy preserved.
- Submission must contain at least one selectable section, no duplicates, no unknown IDs, and no
  sections excluded by the stored appendix policy. The server canonicalizes output document order.
- Only the owner of the waiting ingest job and personal set may submit. Shared-set mutation through
  this endpoint is rejected.
- The first accepted selection is immutable. An identical retry is idempotent; a different retry is
  a conflict. No second set of jobs is created.
- Plan persistence, job transition to `queued`, and checkpoint update are one transaction. Wakeup
  failure leaves a durable queued job recoverable by the existing DB requeue command.
- Invalid/corrupt plans fail closed to a selection error; they never silently schedule a full paper.
- Empty or reference-only documents do not wait for impossible input.

## Frontend behavior

- The dialog is keyboard/focus managed by the existing `Modal` component and scrolls within the
  viewport for large TOCs.
- Parent checkbox changes cascade to descendants; indeterminate parents reflect partial child
  selection. The submitted payload is the exact selectable section-ID set, not display labels.
- Submit is disabled for an empty selection and while the request is pending. Errors stay visible
  and retryable without losing the selection.
- Closing the proposal leaves a persistent "翻訳するセクションを選択" action in the viewer.
- Mobile and desktop use the same component. Long labels wrap; the modal and rows have bounded
  width/height and no fixed text height.
- Selected-out sections use generic "開くと翻訳します" wording rather than appendix-only wording
  in Viewer, mobile TOC, and PDF TOC.

## Verification

Core tests cover threshold, pending-plan construction, canonical selection, nested sections,
appendix opt-out, duplicates/unknown IDs, and empty documents. Worker tests prove no body model call
before selection and exact resume/finalization after it. API tests cover ownership, atomicity,
idempotency, corruption, viewer projection, and wakeup failure. Web tests cover default-all,
subset/parent propagation, empty/error/pending states, dismiss/reopen, mobile bounds, and request
payloads. A browser E2E exercises a synthetic >30-page ingest from setting toggle through resume.
