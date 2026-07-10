# Japanese PDF Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce a validated, fully translated Japanese PDF for every complete translation set regardless of source format.

**Architecture:** Keep source-preserving LaTeX replacement as the preferred renderer, then fall back to a deterministic structured-document LuaLaTeX renderer for mapping failures, HTML, PDF, and OCR revisions. Both renderers emit a block manifest and pass the same coverage, raw-TeX, Japanese-text, and page-bound validators before publication.

**Tech Stack:** Python 3.12, LuaLaTeX/TeX Live container, PyMuPDF, SQLAlchemy, S3-compatible storage, pytest.

---

### Task 1: Structured Japanese document renderer

**Files:**
- Create: `apps/worker/src/alinea_worker/structured_pdf.py`
- Create: `apps/worker/tests/test_structured_pdf.py`

- [ ] **Step 1: Write failing render-source tests**

```python
from alinea_worker.structured_pdf import render_structured_japanese_source


def test_renders_all_translated_block_types_and_manifest() -> None:
    content, units = fixture_content_and_units(
        types=["heading", "paragraph", "list", "figure", "table", "footnote", "equation"]
    )
    rendered = render_structured_japanese_source(content, units, abstract_ja="日本語要旨")
    assert rendered.main_tex_name == "main.tex"
    assert "日本語要旨" in rendered.main_tex
    assert "日本語本文" in rendered.main_tex
    assert r"\includegraphics" in rendered.main_tex
    assert r"\begin{tabular}" in rendered.main_tex
    assert r"E = mc^2" in rendered.main_tex
    assert rendered.manifest.translated_block_ids == expected_translated_ids(content)


def test_never_writes_raw_visible_tex_from_translation_text() -> None:
    rendered = render_structured_japanese_source(
        _paragraph_content(),
        {"p1": _unit(r"結果 \@setfontsize \textbf{重要}")},
    )
    assert "重要" in rendered.main_tex
    assert r"\@setfontsize" not in rendered.main_tex
    assert r"\textbf{重要}" not in rendered.main_tex
```

- [ ] **Step 2: Run and confirm RED**

Run: `uv run pytest apps/worker/tests/test_structured_pdf.py -q`  
Expected: FAIL because `structured_pdf` does not exist.

- [ ] **Step 3: Implement focused render types**

```python
@dataclass(frozen=True)
class PdfRenderManifest:
    expected_block_ids: frozenset[str]
    translated_block_ids: frozenset[str]
    source_fallback_block_ids: frozenset[str]


@dataclass(frozen=True)
class StructuredLatexSource:
    main_tex_name: str
    main_tex: str
    support_text_files: dict[str, str]
    binary_files: dict[str, bytes]
    manifest: PdfRenderManifest


class AssetLoader(Protocol):
    def load(self, asset_key: str) -> bytes:
        raise NotImplementedError


@dataclass(frozen=True)
class RenderedBlock:
    tex: str
    translated: bool
    source_fallback: bool
```

Use a generic LuaLaTeX template with `luatexja-fontspec`, `graphicx`, `longtable`, `booktabs`, `hyperref`, `amsmath`, and page-safe defaults. Render prose through `sanitize_visible_tex` then TeX-escape it; render only structured `math_inline`/equation fields as math. Copy resolved figure bytes to stable local names.

- [ ] **Step 4: Render each block through a type dispatcher**

```python
def render_block(block: Block, unit: TranslationUnit | None, assets: AssetLoader) -> RenderedBlock:
    if block.type in TRANSLATABLE_BLOCK_TYPES:
        if unit is None or not unit.text_ja.strip() or set(unit.quality_flags) & BLOCKING_FLAGS:
            return RenderedBlock(tex="", translated=False, source_fallback=True)
        return render_translated_block(block, unit, assets)
    return render_nontranslatable_block(block, assets)
```

Reference entries preserve their bibliographic language but pass through `sanitize_visible_tex` and TeX escaping; equations and code preserve their structured source fields. These block types do not enter `source_fallback_block_ids` because they are outside translation scope.

- [ ] **Step 5: Run structured renderer tests**

Run: `uv run pytest apps/worker/tests/test_structured_pdf.py -q`  
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add apps/worker/src/alinea_worker/structured_pdf.py apps/worker/tests/test_structured_pdf.py
git commit -m "feat: render japanese pdf from structured documents"
```

### Task 2: Shared PDF completeness validators

**Files:**
- Modify: `apps/worker/src/alinea_worker/latex_pdf.py`
- Test: `apps/worker/tests/test_latex_pdf.py`
- Test: `apps/worker/tests/test_structured_pdf.py`

- [ ] **Step 1: Write failing manifest and raw-control tests**

```python
def test_manifest_rejects_source_fallback_and_missing_blocks() -> None:
    manifest = PdfRenderManifest(
        expected_block_ids=frozenset({"a", "b"}),
        translated_block_ids=frozenset({"a"}),
        source_fallback_block_ids=frozenset({"b"}),
    )
    with pytest.raises(LatexPdfBuildError) as exc:
        _validate_render_manifest(manifest)
    assert exc.value.kind == "translated_pdf_incomplete"


def test_pdf_visible_text_rejects_raw_tex_control_words() -> None:
    pdf = pdf_with_text(r"日本語本文 \@setfontsize")
    with pytest.raises(LatexPdfBuildError) as exc:
        _validate_translated_pdf(pdf)
    assert exc.value.kind == "visible_latex"
```

- [ ] **Step 2: Run and confirm RED**

Run: `uv run pytest apps/worker/tests/test_latex_pdf.py -k 'manifest or visible_text' -q`  
Expected: FAIL because manifest and visible-control validation do not exist.

- [ ] **Step 3: Implement shared validation**

```python
VISIBLE_TEX_CONTROL_RE = re.compile(r"\\[A-Za-z@]{2,}")


def _validate_render_manifest(manifest: PdfRenderManifest) -> None:
    missing = manifest.expected_block_ids - manifest.translated_block_ids
    if missing or manifest.source_fallback_block_ids:
        raise LatexPdfBuildError(
            "translated_pdf_incomplete",
            "not every translatable block was rendered in Japanese",
            detail={"missing": sorted(missing), "fallback": sorted(manifest.source_fallback_block_ids)},
        )


def _validate_translated_pdf(pdf_bytes: bytes) -> None:
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    if doc.page_count < 1:
        raise LatexPdfBuildError("invalid_pdf", "compiled PDF has no pages")
    visible_text = "\n".join(doc.load_page(i).get_text("text") for i in range(doc.page_count))
    if not _JAPANESE_RE.search(visible_text):
        raise LatexPdfBuildError("missing_japanese_text", "compiled PDF has no Japanese text")
    if VISIBLE_TEX_CONTROL_RE.search(visible_text):
        raise LatexPdfBuildError("visible_latex", "compiled PDF exposes TeX control sequences")
    violations = _find_pdf_page_bound_violations(doc)
    doc.close()
    if violations:
        raise LatexPdfBuildError("page_bounds", "compiled PDF has content outside page bounds", detail={"violations": violations[:20]})
```

Coverage comes from the render manifest rather than a broad English-character heuristic, so references, formulas, code, and proper names remain valid.

- [ ] **Step 4: Run PDF validator tests**

Run: `uv run pytest apps/worker/tests/test_latex_pdf.py apps/worker/tests/test_structured_pdf.py -q`  
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/worker/src/alinea_worker/latex_pdf.py apps/worker/tests/test_latex_pdf.py apps/worker/tests/test_structured_pdf.py
git commit -m "fix: validate japanese pdf translation coverage"
```

### Task 3: Source-preserving renderer emits a manifest

**Files:**
- Modify: `apps/worker/src/alinea_worker/latex_pdf.py`
- Test: `apps/worker/tests/test_latex_pdf.py`

- [ ] **Step 1: Write a failing replacement-manifest test**

```python
def test_source_renderer_manifest_matches_translated_scope() -> None:
    rendered = render_translated_latex_source(archive, content, units)
    assert rendered.manifest.expected_block_ids == frozenset(units)
    assert rendered.manifest.translated_block_ids == frozenset(units)
    assert not rendered.manifest.source_fallback_block_ids
```

- [ ] **Step 2: Run and confirm RED**

Run: `uv run pytest apps/worker/tests/test_latex_pdf.py -k replacement_manifest -q`  
Expected: FAIL because `RenderedLatexSource` does not expose a manifest.

- [ ] **Step 3: Add manifest fields while preserving existing counters**

Populate `expected_block_ids` from complete translation scope and `translated_block_ids` from `replaced_block_ids`. `_validate_render_coverage` becomes a thin call to `_validate_render_manifest`, retaining existing detailed warnings in the raised error.

- [ ] **Step 4: Run source-renderer tests**

Run: `uv run pytest apps/worker/tests/test_latex_pdf.py -q`  
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/worker/src/alinea_worker/latex_pdf.py apps/worker/tests/test_latex_pdf.py
git commit -m "refactor: track translated pdf render manifest"
```

### Task 4: Generic builder with structured fallback

**Files:**
- Modify: `apps/worker/src/alinea_worker/latex_pdf.py`
- Modify: `apps/worker/src/alinea_worker/tasks/translate.py`
- Modify: `apps/worker/src/alinea_worker/latex_pdf_backfill.py`
- Test: `apps/worker/tests/test_latex_pdf.py`
- Test: `apps/worker/tests/test_translate_units.py`

- [ ] **Step 1: Write failing non-LaTeX and mapping-failure tests**

```python
async def test_builds_structured_pdf_for_html_revision(
    html_pdf_build_fixture: PdfBuildFixture,
) -> None:
    outcome = await build_translation_pdfs_if_ready(
        html_pdf_build_fixture.session,
        html_pdf_build_fixture.storage,
        html_pdf_build_fixture.settings,
        set_id=html_pdf_build_fixture.set_id,
    )
    assert outcome.built
    assert outcome.renderer == "structured"
    assert await html_pdf_build_fixture.storage.exists(
        html_pdf_build_fixture.storage.sources_bucket, outcome.translated_key
    )


async def test_mapping_failure_falls_back_to_structured_renderer(
    mapping_failure_pdf_build_fixture: PdfBuildFixture,
) -> None:
    outcome = await build_translation_pdfs_if_ready(
        mapping_failure_pdf_build_fixture.session,
        mapping_failure_pdf_build_fixture.storage,
        mapping_failure_pdf_build_fixture.settings,
        set_id=mapping_failure_pdf_build_fixture.set_id,
    )
    assert outcome.built
    assert outcome.renderer == "structured"
    assert outcome.fallback_reason == "translation_mapping_incomplete"
```

- [ ] **Step 2: Run and confirm RED**

Run: `uv run pytest apps/worker/tests/test_latex_pdf.py -k 'html_revision or mapping_failure' -q`  
Expected: FAIL because the builder skips non-LaTeX revisions and propagates mapping errors.

- [ ] **Step 3: Rename and generalize the builder**

Extend `LatexPdfBuildOutcome` with `renderer: str | None` and `fallback_reason: str | None`, then implement:

```python
async def build_translation_pdfs_if_ready(
    session: AsyncSession,
    storage: S3Storage,
    settings: CoreSettings,
    *,
    set_id: str,
) -> LatexPdfBuildOutcome:
    tset, revision, paper, content, units = await _load_pdf_build_context(session, set_id)
    _require_complete_translation_set(
        content,
        units,
        abstract_source=paper.abstract,
        abstract_ja=paper.abstract_ja,
    )
    if revision.source_format == "latex":
        try:
            rendered = await _render_source_preserving(
                session=session,
                storage=storage,
                revision=revision,
                content=content,
                units=units,
                abstract_ja=paper.abstract_ja,
            )
            renderer = "source"
        except LatexPdfBuildError as exc:
            if exc.kind not in {"translation_mapping_incomplete", "source_revision_mismatch", "compile_failed"}:
                raise
            rendered = await _render_structured(
                storage=storage,
                revision=revision,
                content=content,
                units=units,
                abstract_ja=paper.abstract_ja,
            )
            renderer = "structured"
            fallback_reason = exc.kind
    else:
        rendered = await _render_structured(
            storage=storage,
            revision=revision,
            content=content,
            units=units,
            abstract_ja=paper.abstract_ja,
        )
        renderer = "structured"
```

Compile both through the same TeX Live path, validate manifest before compilation and PDF bytes after compilation, then store the same `translated_pdf` asset kind and API-visible key.

- [ ] **Step 4: Update translation task and backfill callers**

Replace `build_latex_translation_pdfs_if_ready` imports/calls with `build_translation_pdfs_if_ready`. Store `renderer`, `fallback_reason`, manifest digest, and build version in revision stats.

- [ ] **Step 5: Run worker PDF and translation suites**

Run: `uv run pytest apps/worker/tests/test_latex_pdf.py apps/worker/tests/test_translate_units.py -q`  
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add apps/worker/src/alinea_worker/latex_pdf.py apps/worker/src/alinea_worker/tasks/translate.py apps/worker/src/alinea_worker/latex_pdf_backfill.py apps/worker/tests
git commit -m "feat: build japanese pdf for every source format"
```

### Task 5: Real compilation and page-geometry regression

**Files:**
- Modify: `apps/worker/tests/test_latex_pdf.py`
- Modify: `apps/worker/tests/test_structured_pdf.py`

- [ ] **Step 1: Add a TeX-container integration test**

```python
@pytest.mark.integration
async def test_structured_japanese_pdf_compiles_without_bounds_or_english_fallback() -> None:
    rendered = render_structured_japanese_source(complex_content(), complete_units())
    _validate_render_manifest(rendered.manifest)
    pdf = await _compile_rendered_source(
        rendered, image=DEFAULT_TEXLIVE_IMAGE, timeout_s=120
    )
    _validate_translated_pdf(pdf)
    with fitz.open(stream=pdf, filetype="pdf") as doc:
        assert doc.page_count >= 2
        assert not _find_pdf_page_bound_violations(doc)
        text = "\n".join(page.get_text() for page in doc)
        assert "日本語本文" in text
        assert "Original untranslated paragraph" not in text
```

- [ ] **Step 2: Run and confirm RED before completing template dependencies**

Run: `uv run pytest apps/worker/tests/test_structured_pdf.py -m integration -q`  
Expected: FAIL until every package/font/template dependency is correct.

- [ ] **Step 3: Make the TeX image/template self-contained**

Add only packages already installed by `docker/texlive/Dockerfile`, or update that Dockerfile and its smoke document in the same change. Ensure long URLs, tables, equations, and captions wrap or use explicit scroll-free page-safe TeX constructs.

- [ ] **Step 4: Rebuild TeX image and rerun integration test**

Run: `docker build -t alinea-texlive:local docker/texlive && ALINEA_TEXLIVE_IMAGE=alinea-texlive:local uv run pytest apps/worker/tests/test_structured_pdf.py -m integration -q`  
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/worker/tests/test_latex_pdf.py apps/worker/tests/test_structured_pdf.py docker/texlive
git commit -m "test: verify structured japanese pdf compilation"
```
