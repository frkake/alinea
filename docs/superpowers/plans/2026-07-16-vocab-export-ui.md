# S5: Vocab Markdown Export UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an "エクスポート (.md)" button to the VocabHeader that downloads the current vocab list as Markdown respecting active filters.

**Architecture:** Add `onExportMarkdown` prop to `VocabHeader`, wire the URL-builder in `page.tsx` using the existing `triggerDownload` helper. One component test verifies the button renders and fires the callback; the URL-construction logic lives in `page.tsx` and is tested inline.

**Tech Stack:** React (Next.js App Router), Vitest + React Testing Library, `@testing-library/user-event`

## Global Constraints

- Frontend-only: no backend changes, no new packages.
- No new npm/pnpm dependencies.
- Follow existing inline style patterns (no CSS modules, no Tailwind).
- Test command: `pnpm --filter @alinea/web test` (runs all web tests; must pass in full).
- All file paths are relative to the worktree root `/home/iida/workspace/alinea/.claude/worktrees/agent-a43b73089c1e45851`.

---

### Task 1: VocabHeader — add `onExportMarkdown` prop + button (TDD)

**Files:**
- Modify: `apps/web/src/components/vocab/VocabHeader.tsx`
- Create: `apps/web/src/components/vocab/VocabHeader.test.tsx`

**Interfaces:**
- Produces: `VocabHeaderProps.onExportMarkdown: () => void` — callback invoked when button clicked.

- [ ] **Step 1: Write the failing test**

Create `apps/web/src/components/vocab/VocabHeader.test.tsx` with:

```tsx
import { render, screen, fireEvent } from "@testing-library/react";
import { describe, expect, test, vi } from "vitest";
import { VocabHeader } from "@/components/vocab/VocabHeader";

const BASE_PROPS = {
  total: 42,
  dueCount: 5,
  searchValue: "",
  searchFetching: false,
  onSearchChange: vi.fn(),
  onStartReview: vi.fn(),
  reviewLoading: false,
  onExportMarkdown: vi.fn(),
};

// VT-S5-01 / VT-S5-02: エクスポートボタン
describe("VocabHeader — export button (S5)", () => {
  test("VT-S5-01: export button is rendered", () => {
    render(<VocabHeader {...BASE_PROPS} />);
    expect(
      screen.getByRole("button", { name: "エクスポート (.md)" }),
    ).toBeInTheDocument();
  });

  test("VT-S5-02: clicking the button calls onExportMarkdown once", () => {
    const onExportMarkdown = vi.fn();
    render(<VocabHeader {...BASE_PROPS} onExportMarkdown={onExportMarkdown} />);
    fireEvent.click(screen.getByRole("button", { name: "エクスポート (.md)" }));
    expect(onExportMarkdown).toHaveBeenCalledTimes(1);
  });
});
```

- [ ] **Step 2: Run the test to confirm it FAILS**

```bash
cd /home/iida/workspace/alinea/.claude/worktrees/agent-a43b73089c1e45851
pnpm --filter @alinea/web test -- --reporter=verbose --run VocabHeader.test
```

Expected: FAIL — "Unable to find an accessible element with the role 'button' and name 'エクスポート (.md)'"

- [ ] **Step 3: Implement — add prop + button to `VocabHeader.tsx`**

Replace the contents of `apps/web/src/components/vocab/VocabHeader.tsx` with:

```tsx
"use client";

import { VocabSearchBox } from "@/components/vocab/VocabSearchBox";

export interface VocabHeaderProps {
  total: number;
  dueCount: number;
  searchValue: string;
  searchFetching: boolean;
  onSearchChange: (v: string) => void;
  onStartReview: () => void;
  reviewLoading: boolean;
  onExportMarkdown: () => void;
}

/** 見出し行(4d §4.2.3)。「語彙帳」「{n} 語 — 読んだ論文の文脈から」+ 検索 + エクスポート + 復習をはじめる。 */
export function VocabHeader({
  total,
  dueCount,
  searchValue,
  searchFetching,
  onSearchChange,
  onStartReview,
  reviewLoading,
  onExportMarkdown,
}: VocabHeaderProps) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
      <span style={{ fontSize: 16, fontWeight: 700 }}>語彙帳</span>
      <span style={{ fontSize: 11.5, color: "var(--pr-text-muted)" }}>
        {total} 語 — 読んだ論文の文脈から
      </span>
      <span style={{ flex: 1 }} />
      <VocabSearchBox value={searchValue} onChange={onSearchChange} fetching={searchFetching} />
      <button
        type="button"
        onClick={onExportMarkdown}
        aria-label="エクスポート (.md)"
        style={{
          display: "inline-flex",
          alignItems: "center",
          height: 28,
          padding: "0 13px",
          borderRadius: 6,
          border: "1px solid var(--pr-border-soft)",
          background: "transparent",
          color: "var(--pr-text-mid)",
          fontSize: 11.5,
          fontWeight: 500,
          fontFamily: "inherit",
          cursor: "pointer",
        }}
      >
        エクスポート (.md)
      </button>
      <button
        type="button"
        onClick={onStartReview}
        disabled={dueCount === 0 || reviewLoading}
        title={dueCount === 0 ? "復習期の語彙はありません" : undefined}
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: 7,
          height: 28,
          padding: "0 13px",
          borderRadius: 6,
          border: "none",
          background: "var(--pr-acc)",
          color: "#FFFFFF",
          fontSize: 11.5,
          fontWeight: 600,
          fontFamily: "inherit",
          cursor: dueCount === 0 ? "default" : "pointer",
          opacity: dueCount === 0 || reviewLoading ? 0.7 : 1,
        }}
      >
        復習をはじめる
        {dueCount > 0 ? (
          <span
            style={{
              fontSize: 9.5,
              fontWeight: 500,
              opacity: 0.8,
              border: "1px solid rgba(255,255,255,0.4)",
              borderRadius: 3,
              padding: "0 5px",
            }}
          >
            {dueCount}
          </span>
        ) : null}
      </button>
    </div>
  );
}
```

- [ ] **Step 4: Run the test to confirm it PASSES**

```bash
cd /home/iida/workspace/alinea/.claude/worktrees/agent-a43b73089c1e45851
pnpm --filter @alinea/web test -- --reporter=verbose --run VocabHeader.test
```

Expected: PASS — 2 tests, 0 failures.

- [ ] **Step 5: Commit**

```bash
cd /home/iida/workspace/alinea/.claude/worktrees/agent-a43b73089c1e45851
git add apps/web/src/components/vocab/VocabHeader.tsx apps/web/src/components/vocab/VocabHeader.test.tsx
git commit -m "feat(web): add onExportMarkdown prop + button to VocabHeader (S5)"
```

---

### Task 2: Wire export in page.tsx

**Files:**
- Modify: `apps/web/src/app/(app)/vocab/[[...vocabId]]/page.tsx`

**Interfaces:**
- Consumes: `VocabHeaderProps.onExportMarkdown: () => void` (from Task 1)
- Consumes: `triggerDownload(url: string): void` from `@/components/settings/download`

- [ ] **Step 1: Add `triggerDownload` import to `page.tsx`**

Open `apps/web/src/app/(app)/vocab/[[...vocabId]]/page.tsx` and add to the import block:

```tsx
import { triggerDownload } from "@/components/settings/download";
```

(The import block starts at line 6 with the api-client imports. Add this import after the existing imports.)

- [ ] **Step 2: Wire `onExportMarkdown` in the `<VocabHeader>` call**

Find the `<VocabHeader` JSX block (around line 225) and add the `onExportMarkdown` prop:

```tsx
      <VocabHeader
        total={counts.all}
        dueCount={counts.due}
        searchValue={q}
        searchFetching={listQuery.isFetching && !listQuery.isFetchingNextPage}
        onSearchChange={(v) => replaceTo(vocabIdParam, { q: v })}
        onStartReview={() => startReviewMutation.mutate()}
        reviewLoading={startReviewMutation.isPending}
        onExportMarkdown={() => {
          const sp = new URLSearchParams();
          if (kind) sp.set("kind", kind);
          if (dueOnly) sp.set("due", "true");
          if (q) sp.set("q", q);
          if (sort !== "added_at") sp.set("sort", sort);
          triggerDownload(
            `/api/vocab/export/markdown${sp.size ? `?${sp.toString()}` : ""}`,
          );
        }}
      />
```

- [ ] **Step 3: Run full web test suite to confirm all tests pass**

```bash
cd /home/iida/workspace/alinea/.claude/worktrees/agent-a43b73089c1e45851
pnpm --filter @alinea/web test -- --run
```

Expected: All tests pass (existing + new 2 VocabHeader tests).

- [ ] **Step 4: Commit**

```bash
cd /home/iida/workspace/alinea/.claude/worktrees/agent-a43b73089c1e45851
git add apps/web/src/app/\(app\)/vocab/\[\[...vocabId\]\]/page.tsx
git commit -m "feat(web): wire vocab Markdown export in VocabPage — respects active filters (S5)"
```

---

### Task 3: Write SDD report

**Files:**
- Create: `.superpowers/sdd/s5-report.md`

- [ ] **Step 1: Create the report**

```bash
cd /home/iida/workspace/alinea/.claude/worktrees/agent-a43b73089c1e45851
cat > .superpowers/sdd/s5-report.md << 'EOF'
# S5 Vocab Export UI — Implementation Report

**Status:** DONE

**Commits:**
- feat(web): add onExportMarkdown prop + button to VocabHeader (S5)
- feat(web): wire vocab Markdown export in VocabPage — respects active filters (S5)

**Test summary:** 2 new tests in VocabHeader.test.tsx pass (VT-S5-01 render, VT-S5-02 click); full web test suite green.

**Concerns:** None. The URL-construction is a direct URLSearchParams build over the already-decoded filter state; no edge-cases beyond what the API itself validates.
EOF
```

- [ ] **Step 2: Commit**

```bash
cd /home/iida/workspace/alinea/.claude/worktrees/agent-a43b73089c1e45851
git add .superpowers/sdd/s5-report.md
git commit -m "docs: add S5 vocab export UI sdd report"
```
