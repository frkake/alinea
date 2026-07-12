# arXiv Ingest and Figure Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make arXiv ingestion select the first complete LaTeX/HTML/PDF candidate and persist every referenced figure as a valid display asset without paper-specific rules.

**Architecture:** Add a pure document-completeness classifier in py-core, then make the worker evaluate LaTeX, HTML, PDF-text, and PDF-OCR candidates instead of committing the first parsable source. Move generic figure path expansion/conversion into a focused worker module and reject candidates whose declared figures cannot be materialized.

**Tech Stack:** Python 3.12, Pydantic document models, PyMuPDF, httpx, SQLAlchemy, pytest, S3-compatible storage.

---

### Task 1: Document completeness classifier

**Files:**
- Create: `packages/py-core/src/alinea_core/ingest/completeness.py`
- Modify: `packages/py-core/src/alinea_core/ingest/__init__.py`
- Create: `packages/py-core/tests/test_document_completeness.py`

- [ ] **Step 1: Write failing completeness tests**

```python
from alinea_core.document.blocks import Block, DocumentContent, Heading, Section
from alinea_core.ingest.completeness import assess_document_completeness


def _doc(*blocks: Block) -> DocumentContent:
    return DocumentContent(
        sections=[Section(id="sec-1", heading=Heading(number="1", title="Body"), blocks=list(blocks))]
    )


def test_rejects_single_embedded_pdf_filename() -> None:
    report = assess_document_completeness(
        _doc(Block(id="b1", type="paragraph", inlines=[{"t": "text", "v": "paper.pdf"}])),
        pdf_text="A long PDF body " * 200,
        source_manifest={"binary_files": ["paper.pdf"]},
    )
    assert not report.accepted
    assert report.code == "embedded_pdf_wrapper"


def test_accepts_short_but_structured_note() -> None:
    report = assess_document_completeness(
        _doc(
            Block(id="h1", type="heading", inlines=[{"t": "text", "v": "Method"}]),
            Block(id="p1", type="paragraph", inlines=[{"t": "text", "v": "A concise method."}]),
            Block(id="p2", type="paragraph", inlines=[{"t": "text", "v": "A concise result."}]),
        ),
        pdf_text="",
        source_manifest={},
    )
    assert report.accepted
```

- [ ] **Step 2: Run tests and confirm RED**

Run: `uv run pytest packages/py-core/tests/test_document_completeness.py -q`  
Expected: FAIL because `alinea_core.ingest.completeness` does not exist.

- [ ] **Step 3: Implement the pure classifier**

```python
@dataclass(frozen=True)
class DocumentCompleteness:
    accepted: bool
    code: str | None
    source_chars: int
    structured_chars: int
    paragraph_count: int
    figure_count: int
    unresolved_figures: int = 0

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def assess_document_completeness(
    content: DocumentContent,
    *,
    pdf_text: str,
    source_manifest: Mapping[str, Any],
    unresolved_figures: int = 0,
) -> DocumentCompleteness:
    blocks = [block for _section, block in content.iter_blocks()]
    prose = "\n".join(block_to_plain(block) for block in blocks if block.type in TRANSLATABLE_BLOCK_TYPES)
    paragraphs = [block for block in blocks if block.type in {"paragraph", "list", "quote", "theorem"}]
    binary_pdfs = {Path(name).name for name in source_manifest.get("binary_files", []) if name.lower().endswith(".pdf")}
    visible = prose.strip()
    if len(blocks) <= 3 and visible in binary_pdfs:
        return DocumentCompleteness(False, "embedded_pdf_wrapper", len(pdf_text), len(visible), len(paragraphs), 0)
    if unresolved_figures:
        return DocumentCompleteness(False, "figure_asset_unresolved", len(pdf_text), len(visible), len(paragraphs), 0, unresolved_figures)
    if len(pdf_text.strip()) >= 1_000 and len(visible) < int(len(pdf_text.strip()) * 0.35):
        return DocumentCompleteness(False, "document_incomplete", len(pdf_text), len(visible), len(paragraphs), 0)
    accepted = bool(visible) and (len(paragraphs) >= 2 or any(block.type == "heading" for block in blocks))
    return DocumentCompleteness(accepted, None if accepted else "document_incomplete", len(pdf_text), len(visible), len(paragraphs), sum(b.type == "figure" for b in blocks))
```

- [ ] **Step 4: Run focused and existing py-core tests**

Run: `uv run pytest packages/py-core/tests/test_document_completeness.py packages/py-core/tests/test_document.py -q`  
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/py-core/src/alinea_core/ingest packages/py-core/tests/test_document_completeness.py
git commit -m "feat: validate structured document completeness"
```

### Task 2: Mandatory original-PDF acquisition and candidate fallback

**Files:**
- Create: `apps/worker/src/alinea_worker/source_candidates.py`
- Modify: `apps/worker/src/alinea_worker/pipeline.py`
- Test: `apps/worker/tests/test_latex_priority_pipeline.py`

- [ ] **Step 1: Add a failing LaTeX-error + HTML-404 + PDF-success integration test**

```python
async def test_ingest_falls_back_to_stored_pdf_when_latex_and_html_fail(
    db_session, pdf_fallback_worker_ctx, seed_ingest_job
) -> None:
    ids = await seed_ingest_job(db_session, arxiv_id=_arxiv_id())
    store = JobStore(db_session)
    job = await store.claim(ids["job_id"])
    await ingest_paper(pdf_fallback_worker_ctx, store, job)

    completed = await store.get(ids["job_id"])
    revision = await db_session.get(DocumentRevision, completed.result["revision_id"])
    assert completed.status == "succeeded"
    assert revision.source_format == "pdf"
    assert revision.stats["candidate_failures"][0]["format"] == "latex"
    assert revision.stats["candidate_failures"][1]["format"] == "arxiv_html"
```

The ASGI fixture must return an invalid e-print, 404 for `/html/*`, and a valid multi-paragraph PDF for `/pdf/*`.

- [ ] **Step 2: Run the integration test and confirm RED**

Run: `uv run pytest apps/worker/tests/test_latex_priority_pipeline.py::test_ingest_falls_back_to_stored_pdf_when_latex_and_html_fail -q`  
Expected: FAIL with the current HTML `source_not_found` error.

- [ ] **Step 3: Add candidate result types and original-PDF lookup**

```python
@dataclass
class SourceCandidate:
    source_format: Literal["latex", "arxiv_html", "pdf"]
    content: DocumentContent
    parsed: ParsedDocument | ParsedPdfDocument
    report: DocumentCompleteness
    source_bytes: bytes
    diagnostics: list[dict[str, Any]]


@dataclass(frozen=True)
class CandidateUnavailable(Exception):
    source_format: str
    code: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {"format": self.source_format, "code": self.code, "message": self.message}


async def load_original_pdf(storage: S3Storage, paper_id: str, source_version: str) -> bytes:
    return await storage.get(storage.sources_bucket, StorageKeys.original_pdf(paper_id, source_version))
```

- [ ] **Step 4: Change fetching to save/reuse PDF before optional candidates**

In `IngestRun._stage_fetching`, call a new `_ensure_original_pdf` immediately after metadata/version resolution. It must first read the existing `SourceAsset(kind="pdf")`, otherwise fetch `/pdf/{versioned}` and persist it. Replace `_fetch_pdf_best_effort` with a required acquisition that raises only after both cache and network fail.

```python
pdf_bytes = await self._ensure_original_pdf(http, base)
self._pdf_bytes = pdf_bytes
await self._collect_latex_and_html_candidates(http, base)
```

- [ ] **Step 5: Evaluate all candidates before persisting a revision**

```python
for loader in (self._latex_candidate, self._html_candidate, self._pdf_candidate):
    try:
        candidate = await loader()
    except CandidateUnavailable as exc:
        failures.append(exc.as_dict())
        continue
    if candidate.report.accepted:
        await self._persist_candidate(candidate, failures=failures)
        return
    failures.append(candidate.report.as_dict())
raise FetchError("document_incomplete", json.dumps({"candidates": failures}, ensure_ascii=False))
```

- [ ] **Step 6: Run fallback, LaTeX-priority, HTML-priority, and PDF-upload tests**

Run: `uv run pytest apps/worker/tests/test_latex_priority_pipeline.py apps/worker/tests/test_pdf_upload_pipeline.py -q`  
Expected: PASS; successful LaTeX still wins, HTML remains second, PDF is used after both fail.

- [ ] **Step 7: Commit**

```bash
git add apps/worker/src/alinea_worker/source_candidates.py apps/worker/src/alinea_worker/pipeline.py apps/worker/tests/test_latex_priority_pipeline.py
git commit -m "feat: fall back to original pdf ingestion"
```

### Task 3: Embedded-PDF wrapper promotion

**Files:**
- Modify: `apps/worker/src/alinea_worker/source_candidates.py`
- Modify: `apps/worker/src/alinea_worker/pipeline.py`
- Test: `apps/worker/tests/test_latex_priority_pipeline.py`

- [ ] **Step 1: Write a failing embedded-PDF wrapper test**

Build a tar fixture whose `main.tex` contains `\includepdf[pages=-]{body.pdf}` and whose `body.pdf` contains headings and several paragraphs.

```python
async def test_latex_wrapper_promotes_embedded_pdf_to_pdf_candidate(
    db_session: AsyncSession,
    embedded_pdf_worker_ctx: dict[str, Any],
    seed_ingest_job: Any,
) -> None:
    ids = await seed_ingest_job(db_session, arxiv_id=_arxiv_id())
    store = JobStore(db_session)
    job = await store.claim(ids["job_id"])
    assert job is not None
    await ingest_paper(embedded_pdf_worker_ctx, store, job)
    revision = (
        await db_session.execute(
            select(DocumentRevision).where(DocumentRevision.paper_id == ids["paper_id"])
        )
    ).scalars().one()
    assert revision.source_format == "pdf"
    assert revision.stats["embedded_pdf_source"] == "body.pdf"
    assert revision.stats["blocks"] > 5
    assert "body.pdf" not in json.dumps(revision.content)
```

- [ ] **Step 2: Run and confirm RED**

Run: `uv run pytest apps/worker/tests/test_latex_priority_pipeline.py::test_latex_wrapper_promotes_embedded_pdf_to_pdf_candidate -q`  
Expected: FAIL because the wrapper is accepted as a one-block LaTeX document.

- [ ] **Step 3: Implement structural embedded-PDF selection**

```python
def embedded_pdf_bytes(
    report: DocumentCompleteness,
    binary_files: Mapping[str, bytes],
) -> tuple[str, bytes] | None:
    if report.code != "embedded_pdf_wrapper":
        return None
    pdfs = [(name, data) for name, data in binary_files.items() if name.lower().endswith(".pdf")]
    if len(pdfs) != 1:
        return None
    return pdfs[0]
```

Feed the selected bytes through `parse_pdf`, not through a title or filename special case, and store the selected source name only in revision diagnostics.

- [ ] **Step 4: Run the wrapper and general candidate tests**

Run: `uv run pytest apps/worker/tests/test_latex_priority_pipeline.py -q`  
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/worker/src/alinea_worker/source_candidates.py apps/worker/src/alinea_worker/pipeline.py apps/worker/tests/test_latex_priority_pipeline.py
git commit -m "fix: parse embedded pdf wrapper documents"
```

### Task 4: Generic LaTeX/HTML figure asset resolution

**Files:**
- Create: `apps/worker/src/alinea_worker/figure_assets.py`
- Modify: `apps/worker/src/alinea_worker/pipeline.py`
- Create: `apps/worker/tests/test_figure_assets.py`

- [ ] **Step 1: Write failing path and conversion tests**

```python
def test_resolves_extensionless_asset_using_graphicspath() -> None:
    result = resolve_latex_asset(
        binary_files={"images/plot.eps": EPS_BYTES},
        requested="plot",
        main_tex_name="paper/main.tex",
        graphicspaths=["../images/"],
    )
    assert result.source_name == "images/plot.eps"
    assert result.payload.ext == "png"
    assert result.payload.content.startswith(b"\x89PNG")


def test_rejects_control_sequence_as_asset_key() -> None:
    assert normalize_requested_asset(r"\iftoggle{largefigures") is None


def test_normalizes_version_relative_html_asset() -> None:
    assert html_asset_url("https://arxiv.org", "2401.00001v2", "2401.00001v2/x1.png") == (
        "https://arxiv.org/html/2401.00001v2/x1.png"
    )
```

- [ ] **Step 2: Run and confirm RED**

Run: `uv run pytest apps/worker/tests/test_figure_assets.py -q`  
Expected: FAIL because the module does not exist.

- [ ] **Step 3: Implement path candidate generation and raster conversion**

```python
SUPPORTED = (".pdf", ".png", ".jpg", ".jpeg", ".eps", ".ps", ".svg")


def asset_candidates(requested: str, main_tex_name: str | None, graphicspaths: Sequence[str]) -> list[str]:
    clean = normalize_requested_asset(requested)
    if clean is None:
        return []
    roots = [PurePosixPath(main_tex_name).parent if main_tex_name else PurePosixPath("."), *(PurePosixPath(p) for p in graphicspaths)]
    suffixes = ("",) if PurePosixPath(clean).suffix else SUPPORTED
    return list(dict.fromkeys(str((root / f"{clean}{suffix}").as_posix()).lstrip("./") for root in roots for suffix in suffixes))
```

Use PyMuPDF for PDF/EPS/PS rasterization when supported, Pillow for raster validation/conversion, and CairoSVG for SVG. A converted payload must decode and have width and height greater than zero.

- [ ] **Step 4: Replace pipeline-local figure helpers**

Import `resolve_latex_asset`, `fetch_html_asset`, and `validate_image_payload` from the new module. On failure, set `fig.asset_key = None` and append a structured diagnostic rather than leaving the original path or macro as a public asset key.

- [ ] **Step 5: Run focused worker tests**

Run: `uv run pytest apps/worker/tests/test_figure_assets.py apps/worker/tests/test_latex_priority_pipeline.py -q`  
Expected: PASS.

- [x] **Step 6: Commit**

```bash
git add apps/worker/src/alinea_worker/figure_assets.py apps/worker/src/alinea_worker/pipeline.py apps/worker/tests/test_figure_assets.py
git commit -m "feat: resolve and validate paper figure assets"
```

### Task 5: Figure completeness gate and PDF fallback

**Files:**
- Modify: `apps/worker/src/alinea_worker/source_candidates.py`
- Modify: `apps/worker/src/alinea_worker/pipeline.py`
- Test: `apps/worker/tests/test_latex_priority_pipeline.py`
- Test: `apps/worker/tests/test_pdf_upload_pipeline.py`

- [ ] **Step 1: Write a failing unresolved-figure fallback test**

```python
async def test_candidate_with_missing_figures_falls_back_to_pdf_extraction(
    db_session: AsyncSession,
    missing_figure_worker_ctx: dict[str, Any],
    seed_ingest_job: Any,
) -> None:
    ids = await seed_ingest_job(db_session, arxiv_id=_arxiv_id())
    store = JobStore(db_session)
    job = await store.claim(ids["job_id"])
    assert job is not None
    await ingest_paper(missing_figure_worker_ctx, store, job)
    revision = (
        await db_session.execute(
            select(DocumentRevision).where(DocumentRevision.paper_id == ids["paper_id"])
        )
    ).scalars().one()
    content = DocumentContent.model_validate(revision.content)
    figures = [b for _s, b in content.iter_blocks() if b.type == "figure"]
    assert figures
    assert all(b.asset_key and b.asset_key.startswith("figures/") for b in figures)
    assert revision.stats["candidate_failures"][0]["code"] == "figure_asset_unresolved"
```

- [ ] **Step 2: Run and confirm RED**

Run: `uv run pytest apps/worker/tests/test_latex_priority_pipeline.py::test_candidate_with_missing_figures_falls_back_to_pdf_extraction -q`  
Expected: FAIL because `_save_figures` is best-effort and commits missing assets.

- [ ] **Step 3: Make figure materialization part of candidate validation**

```python
materialized, failures = await self._materialize_candidate_figures(candidate)
candidate.report = assess_document_completeness(
    materialized.content,
    pdf_text=pdf_text,
    source_manifest=materialized.manifest,
    unresolved_figures=len(failures),
)
if not candidate.report.accepted:
    await self._discard_staged_assets(materialized.staged_keys)
    continue
```

For a PDF candidate, persist the `figure_images` returned by `parse_pdf`. A source candidate with no figure block remains valid; a candidate with figure blocks but unresolved assets does not.

- [ ] **Step 4: Run ingest and PDF regression suites**

Run: `uv run pytest apps/worker/tests/test_latex_priority_pipeline.py apps/worker/tests/test_pdf_upload_pipeline.py packages/py-core/tests/test_pdf_parser.py -q`  
Expected: PASS.

- [ ] **Step 5: Run formatting and type checks for touched Python packages**

Run: `uv run ruff check packages/py-core/src/alinea_core/ingest apps/worker/src/alinea_worker apps/worker/tests/test_figure_assets.py`  
Expected: PASS with no diagnostics.

- [ ] **Step 6: Commit**

```bash
git add apps/worker/src/alinea_worker/source_candidates.py apps/worker/src/alinea_worker/pipeline.py apps/worker/tests packages/py-core/src/alinea_core/ingest
git commit -m "fix: require complete figures before ingest succeeds"
```

### Task 6: OCR as the final PDF candidate

**Files:**
- Modify: `packages/py-core/src/alinea_core/parsing/pdf_parser.py`
- Create: `packages/py-core/tests/test_pdf_ocr.py`
- Modify: `apps/worker/src/alinea_worker/source_candidates.py`
- Modify: `docs/deployment.md`

- [x] **Step 1: Write failing OCR fallback tests**

```python
def test_parse_pdf_uses_ocr_textpage_when_requested(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[str, int, bool]] = []

    def fake_ocr(self: fitz.Page, *, language: str, dpi: int, full: bool):
        seen.append((language, dpi, full))
        return self.get_textpage()

    monkeypatch.setattr(fitz.Page, "get_textpage_ocr", fake_ocr)
    parsed = parse_pdf(scanned_pdf_fixture(), use_ocr=True, ocr_language="eng")
    assert seen == [("eng", 200, True)]
    assert parsed.stats["ocr"] is True


async def test_candidate_pipeline_uses_ocr_only_after_empty_text_pdf(
    scanned_pdf_candidate_runner: CandidateRunner,
) -> None:
    candidate = await scanned_pdf_candidate_runner.run()
    assert candidate.source_format == "pdf"
    assert candidate.diagnostics[-1]["candidate"] == "pdf_ocr"
    assert candidate.report.accepted
```

- [x] **Step 2: Run and confirm RED**

Run: `uv run pytest packages/py-core/tests/test_pdf_ocr.py -q`  
Expected: FAIL because `parse_pdf` has no OCR mode.

- [x] **Step 3: Thread an OCR text page through the existing parser**

Add `use_ocr: bool = False` and `ocr_language: str = "eng"` to `parse_pdf`. For each page, create `textpage = page.get_textpage_ocr(language=ocr_language, dpi=200, full=True)` only in OCR mode and pass that text page to every `page.get_text(...)` call used for block/layout extraction. Set `stats["ocr"]` and preserve the existing non-OCR path byte-for-byte.

- [x] **Step 4: Add OCR candidate ordering and deployment prerequisite**

The worker calls `parse_pdf(data)` first. It calls `parse_pdf(data, use_ocr=True)` only when the text candidate raises `no_text_layer` or fails completeness for insufficient visible text. Document the required `tesseract` binary and English language data in `docs/deployment.md`; startup readiness must report OCR unavailable without breaking non-OCR documents.

- [x] **Step 5: Run PDF and OCR tests**

Run: `uv run pytest packages/py-core/tests/test_pdf_ocr.py packages/py-core/tests/test_pdf_parser.py apps/worker/tests/test_latex_priority_pipeline.py -q`  
Expected: PASS.

- [x] **Step 6: Commit**

```bash
git add packages/py-core/src/alinea_core/parsing/pdf_parser.py packages/py-core/tests/test_pdf_ocr.py apps/worker/src/alinea_worker/source_candidates.py docs/deployment.md
git commit -m "feat: add final pdf ocr fallback"
```
