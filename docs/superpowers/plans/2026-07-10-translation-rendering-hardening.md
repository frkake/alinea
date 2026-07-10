# Translation and Rendering Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Translate every prose block, refuse false completion, and guarantee that Viewer and article text never expose raw TeX control sequences.

**Architecture:** Make translation coverage a first-class pure report shared by ingestion and retry APIs. Include appendices by default, treat untranslated units as blocking, and add a token-aware visible-text sanitizer plus a readable math fallback used by both backend source preparation and frontend renderers.

**Tech Stack:** Python 3.12, SQLAlchemy, Pydantic, LLMRouter, React/TypeScript, KaTeX, Vitest, pytest.

---

### Task 1: Full translation scope including appendices

**Files:**
- Modify: `packages/py-core/src/alinea_core/translation/pipeline.py`
- Test: `packages/py-core/tests/test_translation.py`

- [ ] **Step 1: Write failing scope tests**

```python
def test_default_scope_includes_appendix_but_excludes_references() -> None:
    content = _content_with_main_appendix_and_references()
    scope = compute_translation_scope(content)
    assert "blk-main" in scope.in_scope_block_ids
    assert "blk-appendix" in scope.in_scope_block_ids
    assert "blk-reference" not in scope.in_scope_block_ids


def test_default_initial_plan_schedules_appendix_sections() -> None:
    plan = plan_initial_translation(
        _content_with_main_appendix_and_references(),
        TranslationSettings(),
        pages=12,
    )
    assert "sec-appendix" in plan.section_ids
    assert plan.include_appendix
```

- [ ] **Step 2: Run and confirm RED**

Run: `uv run pytest packages/py-core/tests/test_translation.py -k appendix -q`  
Expected: FAIL because appendices are excluded and the default setting is false.

- [ ] **Step 3: Include appendix prose by default**

```python
@dataclass
class TranslationSettings:
    default_style: str = "natural"
    auto_translate_appendix: bool = True
    translate_table_cells: bool = True
    suggest_section_selection_over_30_pages: bool = False


def compute_translation_scope(
    content: DocumentContent | dict[str, Any], *, include_appendix: bool = True
) -> ScopeResult:
    doc = _as_content(content)
    in_scope: list[str] = []
    sections: list[dict[str, Any]] = []
    appendix_ids: list[str] = []
    reference_ids: list[str] = []

    def walk(section: Section, under_appendix: bool) -> None:
        is_appendix = under_appendix or _is_appendix_heading(
            section.heading.number, section.heading.title
        )
        is_reference = _is_reference_section(section)
        if is_appendix:
            appendix_ids.append(section.id)
        if is_reference:
            reference_ids.append(section.id)
        own: list[str] = []
        if not is_reference and (include_appendix or not is_appendix):
            for block in section.blocks:
                if block.type in TRANSLATABLE_BLOCK_TYPES:
                    in_scope.append(block.id)
                    own.append(block.id)
        if own:
            sections.append({"section_id": section.id, "block_ids": own})
        for child in section.sections:
            walk(child, is_appendix)

    for top in doc.sections:
        walk(top, False)
    return ScopeResult(
        in_scope_block_ids=in_scope,
        sections=sections,
        appendix_section_ids=appendix_ids,
        reference_section_ids=reference_ids,
    )
```

Preserve explicit user opt-out by passing `include_appendix=settings.auto_translate_appendix` when a user has stored that setting, while the absent/default value is full translation.

- [ ] **Step 4: Run translation scope tests**

Run: `uv run pytest packages/py-core/tests/test_translation.py -q`  
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/py-core/src/alinea_core/translation/pipeline.py packages/py-core/tests/test_translation.py
git commit -m "feat: translate appendices by default"
```

### Task 2: Translation completeness report and truthful completion

**Files:**
- Create: `packages/py-core/src/alinea_core/translation/completeness.py`
- Modify: `packages/py-core/src/alinea_core/ingest/progress.py`
- Modify: `packages/py-core/src/alinea_core/translation/pipeline.py`
- Create: `packages/py-core/tests/test_translation_completeness.py`
- Create: `packages/py-core/tests/test_ingest_progress.py`

- [ ] **Step 1: Write failing pure coverage tests**

```python
from alinea_core.translation.completeness import assess_translation_completeness


def test_untranslated_and_missing_units_block_completion() -> None:
    report = assess_translation_completeness(
        expected_ids=["a", "b", "c"],
        units={
            "a": UnitState(text_ja="訳文", quality_flags=[]),
            "b": UnitState(text_ja="source text", quality_flags=["untranslated"]),
        },
        abstract_source="English abstract",
        abstract_ja="日本語要旨",
    )
    assert not report.complete
    assert report.missing_ids == ("c",)
    assert report.blocked_ids == ("b",)
```

Add a database test proving `finalize_ingest_if_body_complete` leaves the translation set partial and the ingest job running/failed when coverage is incomplete.

- [ ] **Step 2: Run and confirm RED**

Run: `uv run pytest packages/py-core/tests/test_translation_completeness.py packages/py-core/tests/test_ingest_progress.py -q`  
Expected: FAIL because the report does not exist and finalization checks only active jobs.

- [ ] **Step 3: Implement the report and make untranslated blocking**

```python
BLOCKING_FLAGS = frozenset(
    {
        "placeholder_mismatch",
        "provider_refusal",
        "context_overflow",
        "number_mismatch",
        "glossary_violation",
        "untranslated",
    }
)


@dataclass(frozen=True)
class TranslationCompleteness:
    complete: bool
    expected_count: int
    valid_count: int
    missing_ids: tuple[str, ...]
    blocked_ids: tuple[str, ...]
    abstract_complete: bool


def assess_translation_completeness(
    expected_ids,
    units,
    *,
    abstract_source: str,
    abstract_ja: str | None,
) -> TranslationCompleteness:
    expected = set(expected_ids)
    missing = sorted(expected - set(units))
    blocked = sorted(
        block_id for block_id, unit in units.items()
        if block_id in expected and (
            not unit.text_ja.strip() or set(unit.quality_flags) & BLOCKING_FLAGS
        )
    )
    valid = expected - set(missing) - set(blocked)
    abstract_complete = not abstract_source.strip() or bool((abstract_ja or "").strip())
    return TranslationCompleteness(
        not missing and not blocked and abstract_complete,
        len(expected),
        len(valid),
        tuple(missing),
        tuple(blocked),
        abstract_complete,
    )
```

- [ ] **Step 4: Gate `finalize_ingest_if_body_complete` with stored units**

Load expected IDs from `compute_translation_scope(content)`, load all units for `set_id`, and load `Paper.abstract`/`abstract_ja` through the revision. Call the report after active jobs reach zero. If body or abstract is incomplete, set `TranslationSet.status = "partial"`, leave the parent ingest job non-successful at `translating_body`, and append a `translation_incomplete` log entry with counts and IDs.

- [ ] **Step 5: Run focused tests**

Run: `uv run pytest packages/py-core/tests/test_translation_completeness.py packages/py-core/tests/test_ingest_progress.py packages/py-core/tests/test_translation.py -q`  
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add packages/py-core/src/alinea_core/translation packages/py-core/src/alinea_core/ingest/progress.py packages/py-core/tests
git commit -m "fix: require complete translations before success"
```

### Task 3: Repair retries instead of source-text fallback

**Files:**
- Modify: `packages/py-core/src/alinea_core/translation/pipeline.py`
- Modify: `apps/worker/src/alinea_worker/tasks/translate.py`
- Test: `packages/py-core/tests/test_translation.py`
- Test: `apps/worker/tests/test_translate_units.py`

- [ ] **Step 1: Add a failing provider-escalation test**

```python
async def test_failed_batch_is_repaired_without_persisting_source_text(
    repair_translation_fixture: RepairTranslationFixture,
) -> None:
    router = SequenceRouter([
        placeholder_broken_response(),
        placeholder_broken_response(),
        valid_translation_response("修復された訳文"),
    ])
    units = await repair_translation_fixture.translate(router=router)
    assert units[0].text_ja == "修復された訳文"
    assert "untranslated" not in units[0].quality_flags
    assert router.tasks[-1] == "retranslation_escalation"
```

- [ ] **Step 2: Run and confirm RED**

Run: `uv run pytest packages/py-core/tests/test_translation.py -k repaired_without -q`  
Expected: FAIL because the current final fallback persists source text with `untranslated`.

- [ ] **Step 3: Add explicit repair output instead of deliverable source fallback**

```python
@dataclass
class TranslationBatchResult:
    units: list[TranslatedUnit]
    failed_block_ids: list[str]


def failed_unit(block: BlockToTranslate, flag: str) -> TranslatedUnit:
    return TranslatedUnit(
        block_id=block.encoded.block_id,
        content_ja=[],
        text_ja="",
        quality_flags=[flag],
    )
```

The initial task must submit failed block IDs through the `retranslation_escalation` route with a repair prompt containing source, preserved tokens, and the previous validation error. Only a validated repaired unit may be upserted as displayable text.

- [ ] **Step 4: Keep failed units retryable in worker state**

When repair remains blocked, finish the section job with a structured partial result, keep the translation set partial, and make `/retry-failed` select empty or blocking units. Do not mark the parent ingest job succeeded.

- [ ] **Step 5: Run worker and py-core translation tests**

Run: `uv run pytest packages/py-core/tests/test_translation.py apps/worker/tests/test_translate_units.py apps/api/tests/test_viewer_api.py -q`  
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add packages/py-core/src/alinea_core/translation/pipeline.py apps/worker/src/alinea_worker/tasks/translate.py packages/py-core/tests/test_translation.py apps/worker/tests/test_translate_units.py
git commit -m "fix: repair failed translation units"
```

### Task 4: Token-aware visible TeX sanitizer

**Files:**
- Create: `packages/py-core/src/alinea_core/document/visible_text.py`
- Modify: `packages/py-core/src/alinea_core/parsing/latex_parser.py`
- Modify: `packages/py-core/src/alinea_core/article/sources.py`
- Create: `packages/py-core/tests/test_visible_text.py`
- Test: `packages/py-core/tests/test_latex_parser.py`

- [ ] **Step 1: Write failing sanitizer tests**

```python
from alinea_core.document.visible_text import sanitize_visible_tex


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (r"\@setfontsize\@ixpt\@xpt 6@ Caption", "Caption"),
        (r"\textbf{Important result}", "Important result"),
        (r"author X. \bibinfo{volume}{B12}", "author X. B12"),
        ("\x1astartsectionparagraph Body", "Body"),
    ],
)
def test_sanitizes_visible_tex_without_losing_content(raw: str, expected: str) -> None:
    value = sanitize_visible_tex(raw)
    assert expected in value
    assert not re.search(r"\\[A-Za-z@]+", value)
```

- [ ] **Step 2: Run and confirm RED**

Run: `uv run pytest packages/py-core/tests/test_visible_text.py -q`  
Expected: FAIL because the module does not exist.

- [ ] **Step 3: Implement a balanced-token sanitizer**

Implement a scanner that reads control words and balanced groups. Formatting commands retain their last braced content, bibliography commands retain value groups, internal declarations and dimensions are dropped, and ordinary text is preserved. Do not add source-paper IDs or titles.

```python
def sanitize_visible_tex(value: str) -> str:
    tokens = tokenize_tex(value.replace("\x1a", " "))
    out = VisibleTextReducer().reduce(tokens)
    return normalize_whitespace(out)
```

- [ ] **Step 4: Apply sanitizer at parser and article-source boundaries**

Sanitize only prose fields (`text`, headings, captions, reference display strings). Preserve `math_inline` and equation LaTeX for the math renderer. Run the same sanitizer when building `ArticleSources` so legacy revisions cannot leak raw commands into newly generated articles.

- [ ] **Step 5: Run parser, article-source, and sanitizer tests**

Run: `uv run pytest packages/py-core/tests/test_visible_text.py packages/py-core/tests/test_latex_parser.py packages/py-core/tests/test_article_sources.py -q`  
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add packages/py-core/src/alinea_core/document/visible_text.py packages/py-core/src/alinea_core/parsing/latex_parser.py packages/py-core/src/alinea_core/article/sources.py packages/py-core/tests
git commit -m "fix: sanitize visible tex control sequences"
```

### Task 5: Readable frontend math fallback

**Files:**
- Modify: `apps/web/src/lib/katex-render.ts`
- Modify: `apps/web/src/lib/katex-render.test.ts`
- Modify: `apps/web/src/components/viewer/article/markdown.test.tsx`

- [ ] **Step 1: Write failing fallback tests**

```typescript
test("never exposes raw control sequences when KaTeX rejects input", () => {
  const html = renderBlockMath(String.raw`\begin{broken} \mystery{x}`);
  expect(html).toContain("alinea-math-fallback");
  expect(html).not.toMatch(/\\[A-Za-z@]+/);
  expect(html).toContain("mystery");
});

test("uses document macros without a paper-specific registry", () => {
  const html = renderInlineMath(String.raw`\foo{x}`, { "\\foo": "\\operatorname{foo}" });
  expect(html).toContain("katex");
  expect(html).not.toContain("alinea-math-fallback");
});
```

- [ ] **Step 2: Run and confirm RED**

Run: `pnpm --filter @alinea/web test --run src/lib/katex-render.test.ts`  
Expected: FAIL because fallback HTML contains the original LaTeX and render functions do not accept document macros.

- [ ] **Step 3: Implement generic readable fallback and macro argument**

```typescript
export function renderMath(
  latex: string,
  displayMode: boolean,
  macros: Record<string, string> = {},
): string {
  try {
    return katex.renderToString(prepareLatex(latex), { displayMode, throwOnError: true, macros });
  } catch {
    const readable = latexToReadableMath(latex);
    const tag = displayMode ? "div" : "span";
    return `<${tag} class="alinea-math-fallback">${escapeHtml(readable)}</${tag}>`;
  }
}
```

`latexToReadableMath` strips control backslashes, maps structural commands to their arguments, and converts common TeX symbol commands through KaTeX's symbol table or a small standard-TeX map. It must not contain paper-specific macro names.

- [ ] **Step 4: Run web renderer tests**

Run: `pnpm --filter @alinea/web test --run src/lib/katex-render.test.ts src/components/viewer/article/markdown.test.tsx`  
Expected: PASS.

- [ ] **Step 5: Run TypeScript checks and commit**

Run: `pnpm --filter @alinea/web typecheck`  
Expected: PASS.

```bash
git add apps/web/src/lib/katex-render.ts apps/web/src/lib/katex-render.test.ts apps/web/src/components/viewer/article/markdown.test.tsx
git commit -m "fix: render readable math without raw latex"
```
