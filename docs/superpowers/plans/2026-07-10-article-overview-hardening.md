# Article and Overview Figure Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate articles only from complete structured bodies and always persist a valid overview figure without leaking raw TeX.

**Architecture:** Reuse document and translation completeness reports as an article readiness gate. Keep LLM DSL generation as the preferred overview path, but generate a deterministic three-card DSL from normalized article sources whenever the provider or schema request fails.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy, LLMRouter, alinea_figures DSL/SVG renderer, pytest.

---

### Task 1: Article readiness gate

**Files:**
- Create: `packages/py-core/src/alinea_core/article/readiness.py`
- Modify: `apps/api/src/alinea_api/routers/articles.py`
- Modify: `apps/worker/src/alinea_worker/tasks/generate_article.py`
- Create: `packages/py-core/tests/test_article_readiness.py`
- Test: `apps/api/tests/test_articles.py`

- [ ] **Step 1: Write failing readiness tests**

```python
from alinea_core.article.readiness import assess_article_readiness


def test_rejects_revision_with_no_meaningful_body() -> None:
    report = assess_article_readiness(
        revision=_revision(blocks=1, translatable_blocks=1, completeness={"accepted": False}),
        content=_content_with_text("paper.pdf"),
    )
    assert not report.ready
    assert report.code == "article_source_incomplete"


def test_accepts_complete_revision() -> None:
    report = assess_article_readiness(
        revision=_revision(blocks=42, translatable_blocks=25, completeness={"accepted": True}),
        content=_content_with_paragraphs(4),
    )
    assert report.ready
```

Add an API test asserting `POST /api/library-items/{id}/article` returns problem code `article_source_incomplete` and enqueues no job for an incomplete revision.

- [ ] **Step 2: Run and confirm RED**

Run: `uv run pytest packages/py-core/tests/test_article_readiness.py apps/api/tests/test_articles.py -k incomplete -q`  
Expected: FAIL because article generation currently accepts metadata-only revisions.

- [ ] **Step 3: Implement the pure readiness report**

```python
@dataclass(frozen=True)
class ArticleReadiness:
    ready: bool
    code: str | None
    body_chars: int
    translatable_blocks: int


def assess_article_readiness(revision: DocumentRevision, content: DocumentContent) -> ArticleReadiness:
    completeness = (revision.stats or {}).get("completeness") or {}
    prose = "\n".join(
        sanitize_visible_tex(block_to_plain(block))
        for _section, block in content.iter_blocks()
        if block.type in TRANSLATABLE_BLOCK_TYPES
    ).strip()
    block_count = int((revision.stats or {}).get("translatable_blocks") or 0)
    ready = bool(completeness.get("accepted")) and block_count >= 2 and len(prose) >= 200
    return ArticleReadiness(ready, None if ready else "article_source_incomplete", len(prose), block_count)
```

- [ ] **Step 4: Gate both API enqueue and worker execution**

The API returns HTTP 409 with a retryable problem pointing to re-ingestion. The worker repeats the gate to protect old queued jobs and fails them with the same code. Do not create an `Article` row or placeholder blocks.

- [ ] **Step 5: Run article API and readiness tests**

Run: `uv run pytest packages/py-core/tests/test_article_readiness.py apps/api/tests/test_articles.py apps/worker/tests/test_generate_article.py -q`  
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add packages/py-core/src/alinea_core/article/readiness.py packages/py-core/tests/test_article_readiness.py apps/api/src/alinea_api/routers/articles.py apps/api/tests/test_articles.py apps/worker/src/alinea_worker/tasks/generate_article.py
git commit -m "fix: block articles from incomplete sources"
```

### Task 2: Normalized article source contract

**Files:**
- Modify: `packages/py-core/src/alinea_core/article/sources.py`
- Test: `packages/py-core/tests/test_article_sources.py`

- [ ] **Step 1: Write a failing source-cleanliness test**

```python
async def test_article_sources_contain_no_visible_tex_commands(
    article_source_fixture: ArticleSourceFixture,
) -> None:
    sources = await article_source_fixture.collect(revision=_revision_with_raw_tex())
    combined = "\n".join(
        [sources.summary_text, sources.bibliography_text, sources.figures_text, sources.body_text]
    )
    assert not re.search(r"\\[A-Za-z@]+", combined)
    assert "Important result" in combined
    assert "B12" in combined
```

- [ ] **Step 2: Run and confirm RED**

Run: `uv run pytest packages/py-core/tests/test_article_sources.py -k no_visible_tex -q`  
Expected: FAIL with raw style, bibliography, or custom math commands.

- [ ] **Step 3: Sanitize only visible prose and keep structured math separately**

Use `sanitize_visible_tex` from the translation/rendering plan on headings, captions, paragraph plain text, and reference display text. For source equations, keep the existing math-specific field and provide a readable normalized display string rather than injecting raw TeX into prose.

```python
def _clean_source_text(value: str) -> str:
    return sanitize_visible_tex(value).strip()
```

- [ ] **Step 4: Run article source and postprocess tests**

Run: `uv run pytest packages/py-core/tests/test_article_sources.py packages/py-core/tests/test_article_postprocess.py -q`  
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/py-core/src/alinea_core/article/sources.py packages/py-core/tests/test_article_sources.py
git commit -m "fix: normalize article source text"
```

### Task 3: Deterministic overview DSL fallback

**Files:**
- Modify: `apps/worker/src/alinea_worker/tasks/generate_overview_figure.py`
- Test: `apps/worker/tests/test_generate_overview_figure.py`

- [ ] **Step 1: Write failing provider-error fallback tests**

```python
async def test_overview_uses_deterministic_svg_when_provider_rejects_schema(
    db_session: AsyncSession,
    overview_fixture: OverviewFixture,
) -> None:
    router = RejectingRouter(ProviderChainExhausted("invalid_request"))
    row = await create_overview_figure_v1(
        {"router": router, "s3": overview_fixture.storage}, db_session,
        article=overview_fixture.article,
        sources=overview_fixture.sources,
        user=overview_fixture.user,
        job=overview_fixture.job,
    )
    assert row.render_mode == "svg"
    assert row.dsl["cards"][0]["label"] == "課題"
    assert row.dsl["cards"][1]["label"].startswith("提案")
    assert row.dsl["cards"][2]["label"] == "結果"
    assert row.provider == "deterministic"
    assert await overview_fixture.storage.exists(
        overview_fixture.storage.assets_bucket, row.svg_storage_key
    )
```

- [ ] **Step 2: Run and confirm RED**

Run: `uv run pytest apps/worker/tests/test_generate_overview_figure.py -k deterministic -q`  
Expected: FAIL because provider errors currently escape and no overview row is created.

- [ ] **Step 3: Build fallback cards from normalized source evidence**

```python
def build_deterministic_overview(
    sources: ArticleSources, *, date: str
) -> tuple[OverviewFigureDsl, list[str]]:
    claims = deterministic_claims(sources)
    cards = [
        Card(role="problem", tone="neutral", label="課題", heading=short_heading(claims[0]), body=short_body(claims[0])),
        Card(role="proposal", tone="accent", label="提案", heading=short_heading(claims[1]), body=short_body(claims[1])),
        Card(role="result", tone="green", label="結果", heading=short_heading(claims[2]), body=short_body(claims[2])),
    ]
    evidence = list(dict.fromkeys([*sources.block_ids, *sources.section_ids]))[:4]
    return OverviewFigureDsl(
        cards=cards,
        connectors=[Connector(**{"from": 0, "to": 1}), Connector(**{"from": 1, "to": 2})],
        footer=OverviewFigureFooter(generated_by=FOOTER_GENERATED_BY, date=date),
    ), evidence


def deterministic_claims(sources: ArticleSources) -> tuple[str, str, str]:
    sentences = [
        sanitize_visible_tex(part).strip()
        for part in re.split(r"(?<=[。.!?])\s+|\n+", "\n".join([sources.summary_text, sources.body_text]))
        if sanitize_visible_tex(part).strip()
    ]
    while len(sentences) < 3:
        sentences.append("本文から要点を確認してください。")
    return sentences[0], sentences[1], sentences[2]


def short_heading(value: str) -> str:
    return value[:36].rstrip("。、,. ") or "要点"


def short_body(value: str) -> str:
    return value[:80].strip() or "本文から要点を確認してください。"
```

Selection uses summary slots and block roles/order, never a paper ID, title, author, or method-name lookup table. `short_heading` and `short_body` enforce existing DSL length limits deterministically.

- [ ] **Step 4: Catch LLM/provider/schema failures around DSL generation**

```python
try:
    generated, response = await generate_overview_dsl_with_retry(
        router,
        material_text=material_text,
        job=job,
        current_dsl=current.dsl if current is not None else None,
        instruction=instruction,
    )
    render_dsl = generated.to_render_dsl(generated_by=FOOTER_GENERATED_BY, date=date_str)
    provider = response.provider
    model = response.model
except (ProviderChainExhausted, OverviewFigureGenerationError, ValueError) as exc:
    render_dsl, evidence = build_deterministic_overview(sources, date=date_str)
    provider, model = "deterministic", "overview-v1"
```

Persist the same `OverviewFigure` and SVG contract for both paths and log the provider failure as a recovered warning, not an article partial failure.

- [ ] **Step 5: Run overview tests**

Run: `uv run pytest apps/worker/tests/test_generate_overview_figure.py -q`  
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add apps/worker/src/alinea_worker/tasks/generate_overview_figure.py apps/worker/tests/test_generate_overview_figure.py
git commit -m "fix: add deterministic overview figure fallback"
```

### Task 4: Article generation treats overview fallback as success

**Files:**
- Modify: `apps/worker/src/alinea_worker/tasks/generate_article.py`
- Test: `apps/worker/tests/test_generate_article.py`

- [ ] **Step 1: Add a failing end-to-end article job test**

```python
async def test_article_job_succeeds_with_overview_when_dsl_provider_fails(
    article_job_fixture: ArticleJobFixture,
) -> None:
    await run_article_job(
        article_job_fixture.ctx_with_rejecting_overview_router,
        article_job_fixture.store,
        article_job_fixture.job,
    )
    completed = await article_job_fixture.store.get(str(article_job_fixture.job.id))
    overview = await _current_overview(article_job_fixture.session, str(completed.article_id))
    assert completed.status == "succeeded"
    assert overview is not None
    assert overview.provider == "deterministic"
    assert not any(entry.get("error", {}).get("reason") == "overview_figure_generation_failed" for entry in completed.log)
```

- [ ] **Step 2: Run and confirm RED**

Run: `uv run pytest apps/worker/tests/test_generate_article.py -k overview_when -q`  
Expected: FAIL because `_generate_overview_figure_v1` records a partial failure and no row.

- [ ] **Step 3: Narrow article-level exception handling**

`create_overview_figure_v1` now owns recoverable provider failures. Keep the outer article catch only for storage/database/programming errors, which must fail the article job rather than silently claim full success. On same-preset regeneration, call `create_overview_figure_v1` when no current overview row exists; preserve the current overview only when it actually exists.

- [ ] **Step 4: Run article and overview suites**

Run: `uv run pytest apps/worker/tests/test_generate_article.py apps/worker/tests/test_generate_overview_figure.py -q`  
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/worker/src/alinea_worker/tasks/generate_article.py apps/worker/tests/test_generate_article.py
git commit -m "fix: guarantee article overview generation"
```
