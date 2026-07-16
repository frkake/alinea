# Viewer Chat History + Cross-Pane Annotations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a "new conversation" (clear) button + a thread-history popover with delete to the chat panel, and extend highlight/comment/ask-AI/add-vocabulary creation from the translation pane to the bilingual and source panes.

**Architecture:** Feature 1 is `ChatPanel.tsx`-only, built on already-generated but unused chat-thread SDK functions (`chatCreateThread`, `chatDeleteThread`) plus the already-wired `threadsQuery`. Feature 2 extracts the ~200 lines of annotation-creation logic (selection handler, `SelectionMenu` render, `createHighlight`/`addToVocab`/`copySelection`/ask-AI) from `TranslationPane.tsx` into a shared `useAnnotationSelection` hook that all three panes consume — one implementation, no triplication.

**Tech Stack:** Next.js (React 18, `"use client"` components), TypeScript, Zustand stores (`useViewerStore`, `useViewerChatStore`), TanStack Query, `@alinea/api-client` (generated SDK), Vitest + `@testing-library/react` (jsdom). Styling = inline styles with `var(--pr-*)` CSS custom properties; icons are text glyphs.

## Global Constraints

- Package manager: `pnpm@10.13.1`. Web package is `@alinea/web`. Test = `pnpm --filter @alinea/web test` (`vitest run`); typecheck = `pnpm --filter @alinea/web typecheck` (`tsc --noEmit`).
- Tests are colocated `*.test.tsx` next to the component. Mock the SDK with `vi.mock("@alinea/api-client", async (importOriginal) => ({ ...await importOriginal(), fnName: vi.fn() }))`.
- Component-level tests that render panes wrap in a fresh `QueryClient` with `{ defaultOptions: { queries: { retry: false } } }` via a local `renderWithClient` helper.
- All UI copy is Japanese. Buttons: `border: "none"; background: "transparent"; cursor: "pointer"; fontFamily: "inherit"`.
- Annotation cache key is exactly `["annotations", itemId]` (shared across panes + `AnnotationListPanel`). Chat thread list key is `["chat-threads", itemId]`; messages key is `["chat-messages", activeThreadId]`.
- Anchor shape: `{ revision_id, block_id, side: "source" | "translation", start: number | null, end: number | null, quote: string | null }`.
- Do NOT modify: `ArticlePane` behavior, the equation "この式を説明" flow, backend/API, or the generated SDK.

---

## File Structure

- **Create** `apps/web/src/hooks/use-annotation-selection.ts` — shared hook returning `{ onPointerUp, selectionMenu }`. Owns selection→anchor resolution, highlight/comment creation, add-to-vocab, copy, and quote-carrying ask-AI.
- **Create** `apps/web/src/hooks/annotation-selection-resolve.ts` — pure `resolveSelectionAnchor(...)` helper (side/offset resolution) so it can be unit-tested without React.
- **Create** `apps/web/src/hooks/annotation-selection-resolve.test.ts` — unit tests for the pure resolver.
- **Modify** `apps/web/src/components/chat/ChatPanel.tsx` — add "新しい会話" button + history popover (with delete + confirm modal).
- **Create** `apps/web/src/components/chat/ChatThreadHistoryPopover.tsx` — the history list popover (mirrors `FigureVersionPopover.tsx`).
- **Modify** `apps/web/src/components/chat/ChatPanel.test.tsx` — tests for new-conversation, history switch, delete.
- **Modify** `apps/web/src/components/viewer/TranslationPane.tsx` — replace inline annotation logic with the hook.
- **Modify** `apps/web/src/components/viewer/BilingualPane.tsx` — add `data-block-id` to translation cell; wire the hook.
- **Modify** `apps/web/src/components/viewer/SourcePane.tsx` — wire the hook.
- **Modify** `apps/web/src/components/viewer/BilingualPane.test.tsx` / `SourcePane.test.tsx` — creation smoke tests.

---

## Task 1: Pure selection-anchor resolver

Extract the side/offset resolution logic (currently inline in `TranslationPane.onPointerUp`, l.348-396) into a pure, testable function. This is the trickiest part of the extraction because side detection differs per pane, so it gets its own task and unit tests.

**Files:**
- Create: `apps/web/src/hooks/annotation-selection-resolve.ts`
- Test: `apps/web/src/hooks/annotation-selection-resolve.test.ts`

**Interfaces:**
- Consumes: `SOURCE_TEXT_ATTR`, `textOffsetWithin` from `@/components/viewer/text-offset`; `ViewerSelection` type from `@/stores/viewer-store`.
- Produces:
  ```ts
  export function resolveSelectionAnchor(
    range: Range,
    selectedText: string,
    defaultSide: "source" | "translation",
  ): ViewerSelection | null;
  ```
  Returns `null` when no enclosing `[data-block-id]` is found. Otherwise a full `ViewerSelection` `{ blockId, side, quote, start, end, rect, sourceFullText? }`.

- [ ] **Step 1: Write the failing tests**

```ts
// apps/web/src/hooks/annotation-selection-resolve.test.ts
import { describe, expect, test } from "vitest";
import { resolveSelectionAnchor } from "@/hooks/annotation-selection-resolve";
import { SOURCE_TEXT_ATTR } from "@/components/viewer/text-offset";

/** Build a DOM subtree, select `text` inside the node matching `selector`, return its Range. */
function selectWithin(html: string, selector: string): { range: Range; text: string } {
  const host = document.createElement("div");
  host.innerHTML = html;
  document.body.appendChild(host);
  const target = host.querySelector(selector) as HTMLElement;
  const textNode = target.firstChild as Text;
  const range = document.createRange();
  range.setStart(textNode, 0);
  range.setEnd(textNode, textNode.length);
  return { range, text: textNode.textContent ?? "" };
}

describe("resolveSelectionAnchor", () => {
  test("returns null when no [data-block-id] ancestor exists", () => {
    const { range, text } = selectWithin(`<p class="x">loose text</p>`, ".x");
    expect(resolveSelectionAnchor(range, text, "translation")).toBeNull();
  });

  test("uses defaultSide=source when block has no side markers (source pane)", () => {
    const { range, text } = selectWithin(
      `<div data-block-id="blk-1">Hello world</div>`,
      '[data-block-id="blk-1"]',
    );
    const sel = resolveSelectionAnchor(range, text, "source");
    expect(sel).toMatchObject({ blockId: "blk-1", side: "source", quote: "Hello world", start: 0 });
    expect(sel?.end).toBe("Hello world".length);
    expect(sel?.sourceFullText).toBe("Hello world");
  });

  test("prefers data-side over defaultSide (bilingual translation cell)", () => {
    const { range, text } = selectWithin(
      `<div data-block-id="blk-2" data-side="translation">訳文テキスト</div>`,
      '[data-block-id="blk-2"]',
    );
    const sel = resolveSelectionAnchor(range, text, "source");
    expect(sel?.side).toBe("translation");
    expect(sel?.sourceFullText).toBeUndefined();
  });

  test("SOURCE_TEXT_ATTR inside a block forces source and sets sourceFullText", () => {
    const { range, text } = selectWithin(
      `<div data-block-id="blk-3"><span ${SOURCE_TEXT_ATTR}>Original sentence.</span></div>`,
      `[${SOURCE_TEXT_ATTR}]`,
    );
    const sel = resolveSelectionAnchor(range, text, "translation");
    expect(sel?.side).toBe("source");
    expect(sel?.sourceFullText).toBe("Original sentence.");
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pnpm --filter @alinea/web test annotation-selection-resolve`
Expected: FAIL — `resolveSelectionAnchor` is not defined / module not found.

- [ ] **Step 3: Write the implementation**

```ts
// apps/web/src/hooks/annotation-selection-resolve.ts
import { SOURCE_TEXT_ATTR, textOffsetWithin } from "@/components/viewer/text-offset";
import type { ViewerSelection } from "@/stores/viewer-store";

/**
 * 選択 Range を注釈アンカー(ViewerSelection)へ解決する純関数。
 * side 判別の優先順位:
 *  1. block 内に [SOURCE_TEXT_ATTR](対訳ポップ内原文・未訳フォールバック)があれば 'source'
 *  2. なければ最寄り [data-block-id] 要素の data-side(対訳ペインのセル)
 *  3. それも無ければ defaultSide(原文ペイン='source' / 訳文ペイン='translation')
 * オフセットは side のテキスト内文字位置(source は SOURCE_TEXT_ATTR 要素、それ以外は block 要素基準)。
 */
export function resolveSelectionAnchor(
  range: Range,
  selectedText: string,
  defaultSide: "source" | "translation",
): ViewerSelection | null {
  let node: Node | null = range.commonAncestorContainer;
  let blockEl: HTMLElement | null = null;
  while (node) {
    if (node instanceof HTMLElement && node.dataset.blockId) {
      blockEl = node;
      break;
    }
    node = node.parentNode;
  }
  if (!blockEl) return null;

  const ancestorEl =
    range.commonAncestorContainer.nodeType === Node.ELEMENT_NODE
      ? (range.commonAncestorContainer as Element)
      : range.commonAncestorContainer.parentElement;
  const sourceRoot = ancestorEl?.closest(`[${SOURCE_TEXT_ATTR}]`) ?? null;

  let side: "source" | "translation";
  if (sourceRoot && blockEl.contains(sourceRoot)) {
    side = "source";
  } else if (blockEl.dataset.side === "source" || blockEl.dataset.side === "translation") {
    side = blockEl.dataset.side;
  } else {
    side = defaultSide;
  }

  const offsetRoot = side === "source" && sourceRoot ? sourceRoot : blockEl;
  const start = textOffsetWithin(offsetRoot, range.startContainer, range.startOffset);
  const end = start + selectedText.length;
  const rect = range.getBoundingClientRect();
  return {
    blockId: blockEl.dataset.blockId ?? "",
    side,
    quote: selectedText.slice(0, 500),
    start,
    end,
    rect: { top: rect.top, left: rect.left, bottom: rect.bottom, right: rect.right },
    sourceFullText: side === "source" ? (offsetRoot.textContent ?? undefined) : undefined,
  };
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pnpm --filter @alinea/web test annotation-selection-resolve`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/hooks/annotation-selection-resolve.ts apps/web/src/hooks/annotation-selection-resolve.test.ts
git commit -m "feat(web): pure selection-anchor resolver for cross-pane annotations"
```

---

## Task 2: `useAnnotationSelection` hook

Wrap the resolver plus the create/vocab/copy/ask-AI handlers into a hook that any pane can drop in. This is a faithful move of `TranslationPane`'s existing handlers (l.399-506, 664-680), with two changes: (a) side resolution goes through Task 1's resolver, (b) ask-AI now attaches the quote as a pending anchor.

**Files:**
- Create: `apps/web/src/hooks/use-annotation-selection.ts`

**Interfaces:**
- Consumes: `resolveSelectionAnchor` (Task 1); `useViewerStore` (`selection`, `setSelection`, `setPanel`); `useViewerChatStore` (`addPendingAnchor`); `annotationsCreate`, `vocabCreate`, `type Annotation`, `type AnnotationListResponse` from `@alinea/api-client`; `extractVocabContext` from `@/components/viewer/vocab-context`; `SelectionMenu` from `@/components/viewer/SelectionMenu`; `useToast`, `useRouter`, `useQueryClient`, `useIsMobile`; `HighlightColor` from `@/components/ui/HighlightMark`.
- Produces:
  ```ts
  export function useAnnotationSelection(opts: {
    itemId: string;
    revisionId: string;
    defaultSide: "source" | "translation";
  }): { onPointerUp: () => void; selectionMenu: ReactNode };
  ```

- [ ] **Step 1: Write the implementation** (no standalone unit test — behavior is covered by the pane smoke tests in Tasks 6-7 and the resolver test in Task 1; the hook is thin glue over already-tested pieces)

```ts
// apps/web/src/hooks/use-annotation-selection.ts
"use client";

import { useCallback, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import {
  annotationsCreate,
  vocabCreate,
  type Annotation,
  type AnnotationListResponse,
} from "@alinea/api-client";
import { useToast } from "@/components/ui/Toast";
import type { HighlightColor } from "@/components/ui/HighlightMark";
import { useIsMobile } from "@/hooks/useMediaQuery";
import { useViewerStore } from "@/stores/viewer-store";
import { useViewerChatStore } from "@/stores/viewer-chat-store";
import { SelectionMenu } from "@/components/viewer/SelectionMenu";
import { extractVocabContext } from "@/components/viewer/vocab-context";
import { resolveSelectionAnchor } from "@/hooks/annotation-selection-resolve";

function tmpId(): string {
  return `tmp_${typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : Date.now()}`;
}

export function useAnnotationSelection({
  itemId,
  revisionId,
  defaultSide,
}: {
  itemId: string;
  revisionId: string;
  defaultSide: "source" | "translation";
}): { onPointerUp: () => void; selectionMenu: ReactNode } {
  const toast = useToast();
  const router = useRouter();
  const qc = useQueryClient();
  const isMobile = useIsMobile();
  const selection = useViewerStore((s) => s.selection);
  const setSelection = useViewerStore((s) => s.setSelection);
  const setPanel = useViewerStore((s) => s.setPanel);
  const addPendingAnchor = useViewerChatStore((s) => s.addPendingAnchor);
  const annotationsQueryKey = ["annotations", itemId];

  const onPointerUp = useCallback(() => {
    const sel = window.getSelection();
    if (!sel || sel.isCollapsed || sel.rangeCount === 0) {
      setSelection(null);
      return;
    }
    const text = sel.toString().trim();
    if (!text) {
      setSelection(null);
      return;
    }
    const resolved = resolveSelectionAnchor(sel.getRangeAt(0), text, defaultSide);
    setSelection(resolved);
  }, [setSelection, defaultSide]);

  const createHighlight = useCallback(
    (color: HighlightColor, comment: string | null) => {
      const sel = selection;
      if (!sel || !itemId) return;
      setSelection(null);
      const anchor = {
        revision_id: revisionId,
        block_id: sel.blockId,
        start: sel.start,
        end: sel.end,
        quote: sel.quote,
        side: sel.side,
      };
      const optimistic: Annotation = {
        id: tmpId(),
        kind: "highlight",
        color,
        anchor: { ...anchor, display: "" },
        comment,
        placed: true,
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      };
      const prev = qc.getQueryData<AnnotationListResponse>(annotationsQueryKey);
      qc.setQueryData<AnnotationListResponse>(annotationsQueryKey, (old) =>
        old ? { ...old, items: [...old.items, optimistic] } : old,
      );
      void annotationsCreate({
        path: { item_id: itemId },
        body: {
          kind: "highlight",
          color,
          anchor,
          comment: comment && comment.length > 0 ? comment : null,
        },
      }).then(
        () => {
          void qc.invalidateQueries({ queryKey: annotationsQueryKey });
          void qc.invalidateQueries({ queryKey: ["viewer", itemId] });
        },
        () => {
          if (prev) qc.setQueryData(annotationsQueryKey, prev);
          toast({
            kind: "error",
            message: "注釈を保存できませんでした",
            action: { label: "再試行", onClick: () => createHighlight(color, comment) },
          });
        },
      );
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [selection, itemId, revisionId, qc, toast, setSelection],
  );

  const addToVocab = useCallback(async () => {
    const sel = selection;
    if (!sel || sel.side !== "source" || sel.start == null || sel.end == null) return;
    setSelection(null);
    const { contextSentence, highlightStart, highlightEnd } = extractVocabContext(
      sel.sourceFullText ?? sel.quote,
      sel.start,
      sel.end,
    );
    try {
      const res = await vocabCreate({
        body: {
          library_item_id: itemId,
          term: sel.quote,
          anchor: {
            revision_id: revisionId,
            block_id: sel.blockId,
            start: sel.start,
            end: sel.end,
            quote: sel.quote,
            side: "source",
          },
          context_sentence: contextSentence,
          highlight: { start: highlightStart, end: highlightEnd },
        },
      });
      if (res.response.status === 409) {
        const existingId = (res.error as { existing?: { vocab_id?: string } } | undefined)?.existing
          ?.vocab_id;
        toast({ kind: "info", message: "すでに語彙帳にあります" });
        if (existingId) router.push(`/vocab/${existingId}`);
        return;
      }
      if (!res.data) throw new Error("vocab create failed");
      toast({ kind: "success", message: `「${sel.quote}」を語彙に追加しました` });
      router.push(`/vocab/${res.data.entry.id}`);
    } catch {
      toast({ kind: "error", message: "語彙に追加できませんでした" });
    }
  }, [selection, itemId, revisionId, router, toast, setSelection]);

  const copySelection = useCallback(
    (format: "citation" | "plain") => {
      const quote = selection?.quote ?? "";
      const text = format === "plain" ? quote : `"${quote}"`;
      void navigator.clipboard?.writeText(text).then(
        () => toast({ kind: "success", message: "コピーしました" }),
        () => toast({ kind: "error", message: "コピーできませんでした" }),
      );
      setSelection(null);
    },
    [selection, toast, setSelection],
  );

  // 「✦ AIに質問」: 選択文を引用チップとして積み、チャットタブへ(数式「この式を説明」と同流儀)。
  const askAI = useCallback(() => {
    const sel = selection;
    if (!sel) return;
    const quote = sel.quote;
    addPendingAnchor({
      anchor: {
        revision_id: revisionId,
        block_id: sel.blockId,
        start: sel.start,
        end: sel.end,
        quote,
        side: sel.side,
      },
      display: quote.length > 24 ? `${quote.slice(0, 24)}…` : quote,
    });
    setSelection(null);
    setPanel(true, "chat");
  }, [selection, revisionId, addPendingAnchor, setSelection, setPanel]);

  const selectionMenu: ReactNode =
    selection && !isMobile ? (
      <SelectionMenu
        milestone="M2"
        side={selection.side}
        position={{ top: selection.rect.bottom + 8, left: selection.rect.left }}
        onAskAI={askAI}
        onCopy={copySelection}
        onHighlight={(color) => createHighlight(color, null)}
        onComment={(color, comment) => createHighlight(color, comment.length > 0 ? comment : null)}
        onAddVocab={() => void addToVocab()}
      />
    ) : null;

  return { onPointerUp, selectionMenu };
}
```

- [ ] **Step 2: Typecheck the new hook**

Run: `pnpm --filter @alinea/web typecheck`
Expected: PASS (no type errors). If `addPendingAnchor`'s `Anchor` type rejects the object, ensure keys match `Anchor` (`revision_id, block_id, start?, end?, quote?, side`).

- [ ] **Step 3: Commit**

```bash
git add apps/web/src/hooks/use-annotation-selection.ts
git commit -m "feat(web): useAnnotationSelection hook (highlight/comment/vocab/ask-AI)"
```

---

## Task 3: Adopt the hook in `TranslationPane`

Replace TranslationPane's inline annotation logic with the hook, proving the hook is a faithful drop-in (its existing tests must stay green). This de-duplicates before adding new call sites.

**Files:**
- Modify: `apps/web/src/components/viewer/TranslationPane.tsx` (remove l.348-451 `onPointerUp`/`addToVocab`/`copySelection`, l.454-506 `createHighlight`, and the l.664-680 `SelectionMenu` block; add hook usage)

**Interfaces:**
- Consumes: `useAnnotationSelection` (Task 2).
- Produces: no new exports; `TranslationPane` public props unchanged.

- [ ] **Step 1: Wire the hook** — near the other store selectors (after l.147 `isMobile`), add:

```ts
const { onPointerUp, selectionMenu } = useAnnotationSelection({
  itemId,
  revisionId,
  defaultSide: "translation",
});
```

Add the import at the top with the other `@/hooks` imports:

```ts
import { useAnnotationSelection } from "@/hooks/use-annotation-selection";
```

- [ ] **Step 2: Delete the now-duplicated handlers** — remove the `onPointerUp` (l.348-396), `addToVocab` (l.399-438), `copySelection` (l.440-451), and `createHighlight` (l.454-506) definitions. Leave the bookmark `useEffect` (l.508-550) intact — it is a separate feature.

- [ ] **Step 3: Replace the SelectionMenu render** — change the scroll region's handler and the trailing menu block:

The scroll region (l.629-632) keeps `onPointerUp={onPointerUp}` (now the hook's). Replace the entire trailing `{selection && !isMobile ? (<SelectionMenu ... />) : null}` block (l.664-680) with:

```tsx
{selectionMenu}
```

- [ ] **Step 4: Remove now-unused imports** — drop imports only used by the deleted code: `vocabCreate`, `extractVocabContext`, `SOURCE_TEXT_ATTR`/`textOffsetWithin` (if unused elsewhere — grep first), `SelectionMenu`, `type Annotation`, `type AnnotationListResponse`, `HighlightColor` (if unused elsewhere), `tmpId` (delete the local fn if now unused). Keep `annotationsCreate`/`annotationsDelete` — the bookmark effect still uses them. Keep `useRouter`/`useToast`/`useQueryClient` only if still referenced (grep).

Run: `grep -n "SOURCE_TEXT_ATTR\|textOffsetWithin\|vocabCreate\|extractVocabContext\|SelectionMenu\|tmpId\|HighlightColor\|useRouter\b" apps/web/src/components/viewer/TranslationPane.tsx` and remove any import whose only references were in the deleted blocks.

- [ ] **Step 5: Run TranslationPane tests + typecheck**

Run: `pnpm --filter @alinea/web test TranslationPane && pnpm --filter @alinea/web typecheck`
Expected: PASS — existing TranslationPane tests still green, no unused-import / type errors.

- [ ] **Step 6: Commit**

```bash
git add apps/web/src/components/viewer/TranslationPane.tsx
git commit -m "refactor(web): TranslationPane uses shared useAnnotationSelection hook"
```

---

## Task 4: Enable annotation creation in `BilingualPane`

**Files:**
- Modify: `apps/web/src/components/viewer/BilingualPane.tsx` (translation cell l.582; scroll region l.358-362; imports)
- Test: `apps/web/src/components/viewer/BilingualPane.test.tsx`

**Interfaces:**
- Consumes: `useAnnotationSelection` (Task 2).
- Produces: `BilingualParagraph`'s translation cell now carries `data-block-id={block.id}`.

- [ ] **Step 1: Write the failing test** — append to `BilingualPane.test.tsx`, inside the existing `describe("BilingualParagraph ...")` area or a new describe:

```tsx
describe("BilingualParagraph data-block-id parity", () => {
  const block: DocBlock = {
    id: "blk-p1",
    type: "paragraph",
    inlines: [{ t: "text", v: "The rectified flow is an ODE." }],
  };
  function unit(): TranslationUnitItem {
    return {
      unit_id: "u1",
      block_id: "blk-p1",
      text_ja: "整流フローは常微分方程式である。",
      content_ja: null,
      state: "machine",
      quality_flags: [],
      proposal: null,
    };
  }

  test("translation cell carries data-block-id so selections resolve to a block", () => {
    const { container } = render(<BilingualParagraph block={block} unit={unit()} />);
    const translationCell = container.querySelector('[data-side="translation"]');
    expect(translationCell).toHaveAttribute("data-block-id", "blk-p1");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pnpm --filter @alinea/web test BilingualPane -t "data-block-id"`
Expected: FAIL — translation cell has no `data-block-id`.

- [ ] **Step 3: Add `data-block-id` to the translation cell** — in `BilingualParagraph`, the translation `<div>` (currently `data-side="translation"` only, l.582-583) becomes:

```tsx
<div
  data-block-id={block.id}
  data-side="translation"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pnpm --filter @alinea/web test BilingualPane -t "data-block-id"`
Expected: PASS.

- [ ] **Step 5: Wire the hook into the pane** — in `BilingualPane` (the function starting l.114), add the import:

```ts
import { useAnnotationSelection } from "@/hooks/use-annotation-selection";
```

After the store selectors (after l.134 `chatEvidence`), add:

```ts
const { onPointerUp, selectionMenu } = useAnnotationSelection({
  itemId,
  revisionId,
  defaultSide: "translation",
});
```

Attach to the scroll region (l.358-362) — add `onPointerUp={onPointerUp}` to that `<div ref={scrollRef} ...>`. Render the menu just before the outer closing `</div>` of the return (after the scroll-region `</div>` at l.376, before l.377 `</div>`):

```tsx
      {selectionMenu}
    </div>
```

- [ ] **Step 6: Write the creation smoke test** — append to `BilingualPane.test.tsx`. This drives a real selection over the rendered pane and asserts `annotationsCreate` fires with the right side. Add `annotationsCreate` to the SDK mock at the top of the file (extend the existing `vi.mock` return with `annotationsCreate: vi.fn()`), and import it.

```tsx
// add to imports:
// import { annotationsCreate } from "@alinea/api-client";
// extend vi.mock("@alinea/api-client", ...) return object with: annotationsCreate: vi.fn(),

describe("BilingualPane annotation creation", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(annotationsList).mockResolvedValue({
      data: { items: [], counts: { all: 0, important: 0, question: 0, idea: 0, term: 0, with_comment: 0, unplaced: 0 } },
    } as never);
    vi.mocked(translationsListUnits).mockResolvedValue({
      data: {
        set_id: "set-1",
        items: [
          {
            unit_id: "u1",
            block_id: "blk-p1",
            text_ja: "整流フローは常微分方程式である。",
            content_ja: null,
            state: "machine",
            quality_flags: [],
            proposal: null,
          },
        ],
      },
    } as never);
    vi.mocked(viewerGetDocument).mockResolvedValue({
      data: {
        revision_id: "revision-1",
        quality_level: "A",
        sections: [
          {
            id: "section-1",
            heading: { number: "1", title: "Intro" },
            blocks: [{ id: "blk-p1", type: "paragraph", inlines: [{ t: "text", v: "The rectified flow is an ODE." }] }],
          },
        ],
      },
    } as never);
    vi.mocked(annotationsCreate).mockResolvedValue({ data: {} } as never);
  });

  test("highlighting a source-cell selection creates a source-side annotation", async () => {
    renderWithClient(
      <BilingualPane
        itemId="item-1"
        revisionId="revision-1"
        style="natural"
        translationSetId="set-1"
        translationStatus="complete"
        toc={[]}
        lastPosition={null}
      />,
    );
    const sourceText = await screen.findByText("The rectified flow is an ODE.");
    // Select the whole source cell text.
    const range = document.createRange();
    range.selectNodeContents(sourceText);
    const sel = window.getSelection()!;
    sel.removeAllRanges();
    sel.addRange(range);
    fireEvent.pointerUp(sourceText);
    // 4 color dots + comment; click the first color dot ("重要でハイライト").
    fireEvent.click(await screen.findByLabelText("重要でハイライト"));
    await waitFor(() => expect(annotationsCreate).toHaveBeenCalled());
    expect(vi.mocked(annotationsCreate).mock.calls[0][0]).toMatchObject({
      path: { item_id: "item-1" },
      body: { kind: "highlight", anchor: { side: "source", block_id: "blk-p1" } },
    });
  });
});
```

Add `waitFor` to the `@testing-library/react` import if not present.

- [ ] **Step 7: Run the pane tests + typecheck**

Run: `pnpm --filter @alinea/web test BilingualPane && pnpm --filter @alinea/web typecheck`
Expected: PASS. (Note: `SelectionMenu` renders only when `!isMobile`; jsdom `useIsMobile()` defaults to desktop, so the menu appears.)

- [ ] **Step 8: Commit**

```bash
git add apps/web/src/components/viewer/BilingualPane.tsx apps/web/src/components/viewer/BilingualPane.test.tsx
git commit -m "feat(web): enable annotation creation in the bilingual pane"
```

---

## Task 5: Enable annotation creation in `SourcePane`

**Files:**
- Modify: `apps/web/src/components/viewer/SourcePane.tsx` (scroll region l.262-265; imports)
- Test: `apps/web/src/components/viewer/SourcePane.test.tsx`

**Interfaces:**
- Consumes: `useAnnotationSelection` (Task 2).
- Produces: nothing new.

- [ ] **Step 1: Wire the hook** — add the import:

```ts
import { useAnnotationSelection } from "@/hooks/use-annotation-selection";
```

After the store selectors (after l.86 `requestScroll`), add:

```ts
const { onPointerUp, selectionMenu } = useAnnotationSelection({
  itemId,
  revisionId,
  defaultSide: "source",
});
```

Attach `onPointerUp={onPointerUp}` to the scroll-region `<div ref={scrollRef} ...>` (l.262-264). Render `{selectionMenu}` just before the outer return's closing `</div>` (after the scroll region `</div>` at l.276, before l.277).

- [ ] **Step 2: Write the creation smoke test** — create/extend `SourcePane.test.tsx`. Mirror the BilingualPane test but expect `side: "source"` and that "語彙に追加" is enabled. Mock `annotationsList`, `viewerGetDocument`, `annotationsCreate`.

```tsx
describe("SourcePane annotation creation", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(annotationsList).mockResolvedValue({
      data: { items: [], counts: { all: 0, important: 0, question: 0, idea: 0, term: 0, with_comment: 0, unplaced: 0 } },
    } as never);
    vi.mocked(viewerGetDocument).mockResolvedValue({
      data: {
        revision_id: "revision-1",
        quality_level: "A",
        sections: [
          {
            id: "section-1",
            heading: { number: "1", title: "Intro" },
            blocks: [{ id: "blk-s1", type: "paragraph", inlines: [{ t: "text", v: "A source sentence." }] }],
          },
        ],
      },
    } as never);
    vi.mocked(annotationsCreate).mockResolvedValue({ data: {} } as never);
  });

  test("highlighting creates a source-side annotation and 語彙に追加 is enabled", async () => {
    renderWithClient(
      <SourcePane itemId="item-1" revisionId="revision-1" toc={[]} lastPosition={null} />,
    );
    const text = await screen.findByText("A source sentence.");
    const range = document.createRange();
    range.selectNodeContents(text);
    const sel = window.getSelection()!;
    sel.removeAllRanges();
    sel.addRange(range);
    fireEvent.pointerUp(text);
    // 語彙に追加 is enabled for source selections.
    expect(await screen.findByRole("menuitem", { name: "語彙に追加" })).not.toBeDisabled();
    fireEvent.click(screen.getByLabelText("重要でハイライト"));
    await waitFor(() => expect(annotationsCreate).toHaveBeenCalled());
    expect(vi.mocked(annotationsCreate).mock.calls[0][0]).toMatchObject({
      body: { kind: "highlight", anchor: { side: "source", block_id: "blk-s1" } },
    });
  });
});
```

Include the standard file scaffolding (imports, `vi.mock("@alinea/api-client", ...)` with `annotationsList`, `viewerGetDocument`, `annotationsCreate`; `FakeIntersectionObserver` stub; local `renderWithClient`). If `SourcePane.test.tsx` already exists, extend it and its mock rather than recreating.

- [ ] **Step 3: Run the pane tests + typecheck**

Run: `pnpm --filter @alinea/web test SourcePane && pnpm --filter @alinea/web typecheck`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add apps/web/src/components/viewer/SourcePane.tsx apps/web/src/components/viewer/SourcePane.test.tsx
git commit -m "feat(web): enable annotation creation in the source pane"
```

---

## Task 6: Chat "new conversation" (clear) button

**Files:**
- Modify: `apps/web/src/components/chat/ChatPanel.tsx` (ThreadBar l.399-452; imports; `useState`)
- Test: `apps/web/src/components/chat/ChatPanel.test.tsx`

**Interfaces:**
- Consumes: `chatCreateThread` from `@alinea/api-client` (`{ path: { item_id }, body: { title: string } }` → returns `ChatThread`).
- Produces: a `createConversation` handler that sets `activeThreadId` to the new thread and invalidates `["chat-threads", itemId]`.

- [ ] **Step 1: Write the failing test** — add to `ChatPanel.test.tsx`. Extend the `vi.mock("@alinea/api-client", ...)` return to include `chatCreateThread: vi.fn()`. New describe:

```tsx
// add chatCreateThread to imports and to the vi.mock return object.
describe("ChatPanel new conversation (clear)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useViewerStore.setState({ pendingChatThreadId: null, pendingChatMessageId: null });
    vi.mocked(chatListThreads).mockResolvedValue({
      data: { items: [{ id: "th-main", title: "メイン", is_main: true, message_count: 3, last_message_at: "2026-07-10T00:00:00Z" }] },
    } as never);
    vi.mocked(chatListMessages).mockImplementation(async ({ path }) => {
      if (path.thread_id === "th-main") {
        return {
          data: {
            items: [
              { id: "m1", role: "user", blocks: [{ type: "markdown", text: "旧会話の質問", evidence: [] }], context_anchors: [], quick_action: null, status: "complete", error: null, created_at: "2026-07-10T00:00:00Z" },
            ],
          },
        } as never;
      }
      return { data: { items: [] } } as never; // new empty thread
    });
    vi.mocked(chatCreateThread).mockResolvedValue({
      data: { id: "th-new", title: "会話", is_main: false, message_count: 0, last_message_at: null },
    } as never);
  });

  test("clicking 新しい会話 creates a thread, switches to it, and clears the message view", async () => {
    renderWithClient(<ChatPanel itemId="li_1" />);
    expect(await screen.findByText("旧会話の質問")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "新しい会話" }));
    await waitFor(() => expect(chatCreateThread).toHaveBeenCalledWith(
      expect.objectContaining({ path: { item_id: "li_1" } }),
    ));
    await waitFor(() => expect(screen.queryByText("旧会話の質問")).not.toBeInTheDocument());
    expect(screen.getByText("まだ会話がありません")).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pnpm --filter @alinea/web test ChatPanel -t "新しい会話"`
Expected: FAIL — no "新しい会話" button.

- [ ] **Step 3: Implement the handler + button** — in `ChatPanel.tsx`:

Add to the SDK import (l.5-15): `chatCreateThread`.

Add the handler near `summarizeToNote` (l.342):

```ts
const [creatingThread, setCreatingThread] = useState(false);
const createConversation = useCallback(() => {
  if (creatingThread) return;
  setCreatingThread(true);
  const now = new Date();
  const title = `会話 ${now.getMonth() + 1}/${now.getDate()} ${String(now.getHours()).padStart(2, "0")}:${String(now.getMinutes()).padStart(2, "0")}`;
  void chatCreateThread({ path: { item_id: itemId }, body: { title } }).then(
    async (res) => {
      setCreatingThread(false);
      const id = res.data?.id;
      if (!id) {
        toast({ kind: "error", message: "新しい会話を作成できませんでした" });
        return;
      }
      setActiveThreadId(id);
      setLocalUser(null);
      setLocalAssistant(null);
      await qc.invalidateQueries({ queryKey: ["chat-threads", itemId] });
    },
    () => {
      setCreatingThread(false);
      toast({ kind: "error", message: "新しい会話を作成できませんでした" });
    },
  );
}, [creatingThread, itemId, qc, toast]);
```

In the ThreadBar (inside the `readOnly ? null : (<>...</>)` block, before the `⋯` button at l.401), add:

```tsx
<button
  type="button"
  aria-label="新しい会話"
  onClick={createConversation}
  disabled={creatingThread || streaming}
  style={{
    border: "none",
    background: "transparent",
    cursor: "pointer",
    color: "var(--pr-text-sub)",
    fontFamily: "inherit",
    fontSize: 11,
    padding: "0 4px",
    opacity: creatingThread || streaming ? 0.5 : 1,
  }}
>
  ＋ 新しい会話
</button>
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pnpm --filter @alinea/web test ChatPanel -t "新しい会話"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/components/chat/ChatPanel.tsx apps/web/src/components/chat/ChatPanel.test.tsx
git commit -m "feat(web): add 新しい会話 (clear chat) button to chat panel"
```

---

## Task 7: Chat history popover with delete

**Files:**
- Create: `apps/web/src/components/chat/ChatThreadHistoryPopover.tsx`
- Modify: `apps/web/src/components/chat/ChatPanel.tsx` (history button + popover + delete confirm modal)
- Test: `apps/web/src/components/chat/ChatPanel.test.tsx`

**Interfaces:**
- Consumes: `Popover`, `Modal`, `CountBadge` from `@/components/ui`; `chatDeleteThread` from `@alinea/api-client`; the `ChatThread` shape `{ id, title, is_main, message_count, last_message_at? }`.
- Produces:
  ```tsx
  export function ChatThreadHistoryPopover(props: {
    open: boolean;
    onClose: () => void;
    anchorRef: RefObject<HTMLElement | null>;
    threads: ChatThread[];
    activeThreadId: string | null;
    onSelect: (threadId: string) => void;
    onRequestDelete: (thread: ChatThread) => void;
  }): ReactNode;
  ```

- [ ] **Step 1: Write the popover component**

```tsx
// apps/web/src/components/chat/ChatThreadHistoryPopover.tsx
"use client";

import { type RefObject } from "react";
import type { ChatThread } from "@alinea/api-client";
import { Popover } from "@/components/ui/Popover";
import { CountBadge } from "@/components/ui/CountBadge";

/** 会話履歴一覧(FigureVersionPopover と同じ実装パターン)。行クリックで切替、× で削除要求。 */
export function ChatThreadHistoryPopover({
  open,
  onClose,
  anchorRef,
  threads,
  activeThreadId,
  onSelect,
  onRequestDelete,
}: {
  open: boolean;
  onClose: () => void;
  anchorRef: RefObject<HTMLElement | null>;
  threads: ChatThread[];
  activeThreadId: string | null;
  onSelect: (threadId: string) => void;
  onRequestDelete: (thread: ChatThread) => void;
}) {
  return (
    <Popover open={open} onClose={onClose} anchorRef={anchorRef} width={260} placement="bottom-end">
      <div role="menu" aria-label="会話履歴" style={{ padding: "6px 0", maxHeight: 320, overflowY: "auto" }}>
        {threads.length === 0 ? (
          <div style={{ padding: "8px 12px", fontSize: 11, color: "var(--pr-text-muted)" }}>
            会話がありません
          </div>
        ) : (
          threads.map((t) => (
            <div
              key={t.id}
              style={{
                display: "flex",
                alignItems: "center",
                gap: 6,
                padding: "6px 10px 6px 12px",
                background: t.id === activeThreadId ? "var(--pr-bg-inset)" : "transparent",
              }}
            >
              <button
                type="button"
                role="menuitem"
                onClick={() => {
                  onSelect(t.id);
                  onClose();
                }}
                style={{
                  flex: 1,
                  minWidth: 0,
                  textAlign: "left",
                  border: "none",
                  background: "transparent",
                  cursor: "pointer",
                  fontFamily: "inherit",
                  padding: 0,
                }}
              >
                <div
                  style={{
                    fontSize: 11.5,
                    fontWeight: t.id === activeThreadId ? 600 : 400,
                    color: "var(--pr-text-mid)",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                  }}
                >
                  {t.title}
                  {t.is_main ? "(メイン)" : ""}
                </div>
                <div style={{ fontSize: 10, color: "var(--pr-text-muted)", display: "flex", gap: 6, alignItems: "center" }}>
                  <CountBadge count={t.message_count} variant="tab" />
                  {t.last_message_at ? <span>{t.last_message_at.slice(0, 10)}</span> : null}
                </div>
              </button>
              {t.is_main ? null : (
                <button
                  type="button"
                  aria-label={`「${t.title}」を削除`}
                  onClick={() => onRequestDelete(t)}
                  style={{
                    border: "none",
                    background: "transparent",
                    cursor: "pointer",
                    color: "var(--pr-text-sub)",
                    fontSize: 13,
                    padding: "0 2px",
                  }}
                >
                  ×
                </button>
              )}
            </div>
          ))
        )}
      </div>
    </Popover>
  );
}
```

- [ ] **Step 2: Write the failing tests** — add to `ChatPanel.test.tsx`. Extend the SDK mock with `chatDeleteThread: vi.fn()`.

```tsx
describe("ChatPanel history", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useViewerStore.setState({ pendingChatThreadId: null, pendingChatMessageId: null });
    vi.mocked(chatListThreads).mockResolvedValue({
      data: {
        items: [
          { id: "th-main", title: "メイン", is_main: true, message_count: 2, last_message_at: "2026-07-10T00:00:00Z" },
          { id: "th-sub", title: "サブ会話", is_main: false, message_count: 5, last_message_at: "2026-07-12T00:00:00Z" },
        ],
      },
    } as never);
    vi.mocked(chatListMessages).mockResolvedValue({ data: { items: [] } } as never);
    vi.mocked(chatDeleteThread).mockResolvedValue({ data: {} } as never);
  });

  test("history popover lists threads and selecting one switches the active thread", async () => {
    renderWithClient(<ChatPanel itemId="li_1" />);
    await screen.findByText("メイン"); // ThreadBar shows active title
    fireEvent.click(screen.getByRole("button", { name: "会話履歴" }));
    const menu = await screen.findByRole("menu", { name: "会話履歴" });
    fireEvent.click(within(menu).getByText(/サブ会話/));
    await waitFor(() =>
      expect(vi.mocked(chatListMessages)).toHaveBeenCalledWith(
        expect.objectContaining({ path: { thread_id: "th-sub" } }),
      ),
    );
  });

  test("deleting a non-main thread confirms then calls chatDeleteThread", async () => {
    renderWithClient(<ChatPanel itemId="li_1" />);
    await screen.findByText("メイン");
    fireEvent.click(screen.getByRole("button", { name: "会話履歴" }));
    const menu = await screen.findByRole("menu", { name: "会話履歴" });
    fireEvent.click(within(menu).getByRole("button", { name: "「サブ会話」を削除" }));
    // confirm modal
    fireEvent.click(await screen.findByRole("button", { name: "削除する" }));
    await waitFor(() =>
      expect(chatDeleteThread).toHaveBeenCalledWith({ path: { thread_id: "th-sub" } }),
    );
  });

  test("main thread has no delete control", async () => {
    renderWithClient(<ChatPanel itemId="li_1" />);
    await screen.findByText("メイン");
    fireEvent.click(screen.getByRole("button", { name: "会話履歴" }));
    await screen.findByRole("menu", { name: "会話履歴" });
    expect(screen.queryByRole("button", { name: "「メイン」を削除" })).toBeNull();
  });
});
```

Add `within` to the `@testing-library/react` import.

- [ ] **Step 3: Run tests to verify they fail**

Run: `pnpm --filter @alinea/web test ChatPanel -t "history"`
Expected: FAIL — no "会話履歴" button / popover.

- [ ] **Step 4: Wire the popover + delete into `ChatPanel`** — add imports:

```ts
import { ChatThreadHistoryPopover } from "@/components/chat/ChatThreadHistoryPopover";
import { Modal } from "@/components/ui/Modal";
import { chatDeleteThread, type ChatThread } from "@alinea/api-client";
```

Add state + refs near l.127:

```ts
const [historyOpen, setHistoryOpen] = useState(false);
const [pendingDelete, setPendingDelete] = useState<ChatThread | null>(null);
const historyAnchor = useRef<HTMLButtonElement>(null);
```

Add the delete handler near `createConversation`:

```ts
const confirmDelete = useCallback(() => {
  const target = pendingDelete;
  if (!target) return;
  setPendingDelete(null);
  void chatDeleteThread({ path: { thread_id: target.id } }).then(
    async () => {
      if (activeThreadId === target.id) {
        const main = (threadsQuery.data?.items ?? []).find((t) => t.is_main);
        setActiveThreadId(main?.id ?? null);
      }
      await qc.invalidateQueries({ queryKey: ["chat-threads", itemId] });
      toast({ kind: "success", message: "会話を削除しました" });
    },
    () => toast({ kind: "error", message: "会話を削除できませんでした" }),
  );
}, [pendingDelete, activeThreadId, threadsQuery.data, qc, itemId, toast]);
```

In the ThreadBar `readOnly ? null : (<>...</>)` block, add the history button (before or after the `⋯`), plus the popover + confirm modal:

```tsx
<button
  ref={historyAnchor}
  type="button"
  aria-label="会話履歴"
  aria-haspopup="menu"
  aria-expanded={historyOpen}
  onClick={() => setHistoryOpen((v) => !v)}
  style={{
    border: "none",
    background: "transparent",
    cursor: "pointer",
    color: "var(--pr-text-sub)",
    fontFamily: "inherit",
    fontSize: 11,
    padding: "0 4px",
  }}
>
  履歴
</button>
<ChatThreadHistoryPopover
  open={historyOpen}
  onClose={() => setHistoryOpen(false)}
  anchorRef={historyAnchor}
  threads={threadsQuery.data?.items ?? []}
  activeThreadId={activeThreadId}
  onSelect={(id) => {
    setActiveThreadId(id);
    setLocalUser(null);
    setLocalAssistant(null);
  }}
  onRequestDelete={(t) => {
    setHistoryOpen(false);
    setPendingDelete(t);
  }}
/>
```

Add the confirm modal near the end of the component's returned tree (before the final `</div>` at l.525):

```tsx
<Modal
  open={pendingDelete !== null}
  onClose={() => setPendingDelete(null)}
  labelledBy="delete-thread-title"
  width={380}
>
  <div style={{ padding: 20, display: "flex", flexDirection: "column", gap: 14 }}>
    <div id="delete-thread-title" style={{ fontSize: 14, fontWeight: 600, color: "var(--pr-text)" }}>
      この会話を削除しますか?
    </div>
    <div style={{ fontSize: 12, color: "var(--pr-text-sub)" }}>
      「{pendingDelete?.title}」を削除します。この操作は取り消せません。
    </div>
    <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
      <button
        type="button"
        onClick={() => setPendingDelete(null)}
        style={{ border: "1px solid var(--pr-border-control)", background: "transparent", cursor: "pointer", fontFamily: "inherit", fontSize: 12, borderRadius: 6, padding: "6px 12px", color: "var(--pr-text-mid)" }}
      >
        キャンセル
      </button>
      <button
        type="button"
        onClick={confirmDelete}
        style={{ border: "none", background: "var(--pr-warn)", color: "#FFFFFF", cursor: "pointer", fontFamily: "inherit", fontSize: 12, fontWeight: 600, borderRadius: 6, padding: "6px 14px" }}
      >
        削除する
      </button>
    </div>
  </div>
</Modal>
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pnpm --filter @alinea/web test ChatPanel && pnpm --filter @alinea/web typecheck`
Expected: PASS (all ChatPanel tests, including prior deep-link/new-conversation ones).

- [ ] **Step 6: Commit**

```bash
git add apps/web/src/components/chat/ChatThreadHistoryPopover.tsx apps/web/src/components/chat/ChatPanel.tsx apps/web/src/components/chat/ChatPanel.test.tsx
git commit -m "feat(web): chat history popover with delete + confirm"
```

---

## Task 8: Full verification pass

**Files:** none (verification only).

- [ ] **Step 1: Run the full web test suite**

Run: `pnpm --filter @alinea/web test`
Expected: PASS (all suites green).

- [ ] **Step 2: Typecheck + lint**

Run: `pnpm --filter @alinea/web typecheck && pnpm --filter @alinea/web lint`
Expected: PASS with no errors.

- [ ] **Step 3: Manual end-to-end (use the `/run` skill to launch the app)** — open a paper viewer and confirm:
  - Chat tab: 「＋ 新しい会話」 empties the message area; 「履歴」 lists conversations; selecting a past one restores its messages; deleting a non-main conversation works after confirm; メイン has no × .
  - Bilingual (対訳) pane: selecting source or translation text shows the selection menu; highlight / comment / AIに質問 / (source only) 語彙に追加 all work; the highlight appears in the pane and in the annotations tab.
  - Source (原文) pane: same, with side fixed to source and 語彙に追加 enabled.
  - 「AIに質問」 from any pane attaches the selected text as a context chip in the chat composer (not just opening the tab).

- [ ] **Step 4: Final commit if any verification fixups were needed** (otherwise skip).

---

## Self-Review Notes

- **Spec coverage:** Feature 1 clear → Task 6; history + delete → Task 7. Feature 2 shared hook → Tasks 1-2; TranslationPane refactor → Task 3; bilingual → Task 4; source → Task 5; ask-AI quote attach → built into Task 2's `askAI`; bilingual `data-block-id` gap → Task 4 Step 3. Verification → Task 8. All spec sections mapped.
- **Type consistency:** `useAnnotationSelection` returns `{ onPointerUp, selectionMenu }` and is consumed identically in Tasks 3-5. `resolveSelectionAnchor(range, text, defaultSide)` signature is stable across Tasks 1-2. `ChatThread` fields (`id, title, is_main, message_count, last_message_at`) match the generated type used in Task 7. `chatCreateThread` body is `{ title: string }` (matches `ThreadCreateRequest`); `chatDeleteThread` takes `{ path: { thread_id } }`.
- **No placeholders:** every code step shows complete code; every run step shows the command + expected result.
