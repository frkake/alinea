# Figure Isolation and Atomicity Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Isolate all untrusted figure/thumbnail decoding, preserve safe SVGs and HTML fallbacks, and make derived S3 publication failure-atomic.

**Architecture:** Extend the disposable spawn supervisor into the only production materialization boundary, sanitize SVG XML by attribute semantics, and stage revision-specific thumbnails inside one cleanup guard. A document deadline supplies remaining wall time to every fetch and child process.

**Tech Stack:** Python 3.12, multiprocessing spawn, asyncio, Pillow, PyMuPDF, ElementTree, SQLAlchemy, httpx, S3, pytest.

---

### Task 1: All-format process isolation and document deadline

**Files:**

- Modify: `apps/worker/src/alinea_worker/figure_assets.py`
- Modify: `apps/worker/src/alinea_worker/pipeline.py`
- Test: `apps/worker/tests/test_figure_assets.py`

- [x] Add RED tests proving raster pipeline materialization calls `isolated_figure_asset_payload`, unknown magic returns `unsupported_figure_format` without `Image.open`, frame/pixel/AS limits are enforced, and timeout children disappear from `multiprocessing.active_children()`.
- [x] Add RED tests for `_terminate_and_reap` raising a stable lifecycle error when a fake process remains alive after kill.
- [x] Add RED tests proving thumbnail rendering succeeds while main-process thumbnail/Pillow functions are monkeypatched to fail and that thumbnail timeout is stable.
- [x] Add a fake-clock RED test where the first conversion consumes the document deadline and later figures/thumbnail do not start.
- [x] Replace the conditional production branch with unconditional `await isolated_figure_asset_payload(...)`; reject unknown magic before decoder entry in the child worker.
- [x] Generalize the spawn supervisor result envelope for `FigureAssetPayload` and `ThumbnailPayload`; apply resource limits and require kill plus join or raise lifecycle failure.
- [x] Add `MaterializationDeadline(deadline, clock)` and pass remaining seconds into fetch, conversion, and thumbnail APIs.
- [x] Rename synchronous helpers to explicit trusted-child/test names and remove them from `__all__` production exports.
- [x] Run `uv run pytest apps/worker/tests/test_figure_assets.py -k 'isolat or deadline or thumbnail or pixel or frame' -q` and require all GREEN.

### Task 2: Semantic SVG sanitizer and HTML fallback

**Files:**

- Modify: `apps/worker/src/alinea_worker/figure_assets.py`
- Modify: `packages/py-core/src/alinea_core/parsing/html_parser.py`
- Modify: `apps/worker/src/alinea_worker/pipeline.py`
- Test: `apps/worker/tests/test_figure_assets.py`
- Test: `packages/py-core/tests/test_html_parser.py`

- [x] Add RED safe tests for punctuation/email-like inert text, pixel-unit transforms, and internal clip paths.
- [x] Add RED representative Matplotlib, Inkscape namespace/metadata, and arXiv SVG fixtures plus active/external regression cases.
- [x] Add a RED parser/pipeline test containing invalid SVG CSS and a valid image fallback; assert fallback PNG survives and raw is cleared.
- [x] Change validation to sanitize a parsed tree: reject active semantics, validate internal references and known CSS/presentation fields, validate inert geometry/text fields, remove unknown/data attributes and foreign metadata nodes, then serialize sanitized SVG.
- [x] Preserve both `raw` and fallback `asset_key` in the transient HTML figure block; try raw first and fetch fallback only after raw failure.
- [x] Run `uv run pytest apps/worker/tests/test_figure_assets.py packages/py-core/tests/test_html_parser.py -k 'svg or fallback' -q` and require all GREEN.

### Task 3: Failure-atomic S3 staging and immutable thumbnails

**Files:**

- Modify: `packages/py-core/src/alinea_core/storage/s3.py`
- Modify: `apps/worker/src/alinea_worker/pipeline.py`
- Modify: `apps/api/src/alinea_api/routers/library_items.py`
- Test: `apps/worker/tests/test_figure_assets.py`
- Test: `apps/worker/tests/test_latex_priority_pipeline.py`
- Test: `apps/api/tests/test_library_api.py`

- [x] Add RED tests for cleanup after index failure, second thumbnail PUT failure, cancellation, and commit failure while preserving the original exception.
- [x] Add RED tests proving reingest writes revision-specific thumbnail keys and never overwrites/deletes the prior current thumbnail on failure.
- [x] Add RED strict parser tests for deriving `card@2x.webp` only from valid legacy or revision thumbnail base keys; reject arbitrary keys.
- [x] Wrap figure upload, indexing, isolated thumbnail generation, and commit in one `try/except BaseException`; shield best-effort deletion of only tracked keys and re-raise the original exception.
- [x] Add revision-specific thumbnail key builders and strict retina-sibling parsing; update deletion collection for the current pointer and retain paper thumbnail prefix cleanup.
- [x] Run focused worker/API cleanup tests and require all GREEN.

### Task 4: Full verification and one commit

**Files:** all files above plus this plan/design.

- [x] Run the full parser/figure/priority pipeline pytest set, worker/API cleanup tests, Ruff, format check, mypy, web typecheck, and viewer focused tests.
- [x] Run `git diff --check` and scan the diff for paper IDs, titles, authors, or source-fragment special cases.
- [x] Commit the approved design, plan, implementation, and tests as one scoped commit and report the hash and clean status.

### Task 5: Document-limited operation timeout mapping

**Files:**

- Modify: `apps/worker/src/alinea_worker/pipeline.py`
- Test: `apps/worker/tests/test_figure_assets.py`

- [x] Add parametrized RED boundary tests for conversion, HTML asset fetch, and thumbnail timeouts: remap to `materialization_timeout` only when the document deadline strictly shortens the operation limit, and preserve the operation-specific code when both limits are equal.
- [x] Introduce one timeout selection result that records both the effective seconds and whether the document deadline was the limiting bound, reading the deadline clock once per operation.
- [x] Map only the active operation's timeout code (`conversion_timeout`, `asset_fetch_timeout`, or `thumbnail_timeout`) when the document deadline is limiting; preserve all other failure codes.
- [x] Run focused tests to GREEN, then the fresh worker/parser/API suite, Python static checks, web typecheck/viewer tests, diff/hardcode scans, and specification review.
- [x] Commit the reviewed fix and tests as one separate scoped commit.
