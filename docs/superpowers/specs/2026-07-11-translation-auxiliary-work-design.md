# Translation Auxiliary Work API Design

## Goal

Persist on-demand and retry translation work as canonical auxiliary targets without changing the
primary translation denominator, while making plan mutation and job creation atomic and safely
repeatable across failures and concurrent requests.

## Boundaries

- Modify the translations API and `JobStore`; do not modify the core translation pipeline.
- Do not modify `viewer.py` or `test_viewer_api.py`.
- Keep existing initial scheduling, progress, finalization, and empty-primary completion semantics
  based on primary targets only.
- Keep the existing commit-on-enqueue `JobStore.enqueue` contract. Add a separate uncommitted API.

## Document validation

Every translations-router conversion from `DocumentRevision.content` to `DocumentContent` runs
the core scope computation once. This validates revision-global uniqueness and size bounds for
section and block IDs. Invalid JSON, duplicate IDs, and invalid scope data produce a stable
`422 validation_error`; no endpoint may continue with the first matching duplicate ID.

## Work requests

A translation work request is identified by:

- translation set ID;
- section ID;
- work kind: `literal`, `full`, `table`, or `retry`;
- canonical hash of the requested block IDs.

The stable identity is stored in `jobs.payload.request_key`. Each concrete attempt also stores an
integer `generation`; its idempotency key is the stable identity plus the generation. Legacy jobs
without these fields match only when their set, section, reason/work kind, table marker, and exact
canonical block list agree.

Active matching jobs (`queued`, `running`, `waiting_quota`) are reused. A succeeded job is reused
only when every requested block resolves through the effective personal/base overlay and has no
blocking quality flag. Failed, canceled, or succeeded-but-incomplete work advances to one above the
largest known generation, allowing unlimited user-triggered retries.

## Section validation and auxiliary plans

A full-section request selects only direct translatable blocks in that section from the core full
scope. Empty and reference-only sections are rejected. A block request accepts exactly one direct
block of type `table` in the requested section and full scope. Unknown, cross-section, non-table,
reference, and empty requests are rejected with `422 validation_error`.

The translation-set row is locked before reading or mutating its plan. Requested blocks outside
the primary plan are appended monotonically to `auxiliary_block_ids` in canonical document order.
For a personal set, work already inherited from a valid shared base effective plan is not duplicated
in the personal auxiliary list. Primary targets, set status, and progress denominator do not change.

## Retry behavior

Retry considers blocking units from the primary plus effective auxiliary execution scope and uses
valid base-overlay units. A legacy blocking unit outside the stored plan is accepted only after its
block is revalidated as a full-scope translatable member of its actual section; it is then backfilled
into the owning plan's auxiliary list. Optional section filtering is applied after that membership
validation. All section jobs and the plan change commit in one transaction.

## Transactions and concurrency

`JobStore.enqueue_uncommitted` performs lookup, add, and flush but never commits or rolls back.
Callers hold the translation-set row lock, perform exact work reuse/generation selection, enqueue all
new jobs, and commit once. Unexpected enqueue/flush errors cause an explicit outer rollback, so a
plan cannot remain without its jobs. Wakeups occur only after commit. The row lock serializes two
requests for the same set and prevents duplicate plan additions or jobs.

## Compatibility

Legacy plans without `auxiliary_block_ids` resolve as an empty auxiliary list. Literal shared-set
reuse keeps returning the same active or completed/displayable job. Only terminal incomplete work
creates a new generation. Worker payloads include the same primary `block_ids`; plan JSON assertions
accept the additive `auxiliary_block_ids: []` field.

## Verification

TDD covers invalid/duplicate document IDs, section/table membership, empty requests, monotonic plan
updates, unchanged progress/status, atomic rollback, concurrent requests, active/succeeded reuse,
multiple failed generations, legacy blocking backfill, and personal/base retry behavior. Focused API,
JobStore, and worker tests run before broader API/core/worker suites, followed by Ruff, mypy, diff,
and hardcode audits.
