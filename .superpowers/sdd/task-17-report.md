# Task 17 — PubMed / PMC adapters + JATS quality-A ingest

## Status
DONE. Committed to worktree branch `worktree-agent-a7c4cd03aed5efa5f` as `ce3e889`.

The worktree was created off `main`, but Task 15 infra (adapters/fetch.py, PaperExternalId,
migrations 0014/0015) lives on `feat/remaining-features-completion`. I `git reset --hard` the
worktree branch onto that branch first, then built Task 17 on top. `.superpowers/brainstorm/`
was never touched.

## TDD RED → GREEN
- **RED (Step 2):** `uv run pytest ... -k 'pubmed or pmc or jats'` collected 2 import errors —
  `cannot import name 'PmcAdapter'` and `No module named 'alinea_core.parsing.jats'`.
- After JATS parser + adapters: 15/19 passed; 4 failed only because tests referenced
  `JatsDocument.iter_blocks` (added it) — an assertion-shape gap, not a logic bug.
- **GREEN (Step 6):** target command
  `test_site_adapters.py test_jats_parser.py test_site_ingest_pipeline.py` → **38 passed**;
  brief's `-k 'pubmed or pmc or jats'` filter → **20 passed, 18 deselected**. Re-run idempotent.
- Regression: `apps/worker/tests/test_ingest.py` full file → **26 passed** (exit 0); arxiv
  full-pipeline + resume + metadata subset re-verified from the worktree → 3 passed;
  `test_schema.py`/`test_document.py` → 25 passed. Ruff clean on all changed files.

## Migration
- File: `apps/api/alembic/versions/0016_jats_source_format.py`
- `revision = "0016_jats_source_format"`, `down_revision = "0015_article_publications"`.
- Adds `'jats'` to `ck_document_revisions_format` (superset: keeps latex/arxiv_html/pdf).
  Downgrade re-points any `jats` rows to `pdf` before restoring the 3-value CHECK.
- DB verification: shared DB was already advanced by a concurrent lane to
  `0016_publication_comments` (multiple 0016 heads expected — controller linearizes at merge,
  per brief). I could not `alembic upgrade head` cleanly because of the parallel 0016; I applied
  the exact CHECK change directly to the shared test DB so the worker JATS test exercises real
  persistence. **Deferred to T32:** running my 0016 through the linearized chain.
- Python Literal: added `'jats'` to `SourceCandidate.source_format` in
  `apps/worker/src/alinea_worker/source_candidates.py`. No API/schema Literal restricts
  document source_format (viewer schema uses `str`); `translation/table_cells.py`'s
  `Literal["html","latex"]` is table-cell translation, unrelated — left as-is.

## XXE hardening approach
`defusedxml` and `lxml` are NOT installed. I parse with stdlib `xml.parsers.expat` directly and
**fail-closed**: `StartDoctypeDeclHandler`, `EntityDeclHandler`, `UnparsedEntityDeclHandler`, and
`ExternalEntityRefHandler` all raise `JatsParseError`, plus `SetParamEntityParsing(NEVER)`. Any
DOCTYPE, any custom ENTITY declaration (internal or external), and any external entity reference
are rejected before expansion — so billion-laughs is impossible (the entity is refused at
declaration). A hand-rolled `_Node` tree is built from the expat element/char handlers; unknown
tags degrade to their concatenated child text. Standard predefined entities (`&amp;` etc.) still
work. Tests cover: external-entity SYSTEM ref, DOCTYPE-only, internal entity bomb, and non-JATS
input — all raise `parse_error`.

## NCBI client + throttle
`alinea_core/adapters/pubmed.py`: `NcbiClient` (efetch db=pmc for JATS, efetch db=pubmed for
abstract XML, ID-converter for PMID→PMCID) over **configurable base URLs** (`NcbiConfig` with
`eutils_base_url` / `idconv_base_url`, overridable + `from_settings`). Tests inject
`httpx.MockTransport` + a base URL host of `eutils.test` / `idconv.test` — **no live NCBI**.
Throttle: `ncbi_throttle` uses the same Redis `SET NX PX` spin as `arxiv_throttle`;
`ncbi_throttle_interval_ms(api_key=...)` = 340ms keyless (≈3 req/s) / 105ms with key (≈10 req/s).
Response size is bounded (`MAX_JATS_BYTES = 24 MiB`); 429→`rate_limited`, 5xx→`upstream_5xx`.

## PMID / PMCID handling
- `PubMedAdapter` matches `pubmed.ncbi.nlm.nih.gov/<PMID>` (+ legacy `ncbi.nlm.nih.gov/pubmed/`)
  → PMID (digits); `pdf_url` is `None` (PubMed has no direct body PDF).
- `PmcAdapter` matches `(www|pmc).ncbi.nlm.nih.gov/(pmc/)?articles/PMC<n>` → PMCID normalized to
  `PMC<digits>` (uppercase, prefix-completes bare digits).
- Registry appends both after ACL Anthology (`resolve_adapter` order preserved).
- A paper may hold BOTH ids — stored via Task 15 `PaperExternalId` (site,external_id unique;
  many ids per paper). `NcbiClient.pmid_to_pmcid` bridges the two.

## Source-candidate ordering
`packages/py-core/src/alinea_core/parsing/source_candidates.py` (new pure module, per brief):
`site_source_candidates("pmc") == ("jats","pdf")`, `("pubmed") == ("pdf",)` (PubMed only uses PDF
when available; otherwise abstract-only), default `("pdf",)`. The worker's existing
`apps/worker/.../source_candidates.py` (arXiv LaTeX→HTML→PDF) is a separate layer, untouched
except the Literal.

## Worker pipeline JATS path
`source='site'` + `source_format='jats'` sets `is_jats` (and excludes it from the pdf_upload
alias). Fetching loads a pre-stored `jats.xml` (`StorageKeys.jats_xml`, new) from S3 — no NCBI
re-fetch in the worker (API stores it, mirroring the site-PDF pattern). Parsing/structuring:
`_parse_and_structure_jats` parses → builds a `ParsedDocument(source_format="jats", quality A)`
→ applies front metadata to the Paper (manual values protected) → reuses the existing
`_structure()` (same figures/search-index/thumbnail path as HTML). Figures carry no asset_key at
parse time (pure); they are caption-only blocks (not required assets) so no HTTP fetch happens in
the worker path here — deferred hrefs are recorded for later on-demand materialization.
No-body JATS → abstract metadata kept on the Paper, then `source_not_found` (body-unavailable),
matching "PubMed w/o JATS → abstract metadata only".

## Files changed
Created: `adapters/pubmed.py`, `parsing/jats.py`, `parsing/source_candidates.py`,
`tests/fixtures/pmc_article.xml`, `tests/test_jats_parser.py`,
`alembic/versions/0016_jats_source_format.py`.
Modified: `adapters/registry.py`, `adapters/__init__.py`, `storage/s3.py` (jats_xml key),
`worker/pipeline.py` (is_jats path), `worker/source_candidates.py` (Literal),
`tests/test_site_adapters.py`, `worker/tests/test_site_ingest_pipeline.py`.

## Self-review / concerns
- **DB migration linearization:** parallel 0016 exists on the shared DB; my 0016 was NOT run via
  alembic (constraint applied directly for the test). Merge controller must linearize — my
  revision id is unique and chains onto 0015 as instructed. (Deferred to T32.)
- **API wiring not in scope:** the worker consumes `source_format='jats'` + a pre-stored
  `jats.xml`, but `POST /api/ingest/site` does not yet branch PMC→NCBI/JATS (it still fetches a
  PDF). Wiring the API to call `NcbiClient.fetch_pmc_jats`, store `jats.xml`, and enqueue
  `source_format='jats'` is a follow-up (the brief's file list did not include the API router;
  the client + adapters + worker path + storage key are all in place for it).
- **Test isolation:** the JATS fixture has a fixed DOI; the worker seed now deletes any prior
  same-DOI Paper so the shared DB stays re-runnable (production dedups by DOI upstream).
- Figure asset fetching for JATS is deferred-only in the worker path (no bounded fetch wired yet);
  hrefs are preserved so a later reingest can materialize them.

---

## Reviewer fix pass (3 defects + 2 minor)

### Important 1 — JATS figures no longer silently vanish
The parser preserves each figure's `graphic/@xlink:href` on `block.href` (and in
`result.deferred_figures`). `_save_figures` now has an explicit JATS branch: every figure block
that declares an href is recorded as a `figure_deferred` placeholder in
`stats.figure_asset_failures` (code `figure_deferred`, with `href`), `asset_key` stays None, and
the block survives structuring. Deleted the dead `self._jats_deferred_figures` assignment (it was
never read) and documented that the href is retained for future on-demand materialization.
Test: `test_parse_jats_figure_deferred_when_asset_unfetched` now also asserts `block.href`;
worker `test_site_ingest_pmc_jats_reaches_complete_quality_a` asserts the figure block survives in
`content` AND a `figure_deferred` entry exists in `rev.stats` (not silently dropped).

### Important 2 — inline href scheme allow-list (stored-XSS parity)
Added `_SAFE_URL_SCHEMES = (http/https/mailto/ftp)` + `_is_safe_url_scheme` in `jats.py`,
mirroring `html_parser.py:448`. `_inline_child` now emits a `url` inline only for safe/relative
hrefs; `javascript:`/`data:` (and any other explicit scheme) degrade to plain text (link text
preserved, never reaches a rendered `<a href>`).
Test: `test_parse_jats_drops_unsafe_url_schemes` asserts the https link survives as a url inline
while javascript:/data: hrefs are dropped and their text is kept.

### Important 3 — PubMed no longer an always-error path
Added `_site_body_unavailable(adapter, ref)` in `routers/ingest.py`, driven by
`site_source_candidates(adapter.site)`: sites whose candidates include `jats` (PMC) pass; PDF-only
sites pass only if the adapter can produce a PDF direct link. PubMed (`pdf_url` None, no JATS
wiring) is gated in `ingest_site` to raise `unsupported_media_type` (415, terminal
"本文取得は未対応") instead of `provider_error` (502, retry-implying). This also gives
`source_candidates.py` a real consumer.
Test: `test_site_ingest_pubmed_without_body_returns_clear_terminal_signal` asserts 415 +
`code == "unsupported_media_type"` and `!= "provider_error"`.

### Minor
- `normalize_pmid`/`normalize_pmcid` de-duplicated: canonical definitions live in
  `adapters/pubmed.py`; `jats.py` imports and re-exports them.
- `prefers_jats` (zero consumers) deleted from `source_candidates.py`; `site_source_candidates`
  is now consumed by the API gate.

### Test output (reviewer command)
`uv run pytest test_site_adapters.py test_jats_parser.py test_site_ingest_pipeline.py
test_ingest_api.py -k 'pubmed or pmc or jats or site'` → **49 passed, 37 deselected**.
The three targeted defect tests pass explicitly (figure-not-lost, href-scheme, PubMed-clear-signal).
Broader regression: full `test_ingest_api.py` 47 passed; ruff clean on all changed files.

### Files changed in this pass
`parsing/jats.py` (href allow-list, normalizer dedup, deferred href on block),
`parsing/source_candidates.py` (dropped `prefers_jats`), `worker/pipeline.py` (JATS deferred
placeholders in `_save_figures`, removed dead attr), `routers/ingest.py` (PubMed body-unavailable
gate + import), plus the three test files.
