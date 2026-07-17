# Context compression (chat + article) design

## Goal

Implement the compression mode described in `docs/05-chat.md` §3 so that long papers are no
longer silently truncated when they exceed the model context budget. Today two builders
mechanically drop the tail of a paper once the token budget is exceeded:

- Chat: `apps/api/src/alinea_api/chat/context_builder.py` — `_truncate_to_budget` cuts
  `render_document_context(...)` at `SYSTEM1_FULL_BUDGET = 60_000` tokens.
- Article: `packages/py-core/src/alinea_core/article/sources.py` — `_truncate_tail_to_budget`
  cuts the translated body at `BODY_BUDGET = 50_000` tokens.

Both drop the *back half* of the document, so questions about conclusions, appendices, or later
experiments get unsupported/wrong answers, and generated articles lose the paper's later half.

Per `docs/05-chat.md` §3, when a paper exceeds the full budget the context must instead be built
as **"全セクションの要約 + 質問に関連するセクションの全文 + 選択範囲の周辺全文"**:

1. A per-section summary of **all** sections (so nothing is fully dropped).
2. Full text of the sections most relevant to the query/selection.
3. (Chat only) The full surrounding text of the selection anchors — already handled separately by
   `_render_surroundings` in the user message, so it is untouched here.

## Summarization approach

**Recommendation: deterministic extractive lead-sentence summaries (default), LLM summarization
deferred as an optional enhancement.**

- The repo's constraints require no new dependencies and deterministic tests (FakeLLM/script
  providers only). A cheap-model summarization pass would either (a) add a live-LLM dependency to a
  pure, synchronous context builder that currently makes zero model calls, or (b) require a large
  new caching/persistence surface to stay deterministic. Neither is justified for this change.
- The extractive summary keeps each section's heading and the lead sentences of its first blocks,
  preserving the `[block_id|position]` evidence markers so chat can still cite a summarized section.
  It is O(n) over the rendered text, fully deterministic, and dependency-free.
- LLM summarization remains a future enhancement: it would produce denser summaries but must be
  gated behind the fake provider in tests and cached per revision to remain deterministic and cheap.
  This is flagged as a decision for the user rather than built now.

## Relevance selection

Investigated `block_search_index` / PGroonga. PGroonga scoring lives behind SQL functions
(`&@~`, `pgroonga_score`) and requires a DB round-trip; it is neither deterministic in unit tests
nor available to the pure, in-memory context builders. The builders already hold the full
`DocumentContent` in memory, so relevance is computed in-memory and deterministically:

- **Selection anchors are authoritative.** Each anchor `block_id` maps to its owning section; those
  sections are promoted to full text first (in document order).
- **Query keyword overlap** (best-effort): latin alphanumeric terms (length >= 3) from the user's
  question are matched, case-insensitively, as substrings against each section's plaintext. Sections
  with matches are promoted next, ordered by match count then document order. Japanese-only queries
  yield no latin terms and rely on anchors + document-order fill, which is acceptable because the
  paper body is the original (English) text and the surrounding-of-selection is added separately.
- PGroonga is documented as the alternative for a future server-side relevance pass; it is not used
  here to keep the builder pure and tests deterministic.

## Construction

A shared, dependency-free primitive `alinea_core.document.context_compaction` operates on a flat,
document-order list of `RenderedSection` (one per section node: its header line + its already
rendered block lines + plaintext for scoring). Both builders refactor their tree walk to emit this
list; the full-text join is byte-identical to today's output, so the non-compressed path is
unchanged.

`compact_document_context(sections, *, budget, preamble, note, query, anchor_section_ids)`:

1. If the full join fits `budget`, return it verbatim (no note, no summaries).
2. Otherwise build an extractive summary for every section and start from "preamble + note + all
   summaries".
3. Promote sections to full text greedily within `budget`: anchor sections first, then
   query-matched sections (by score), then the remaining sections in document order. A section whose
   full text is not larger than its summary is promoted for free.
4. Assemble in document order (full text where promoted, summary otherwise). Every section retains
   at least its heading + lead summary, so a late section is never fully dropped.
5. Final safety guard: if the assembled text still exceeds `budget` (pathological — thousands of
   sections), apply a hard tail truncation as a last resort. Not reachable for realistic papers.

## Contracts and invariants

- The non-compressed path is byte-identical to the previous full render (existing tests unchanged).
- The compressed path never exceeds `budget` tokens (o200k_base, matching the existing estimator).
- Every section heading appears in the compressed output; no section is fully dropped.
- Anchored/relevant sections appear as full text (subject to budget); irrelevant sections appear as
  summaries only.
- No new runtime dependency; no live-LLM call added to the builders.
- Article body still excludes figure/table/reference_entry blocks and preserves `block_source_text`
  for every block regardless of compression.

## Verification

- `packages/py-core/tests/test_context_compaction.py`: unit tests for the primitive — verbatim
  pass-through under budget; note + budget-respect + every-section-present + late-section-summarized
  + anchor/query promotion over budget; and an article-body integration via
  `_render_translated_sections` proving a late section survives as a summary where tail truncation
  dropped it.
- `apps/api/tests/test_chat.py`: a long synthetic document exceeding `SYSTEM1_FULL_BUDGET` proves
  the late section's heading is present (contrasted with `_truncate_to_budget`, which drops it), the
  anchored section's tail is included in full, a late unrelated section is summarized, and the
  system[1] token count stays within budget.
- `uv run pytest apps/api packages -q` for the touched areas.
