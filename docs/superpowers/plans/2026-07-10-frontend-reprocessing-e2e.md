# Frontend Reprocessing and arXiv E2E Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose truthful recovery controls in the frontend, reprocess existing library items without data loss, and prove all requirements through a corpus-driven browser test.

**Architecture:** Add completeness diagnostics to the existing Viewer API without changing navigation, harden image/error states, and invalidate all affected queries on job completion. A generic Playwright harness reads an external corpus file, operates only through extension/Viewer controls, and performs DOM, asset, article, layout, and PDF audits.

**Tech Stack:** FastAPI/OpenAPI, React/Next.js, TanStack Query, Playwright, Chromium extension testing, PyMuPDF audit helper, Vitest, pytest.

---

### Task 1: Viewer completeness diagnostics

**Files:**
- Modify: `apps/api/src/alinea_api/schemas/viewer.py`
- Modify: `apps/api/src/alinea_api/routers/viewer.py`
- Modify: `apps/api/tests/test_viewer_api.py`
- Regenerate: `packages/api-client/openapi.json`
- Regenerate: `packages/api-client/src/generated/`

- [ ] **Step 1: Write a failing API contract test**

```python
async def test_viewer_init_exposes_revision_completeness(client, factories) -> None:
    item = await factories.make_library_item(
        revision_stats={
            "completeness": {"accepted": False, "code": "figure_asset_unresolved"},
            "translated_pdf_failures": {"natural": {"code": "translated_pdf_incomplete"}},
        }
    )
    response = await client.get(f"/api/library-items/{item.id}/viewer")
    body = response.json()
    assert body["revision"]["complete"] is False
    assert body["revision"]["issues"] == ["figure_asset_unresolved", "translated_pdf_incomplete"]
```

- [ ] **Step 2: Run and confirm RED**

Run: `uv run pytest apps/api/tests/test_viewer_api.py -k completeness -q`  
Expected: FAIL because revision diagnostics are not in the schema.

- [ ] **Step 3: Add a stable DTO derived from revision stats**

```python
class RevisionInfo(BaseModel):
    revision_id: str
    source_version: str
    source_format: str
    quality_level: str
    complete: bool = True
    issues: list[str] = Field(default_factory=list)
```

Derive issues from machine-readable completeness, translation, figure, and translated-PDF failure codes. Never expose stack traces or provider secrets.

- [ ] **Step 4: Run API tests and regenerate client**

Run: `uv run pytest apps/api/tests/test_viewer_api.py -q && uv run python -m alinea_api.export_openapi > packages/api-client/openapi.json && pnpm --filter @alinea/api-client generate`  
Expected: PASS and generated client diff contains `complete` and `issues`.

- [ ] **Step 5: Commit**

```bash
git add apps/api/src/alinea_api/schemas/viewer.py apps/api/src/alinea_api/routers/viewer.py apps/api/tests/test_viewer_api.py packages/api-client
git commit -m "feat: expose viewer completeness diagnostics"
```

### Task 2: Frontend recovery banner and query refresh

**Files:**
- Modify: `apps/web/src/components/viewer/InfoPanel.tsx`
- Modify: `apps/web/src/components/viewer/ViewerShell.tsx`
- Modify: `apps/web/src/components/viewer/InfoPanel.test.tsx`
- Modify: `apps/web/src/hooks/useJobEvents.ts`
- Modify: `apps/web/src/hooks/useJobEvents.test.ts`

- [ ] **Step 1: Write failing UI tests**

```typescript
test("shows incomplete reason and reingest action", async () => {
  renderInfoPanel({ revision: { complete: false, issues: ["figure_asset_unresolved"] } });
  expect(screen.getByText("図表の復元が完了していません")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "再取り込み" })).toBeEnabled();
});

test("job completion invalidates every derived viewer query", async () => {
  const queryClient = new QueryClient();
  const invalidateQueries = vi.spyOn(queryClient, "invalidateQueries");
  renderHook(() => useJobEvents({ jobId: "job-1", itemId: "item-1" }), {
    wrapper: ({ children }) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    ),
  });
  act(() => latestFakeEventSource.emit("done", { status: "succeeded" }));
  expect(invalidateQueries).toHaveBeenCalledWith({ queryKey: ["viewer", "item-1"] });
  expect(invalidateQueries).toHaveBeenCalledWith({ queryKey: ["document", "item-1"] });
  expect(invalidateQueries).toHaveBeenCalledWith({ queryKey: ["article", "item-1"] });
  expect(invalidateQueries).toHaveBeenCalledWith({ queryKey: ["pdf-availability", "item-1"] });
});
```

- [ ] **Step 2: Run and confirm RED**

Run: `pnpm --filter @alinea/web test --run src/components/viewer/InfoPanel.test.tsx src/hooks/useJobEvents.test.ts`  
Expected: FAIL because completeness reasons and full invalidation are missing.

- [ ] **Step 3: Implement issue labels and existing control wiring**

```typescript
const COMPLETENESS_LABELS: Record<string, string> = {
  document_incomplete: "本文の復元が完了していません",
  figure_asset_unresolved: "図表の復元が完了していません",
  translation_incomplete: "翻訳が完了していません",
  translated_pdf_incomplete: "日本語PDFが完了していません",
};
```

Use the existing re-ingest mutation and retry endpoints; do not add a second competing workflow. After SSE `done`, invalidate Viewer init, document, translations, figures, article, job list, and PDF availability.

- [ ] **Step 4: Run frontend tests and typecheck**

Run: `pnpm --filter @alinea/web test --run src/components/viewer/InfoPanel.test.tsx src/hooks/useJobEvents.test.ts && pnpm --filter @alinea/web typecheck`  
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/components/viewer/InfoPanel.tsx apps/web/src/components/viewer/ViewerShell.tsx apps/web/src/components/viewer/InfoPanel.test.tsx apps/web/src/hooks/useJobEvents.ts apps/web/src/hooks/useJobEvents.test.ts
git commit -m "fix: expose and refresh viewer recovery states"
```

### Task 3: Broken-image-safe figure UI

**Files:**
- Modify: `apps/web/src/components/viewer/FigureTableBlock.tsx`
- Modify: `apps/web/src/components/viewer/FiguresPanel.tsx`
- Modify: `apps/web/src/components/viewer/FigureTableBlock.test.tsx`
- Modify: `apps/web/src/components/viewer/FiguresPanel.test.tsx`

- [ ] **Step 1: Write failing image-error tests**

```typescript
test("does not leave a broken img icon in the document", () => {
  render(<FigureTableBlock block={figureWithAsset("/api/assets/bad")} />);
  fireEvent.error(screen.getByRole("img"));
  expect(screen.queryByRole("img")).toBeNull();
  expect(screen.getByText("図を復元できませんでした")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "再取り込み" })).toBeEnabled();
});
```

- [ ] **Step 2: Run and confirm RED**

Run: `pnpm --filter @alinea/web test --run src/components/viewer/FigureTableBlock.test.tsx src/components/viewer/FiguresPanel.test.tsx`  
Expected: FAIL because broken `<img>` elements remain rendered.

- [ ] **Step 3: Implement error replacement**

Track asset failure per `asset_key`, remove the failed `<img>`, render a consistent empty/error state, and route retry to the existing re-ingest action. A missing `asset_key` is the same explicit error state, not an empty `src`.

- [ ] **Step 4: Run figure UI tests**

Run: `pnpm --filter @alinea/web test --run src/components/viewer/FigureTableBlock.test.tsx src/components/viewer/FiguresPanel.test.tsx`  
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/components/viewer/FigureTableBlock.tsx apps/web/src/components/viewer/FiguresPanel.tsx apps/web/src/components/viewer/FigureTableBlock.test.tsx apps/web/src/components/viewer/FiguresPanel.test.tsx
git commit -m "fix: replace broken paper images with recovery state"
```

### Task 4: Corpus-driven extension and Viewer harness

**Files:**
- Create: `apps/extension/e2e/arxiv-production-regression.mjs`
- Create: `apps/extension/e2e/check-no-corpus-hardcoding.mjs`
- Create: `apps/extension/e2e/README-arxiv-regression.md`
- Modify: `apps/extension/package.json`

- [ ] **Step 1: Write corpus-loader unit behavior into the harness**

The harness must require an external JSON file through `ALINEA_ARXIV_CORPUS`; no production or committed default corpus is allowed.

```javascript
const corpusPath = process.env.ALINEA_ARXIV_CORPUS;
if (!corpusPath) throw new Error("ALINEA_ARXIV_CORPUS is required");
const corpus = JSON.parse(readFileSync(corpusPath, "utf8"));
if (!Array.isArray(corpus) || corpus.length === 0) throw new Error("corpus must be a non-empty array");
for (const paper of corpus) {
  if (!paper.arxiv_id) throw new Error("each corpus entry requires arxiv_id");
}
```

- [ ] **Step 2: Add frontend-only action helpers**

Implement extension popup save for new items and Viewer `再取り込み` for existing items. Translation retry, article regeneration, figure tab, source/Japanese PDF toggles, and all waits must click visible controls. API requests may be observed and polled for diagnostics but not used to trigger product actions.

- [ ] **Step 3: Add generic DOM and asset assertions**

```javascript
assert.equal(await page.locator(".alinea-paragraph[data-block-id]").count() > 1, true);
assert.deepEqual(await visibleTexCommands(page, "main"), []);
assert.deepEqual(await fullLayoutIssues(page, "main"), []);
assert.equal((await imageStates(page)).every((img) => img.complete && img.naturalWidth > 0), true);
assert.equal(await page.getByText("✦ 全体概要図").count(), 1);
```

Force lazy images into view, verify unique source-figure URLs, article images, and tables, and save per-paper screenshots/results.

- [ ] **Step 4: Add Japanese PDF audit command**

After the UI downloads each Japanese PDF, invoke a repository helper that checks page count, Japanese text, raw controls, page bounds, and a render manifest supplied through revision stats. The helper fails nonzero on any paper.

- [ ] **Step 5: Add script and documentation**

```json
{
  "scripts": {
    "e2e:arxiv-regression": "node e2e/arxiv-production-regression.mjs"
  }
}
```

Document required services, auth state, extension build, corpus format, output directory, and that corpus IDs are QA data rather than production behavior.

Add a hardcoding audit helper that builds escaped ID/title patterns from the external corpus, scans only files under the supplied product roots, skips test/docs/build directories, and exits nonzero with matching paths. It must use `rg --files`/`rg -n` through `spawnSync`, never a committed ID list.

```javascript
import { readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { spawnSync } from "node:child_process";

function patternFile(values) {
  const path = join(tmpdir(), `alinea-no-hardcode-${process.pid}.txt`);
  writeFileSync(path, `${values.join("\n")}\n`);
  return path;
}

const [corpusPath, ...roots] = process.argv.slice(2);
const corpus = JSON.parse(readFileSync(corpusPath, "utf8"));
const needles = corpus.flatMap((paper) => [paper.arxiv_id, paper.title].filter(Boolean));
const result = spawnSync("rg", ["-n", "-F", "-f", patternFile(needles), ...roots], {
  encoding: "utf8",
});
const productMatches = result.stdout
  .split("\n")
  .filter(Boolean)
  .filter((line) => !/(^|\/)(tests?|e2e|docs|dist|\.output)\//.test(line));
if (productMatches.length) {
  process.stderr.write(`${productMatches.join("\n")}\n`);
  process.exit(1);
}
```

- [ ] **Step 6: Smoke the loader with a one-paper temporary corpus**

Run: `ALINEA_ARXIV_CORPUS=/tmp/alinea-one-paper.json QA_LIMIT=1 pnpm --filter @alinea/extension e2e:arxiv-regression`  
Expected: the harness loads the extension and either completes the paper or reports a product assertion; no loader/configuration error.

- [ ] **Step 7: Commit**

```bash
git add apps/extension/e2e/arxiv-production-regression.mjs apps/extension/e2e/check-no-corpus-hardcoding.mjs apps/extension/e2e/README-arxiv-regression.md apps/extension/package.json
git commit -m "test: add corpus-driven arxiv browser regression"
```

### Task 5: Reprocess the existing 20 through the frontend

**Files:**
- No production source file changes
- Runtime input: `/tmp/alinea-arxiv20-corpus.json`
- Runtime output: `/tmp/alinea-arxiv20-fixed-20260710/`

- [ ] **Step 1: Create a temporary corpus from the prior QA result**

Create `/tmp/alinea-arxiv20-corpus.json` from the 20 prior result entries. This runtime QA file may contain IDs; do not add it to git.

- [ ] **Step 2: Build extension and start the real stack**

Run: `pnpm --filter @alinea/extension build` and the repository's documented API, web, worker, Redis, S3, database, and TeX services. Confirm real LLM routing is configured and fake LLM mode is unset.

- [ ] **Step 3: Run all 20 through frontend re-ingestion and regeneration**

Run: `ALINEA_ARXIV_CORPUS=/tmp/alinea-arxiv20-corpus.json QA_OUTPUT=/tmp/alinea-arxiv20-fixed-20260710 pnpm --filter @alinea/extension e2e:arxiv-regression`  
Expected: exit 0 with 20/20 paper records passing every assertion.

- [ ] **Step 4: Verify user data preservation**

Compare each LibraryItem ID, collections, tags, status, reading position, and annotation counts before/after. Expected: LibraryItem identity and user-owned metadata unchanged; revision/translation/article asset IDs may change.

- [ ] **Step 5: Audit product code for corpus hardcoding**

Run a generated pattern from the temporary corpus against product directories only:

```bash
node apps/extension/e2e/check-no-corpus-hardcoding.mjs \
  /tmp/alinea-arxiv20-corpus.json \
  apps packages docker
```

Expected: exit 0 and zero ID/title matches outside test fixtures and documentation.

### Task 6: Full verification

**Files:**
- No new files unless a discovered generic regression requires another test

- [ ] **Step 1: Run Python suites for touched packages**

Run: `uv run pytest packages/py-core/tests apps/worker/tests apps/api/tests -q`  
Expected: PASS with zero failures.

- [ ] **Step 2: Run frontend and extension checks**

Run: `pnpm --filter @alinea/web test --run && pnpm --filter @alinea/web typecheck && pnpm --filter @alinea/extension typecheck && pnpm --filter @alinea/extension build`  
Expected: PASS.

- [ ] **Step 3: Run formatting/static checks**

Run: `uv run ruff check packages/py-core apps/worker apps/api && pnpm lint`  
Expected: PASS.

- [ ] **Step 4: Re-run the 20-paper browser regression fresh**

Run the Task 5 command against a new QA output directory. Expected: 20/20 success, visible raw LaTeX 0, broken images 0, layout findings 0, article overviews 20, Japanese PDFs 20.

- [ ] **Step 5: Commit only any final generic test adjustments**

```bash
git status --short
git add <only-reviewed-generic-files>
git commit -m "test: close arxiv production regression gaps"
```
