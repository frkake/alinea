# Final Figure Safety Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make SVG sanitization, cancellation teardown, and PDF-extracted figure publication enforce the same finite, bounded, isolated safety invariants.

**Architecture:** Rebuild passive SVG CSS from allowlisted declarations, add cooperative cancellation to the existing spawn supervisor, and route PDF-extracted bytes through the shared materialization boundary. Preserve stable structured failures and failure-atomic S3 staging.

**Tech Stack:** Python 3.12, asyncio, threading, multiprocessing spawn, ElementTree, Pillow, PyMuPDF, httpx, pytest.

---

### Task 1: Finite SVG transform and CSS numeric semantics

**Files:**

- Modify: `apps/worker/src/alinea_worker/figure_assets.py`
- Test: `apps/worker/tests/test_figure_assets.py`

- [x] Add RED tests that capture renderer calls and require `unsafe_vector` before rendering for `translate(1e999 0)`, every invalid transform arity, `stroke-width="1e999"`, and `style="opacity:1e999"`.
- [x] Add safe lexer tests containing an internal fragment with exponent-looking digits, a hex color, an identifier with digits, and finite unit-bearing values.
- [x] Replace permissive transform matching with a complete finite-number argument parser and this arity table:

  ```python
  _SVG_TRANSFORM_ARITIES = {
      "matrix": frozenset({6}),
      "translate": frozenset({1, 2}),
      "scale": frozenset({1, 2}),
      "rotate": frozenset({1, 3}),
      "skewX": frozenset({1}),
      "skewY": frozenset({1}),
  }
  ```

- [x] Add a CSS numeric lexer that skips quoted text, URL targets, hash tokens, and identifier continuations, then checks `math.isfinite(float(number))` independently from its unit.
- [x] Run `uv run pytest -q apps/worker/tests/test_figure_assets.py -k 'svg and (finite or transform or numeric_lexer)'` and require GREEN.

### Task 2: Rebuilt passive CSS declarations

**Files:**

- Modify: `apps/worker/src/alinea_worker/figure_assets.py`
- Test: `apps/worker/tests/test_figure_assets.py`

- [x] Add RED renderer-capture tests for `font-variation-settings:normal;fill:red`, a representative recent Inkscape style, and an unknown declaration containing an external URL; assert safe declarations survive and unknown declarations/URLs do not reach renderer bytes.
- [x] Change declaration validation into `_sanitize_svg_css_declarations(value) -> str`: precheck the complete original text, reject malformed known declarations, drop unknown inert properties, validate known values/references/numbers, and serialize only known declarations.
- [x] Change stylesheet validation into `_sanitize_svg_stylesheet(value) -> str`, retaining passive selector validation and rebuilding each rule from sanitized declarations.
- [x] Replace style attribute/text content in the ElementTree with the sanitized result and remove empty style attributes or child content.
- [x] Run `uv run pytest -q apps/worker/tests/test_figure_assets.py -k 'svg and (style or css or inkscape)'` and require GREEN.

### Task 3: Cancellation-complete cleanup and isolated workers

**Files:**

- Modify: `apps/worker/src/alinea_worker/figure_assets.py`
- Modify: `apps/worker/src/alinea_worker/pipeline.py`
- Test: `apps/worker/tests/test_figure_assets.py`

- [x] Add RED staging tests that cancel the publisher at least twice while deletion is blocked, then release deletion in success and failure variants; assert the original exception is preserved and no cleanup task remains.
- [x] Add a RED bounded-cleanup test with a cancellation-cooperative hanging delete and a shortened cleanup deadline; assert it is canceled, drained, and leaves no task.
- [x] Add RED cancellation tests through figure, thumbnail, and HTML-fetch materialization. Each starts a sleeping isolated worker, cancels twice, awaits the original `CancelledError`, and asserts the child set and pending-task set return to baseline.
- [x] Add a `threading.Event` cancellation input to `_run_isolated_worker`; poll it, then run the existing terminate/kill/reap lifecycle before the supervisor returns.
- [x] Wrap `asyncio.to_thread` in a shielded task. On cancellation, set the thread event, ignore further cancellation only while draining the supervisor task within its bounded teardown interval, retrieve its result, then re-raise the first `CancelledError`.
- [x] Make `_staged_revision_assets` drain its cleanup task through repeated cancellation, impose a configurable deadline, cancel and retrieve timed-out cleanup, log cleanup errors, and re-raise the original exception.
- [x] Run focused cancellation tests repeatedly and require GREEN with no `multiprocessing.active_children()` or relevant pending asyncio tasks.

### Task 4: Isolated PDF-extracted figure materialization

**Files:**

- Modify: `apps/worker/src/alinea_worker/pipeline.py`
- Test: `apps/worker/tests/test_figure_assets.py`

- [x] Add RED tests proving `_save_pdf_assets` routes every image through `_materialize_figure_payload`, uses payload-derived extension/content type/bytes, and counts source plus output toward the aggregate limit.
- [x] Add RED malformed raster, main-process oversize, conversion timeout, and expired/later-document-deadline cases; assert structured failure codes, no PUT, and cleared `asset_key`.
- [x] Add `deadline` to `_save_pdf_assets`, precheck it per figure, pass remaining budget/deadline to `_materialize_figure_payload`, and publish only its returned canonical payload.
- [x] Pass the one `_structure_pdf` materialization deadline into `_save_pdf_assets` and `_make_thumbnail`.
- [x] Run `uv run pytest -q apps/worker/tests/test_figure_assets.py -k 'pdf_pipeline or pdf_asset'` and require GREEN.

### Task 5: Fresh verification, review, and one commit

**Files:** all files above plus this design and plan.

- [x] Run the full figure/parser/ingest/PDF/priority/API pytest set from a fresh process.
- [x] Run Ruff check and format, mypy, Prettier, web typecheck, and the focused viewer tests.
- [x] Run `git diff --check`; scan production diff for paper IDs, titles, authors, and source-fragment special cases.
- [x] Request the same implementation reviewer and resolve all Critical, Important, and Minor findings.
- [x] Commit the complete reviewed change as one scoped commit and report its hash and clean worktree.

### Task 6: Preserve teardown diagnostics under original exceptions

**Files:**

- Modify: `apps/worker/src/alinea_worker/figure_assets.py`
- Modify: `apps/worker/src/alinea_worker/pipeline.py`
- Test: `apps/worker/tests/test_figure_assets.py`

- [x] Add RED cleanup-deadline cases where cancellation produces a real exception, cancellation, or success; require the real exception only in the first case and stable `TimeoutError` in the latter two while preserving the publication error.
- [x] Return the real terminal cleanup exception after the cancellation grace, but retain the stable cleanup timeout for a canceled or successful task.
- [x] Add RED isolated-cancellation cases where the supervisor exits normally or raises `FigureAssetError("conversion_lifecycle", ...)`; require the original `CancelledError` in both cases, one structured warning only for the lifecycle failure, and no warning for normal cancellation.
- [x] Return the drained supervisor exception and log its `code` and `error_type` at the cancellation owner before re-raising the original `CancelledError`.
- [x] Run focused tests, the full figure test file, Python static checks, web smoke tests, the same quality reviewer, and commit the reviewed follow-up separately.
