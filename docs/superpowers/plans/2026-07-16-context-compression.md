# Context compression implementation plan

> Execute with strict RED/GREEN checkpoints. TDD; deterministic (no live LLM).

**Goal:** Implement the compression mode in
`../specs/2026-07-16-context-compression-design.md` for chat and article context builders.

### Task 1: Shared compaction primitive (py-core)

- [ ] Add failing `packages/py-core/tests/test_context_compaction.py`: verbatim pass-through under
      budget; over budget -> note present, all section headers present, late section summarized,
      anchor + query sections promoted to full, token count within budget.
- [ ] Add `alinea_core/document/context_compaction.py` with `RenderedSection` and
      `compact_document_context(...)` (extractive lead-sentence summaries, in-memory relevance,
      greedy promotion, final safety guard). No new dependencies.
- [ ] GREEN: run the new py-core test.

### Task 2: Chat context builder integration (api)

- [ ] Add failing `apps/api/tests/test_chat.py` case: long synthetic doc > `SYSTEM1_FULL_BUDGET`,
      late-section heading present (vs. `_truncate_to_budget` dropping it), anchored section full,
      late unrelated section summarized, system[1] within budget.
- [ ] Refactor `render_document_context` to emit `RenderedSection`s; wire `build_chat_request` to
      `compact_document_context` with the query and anchor sections; drop `_truncate_to_budget`.
- [ ] Update the module docstring (compression mode now implemented).
- [ ] GREEN: run `apps/api/tests/test_chat.py`.

### Task 3: Article sources integration (py-core)

- [ ] Refactor `_render_translated_body` -> `_render_translated_sections` emitting
      `RenderedSection`s while preserving `block_source_text` for every block and the
      figure/table/ref exclusions.
- [ ] Wire `collect_article_sources` to `compact_document_context` (no query); drop
      `_truncate_tail_to_budget`.
- [ ] Cover the article body via the py-core compaction test (integration through
      `_render_translated_sections`).
- [ ] GREEN: run py-core article tests.

### Task 4: Broad verification

- [ ] `uv run pytest apps/api packages -q` for touched areas.
- [ ] Report summarization-approach recommendation to `.superpowers/sdd/s6-report.md`.
- [ ] Commit to the working branch.
