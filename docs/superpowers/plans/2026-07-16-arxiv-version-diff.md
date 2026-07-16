# arXiv Version Diff (v1→v2) Implementation Plan

> Execute with strict RED/GREEN checkpoints. First slice = diff engine + API only.

**Goal:** Implement the deterministic block-level version diff in
`../specs/2026-07-16-arxiv-version-diff-design.md`, reusing carryover's block-id mapping.
No new dependencies (difflib is stdlib). Tests deterministic (no live LLM).

### Task 1: Diff engine (py-core, TDD) — THIS SLICE

- [ ] Add failing `packages/py-core/tests/test_version_diff.py`: two synthetic revisions →
      assert added/removed/changed/unchanged classification, identity → zero changes,
      document (opcode) order, and a `carry_over_ids`-driven integration case.
- [ ] Add `alinea_core/parsing/version_diff.py`: `diff_revisions(old, new) -> RevisionDiff`
      via `flatten_blocks` + block-id `SequenceMatcher` opcodes + `block_source_hash` compare.
      Export `RevisionDiff`, `BlockChange`, `RevisionDiffStats`, `diff_revisions` from `parsing/__init__`.
- [ ] Run `uv run pytest packages/py-core/tests/test_version_diff.py -q`.

### Task 2: API endpoint (apps/api) — THIS SLICE

- [ ] Add failing `apps/api/tests/test_revision_diff.py`: stats/blocks payload, reject revision
      from another paper (422), access control (404), missing revision (404).
- [ ] Add `GET /api/papers/{paper_id}/revisions/diff?from=&to=` in `routers/viewer.py`
      (next to `list_revisions`); DTOs in `schemas/viewer.py`. Reuse `_paper_accessible`,
      `get_paper_revision`, `_as_content`.
- [ ] Run `uv run pytest packages apps/api -q` (touched areas).

### Task 3 (follow-up slice, out of scope for first commit)

- [ ] Info-panel "変更点" section: summary chips + expandable changed-block list.
- [ ] "変更点を見る" affordance next to the existing newer-version banner.
- [ ] Web component tests; regenerate API client if the public DTO changes.

### Decisions surfaced to user (see spec)

- Presentation surface: info-panel "変更点" section (recommended) vs. dedicated diff mode.
- Change summary: deterministic structural diff for v1; LLM summary optional/flagged (default off).
