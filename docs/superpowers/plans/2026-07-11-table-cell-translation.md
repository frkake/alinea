# Table Cell Translation Implementation Plan

> Execute with strict RED/GREEN checkpoints. Do not commit.

**Goal:** Implement the shared table-cell contract in
`../specs/2026-07-11-table-cell-translation-design.md` end to end.

### Task 1: Canonical grid and typed translation contract

- [ ] Add failing core tests for bounded HTML/LaTeX grids, spans, math, prose classification,
      malformed input, and strict typed-result dimensions.
- [ ] Add `translation/table_cells.py` and public exports without paper-specific values.
- [ ] Run focused core parser/contract tests.

### Task 2: Translation pipeline integration

- [ ] Add failing tests for caption-only versus cells-enabled plans, explicit `reason="table"`, exact
      pseudo IDs, placeholder failures, aggregate source hashes, retries, and one-unit progress.
- [ ] Translate/aggregate table targets through the existing verified batching path.
- [ ] Preserve legacy inline captions and fail closed on malformed model output.
- [ ] Run core and Worker translation tests.

### Task 3: API scheduling and completeness

- [ ] Add failing API tests proving caption-only units do not satisfy table work, typed complete
      units do, plan settings affect literal/full reuse, and explicit requests remain atomic.
- [ ] Make work displayability table-aware without changing the primary denominator.
- [ ] Run auxiliary/literal/retry/jobs tests.

### Task 4: Viewer API and frontend

- [ ] Expose the canonical source grid and add failing Viewer/FigureTableBlock tests for exact overlay,
      spans, captions, malformed typed data, overflow, and legacy fallback.
- [ ] Add the visible `この表を翻訳` action in translation and bilingual modes with SSE lifecycle and
      exact query invalidation.
- [ ] Regenerate the API client only if the public DTO changes; run Web tests/typecheck.

### Task 5: Japanese PDF and article tables

- [ ] Add failing LaTeX tests for cell replacement with multicolumn/multirow/rules/math and mismatch
      all-or-nothing fallback; add compile regression where TeX is available.
- [ ] Overlay the typed matrix in article table rows.
- [ ] Run latex-PDF, article, and Worker suites.

### Task 6: Review and broad verification

- [ ] Independent contract/security/layout review and fixes.
- [ ] Run full core/API/Worker/Web suites, Ruff, mypy, typecheck, diff check, and hardcode scan.
- [ ] Verify migrations remain at head. Leave changes uncommitted.
