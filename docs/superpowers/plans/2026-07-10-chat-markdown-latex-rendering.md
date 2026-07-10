# Chat Markdown and LaTeX Rendering Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Render assistant and aside chat blocks as safe GFM Markdown with inline/block KaTeX while preserving evidence chips, streaming, and narrow-panel layout.

**Architecture:** Add a focused `ChatMarkdown` component backed by `react-markdown`, GFM, math, and KaTeX plugins. A preprocessing/plugin module normalizes same-line `$$...$$` into block math and converts verified `[[ev:n]]` text markers into custom React evidence chips; `ChatMessage` remains responsible for message chrome and actions.

**Tech Stack:** React 19, TypeScript 5.8, react-markdown 10, remark-gfm 4, remark-math 6, rehype-katex 7, KaTeX 0.16, Vitest, Testing Library, pnpm.

---

**Design spec:** `docs/superpowers/specs/2026-07-10-chat-markdown-latex-rendering-design.md`

## File map

- Create `apps/web/src/components/chat/chat-markdown-plugins.ts`: display-math normalization and evidence-marker MDAST transform.
- Create `apps/web/src/components/chat/chat-markdown-plugins.test.ts`: pure transformation tests, including streaming-incomplete delimiters and code protection.
- Create `apps/web/src/components/chat/ChatMarkdown.tsx`: safe Markdown/KaTeX React renderer and evidence-chip adapter.
- Create `apps/web/src/components/chat/ChatMarkdown.test.tsx`: GFM, safety, evidence, math, fallback, and streaming tests.
- Modify `apps/web/src/components/chat/ChatMessage.tsx`: delegate assistant/aside bodies to `ChatMarkdown` while leaving user bodies plain.
- Modify `apps/web/src/components/chat/ChatPanel.test.tsx`: integration coverage for assistant, aside, and user rendering.
- Delete `apps/web/src/components/chat/EvidenceHighlight.tsx`: superseded lightweight parser.
- Modify `apps/web/src/lib/katex-render.ts` and its test: expose a fresh shared macro map for both existing and chat renderers.
- Modify `apps/web/src/styles/globals.css`: scoped chat typography and overflow containment.
- Modify `apps/web/package.json` and `pnpm-lock.yaml`: add the Markdown pipeline dependencies.
- Modify `docs/05-chat.md`, `plans/09-screens/1a-viewer-parallel-chat.md`, and `plans/12-testing.md`: align product and test documentation with full GFM/block-math behavior.

### Task 1: Add the Markdown pipeline dependencies

**Files:**
- Modify: `apps/web/package.json`
- Modify: `pnpm-lock.yaml`

- [ ] **Step 1: Add exact runtime dependencies**

Run:

```bash
pnpm --filter @alinea/web add react-markdown@10.1.0 remark-gfm@4.0.1 remark-math@6.0.0 rehype-katex@7.0.1 unified@11.0.5 unist-util-visit@5.0.0
```

Expected: pnpm exits 0 and adds all six packages under `@alinea/web` dependencies.

- [ ] **Step 2: Add the direct MDAST type dependency**

Run:

```bash
pnpm --filter @alinea/web add -D @types/mdast@4.0.4
```

Expected: pnpm exits 0 and adds `@types/mdast` under `@alinea/web` devDependencies.

- [ ] **Step 3: Verify dependency resolution**

Run:

```bash
pnpm --filter @alinea/web exec node --input-type=module -e "await Promise.all([import('react-markdown'), import('remark-gfm'), import('remark-math'), import('rehype-katex'), import('unified'), import('unist-util-visit')]); console.log('markdown dependencies resolved')"
```

Expected: output contains `markdown dependencies resolved` and exits 0.

- [ ] **Step 4: Commit dependency setup**

```bash
git add apps/web/package.json pnpm-lock.yaml
git commit -m "chore(web): add chat markdown rendering dependencies"
```

### Task 2: Build and test chat-specific Markdown transforms

**Files:**
- Create: `apps/web/src/components/chat/chat-markdown-plugins.test.ts`
- Create: `apps/web/src/components/chat/chat-markdown-plugins.ts`

- [ ] **Step 1: Write failing transform tests**

Create `apps/web/src/components/chat/chat-markdown-plugins.test.ts`:

```ts
import type { Root } from "mdast";
import { describe, expect, test } from "vitest";
import {
  EVIDENCE_PROPERTY,
  normalizeDisplayMath,
  replaceEvidenceMarkers,
} from "@/components/chat/chat-markdown-plugins";

describe("normalizeDisplayMath", () => {
  test("moves same-line double-dollar math onto flow lines", () => {
    expect(normalizeDisplayMath("前 $$x^2$$ 後")).toBe(
      "前 \n\n$$\nx^2\n$$\n\n 後",
    );
  });

  test("keeps existing multiline display math as a display block", () => {
    const result = normalizeDisplayMath("前\n\n$$\nx^2 + y^2\n$$\n\n後");

    expect(result).toContain("$$\nx^2 + y^2\n$$");
    expect(result.indexOf("前")).toBeLessThan(result.indexOf("$$"));
    expect(result.indexOf("$$\nx^2 + y^2\n$$")).toBeLessThan(result.indexOf("後"));
  });

  test("does not rewrite double dollars in inline or fenced code", () => {
    const markdown = [
      "`$$inline$$`",
      "",
      "```tex",
      "$$fenced$$",
      "```",
    ].join("\n");

    expect(normalizeDisplayMath(markdown)).toBe(markdown);
  });

  test("leaves an unfinished display delimiter visible while streaming", () => {
    expect(normalizeDisplayMath("生成中 $$\\frac{a}{b}")).toBe(
      "生成中 $$\\frac{a}{b}",
    );
  });
});

describe("replaceEvidenceMarkers", () => {
  test("splits text and annotates evidence links with an internal property", () => {
    const tree: Root = {
      type: "root",
      children: [
        {
          type: "paragraph",
          children: [{ type: "text", value: "前 [[ev:12]] 後" }],
        },
      ],
    };

    replaceEvidenceMarkers(tree);

    expect(tree).toMatchObject({
      children: [
        {
          type: "paragraph",
          children: [
            { type: "text", value: "前 " },
            {
              type: "link",
              url: "#",
              data: { hProperties: { [EVIDENCE_PROPERTY]: 12 } },
            },
            { type: "text", value: " 後" },
          ],
        },
      ],
    });
  });

  test("does not transform markers inside inline or fenced code nodes", () => {
    const tree: Root = {
      type: "root",
      children: [
        {
          type: "paragraph",
          children: [{ type: "inlineCode", value: "[[ev:1]]" }],
        },
        { type: "code", lang: "txt", value: "[[ev:2]]" },
      ],
    };

    replaceEvidenceMarkers(tree);

    expect(tree).toMatchObject({
      children: [
        {
          type: "paragraph",
          children: [{ type: "inlineCode", value: "[[ev:1]]" }],
        },
        { type: "code", value: "[[ev:2]]" },
      ],
    });
  });
});
```

- [ ] **Step 2: Run the transform tests and verify RED**

Run:

```bash
pnpm --dir apps/web exec vitest run src/components/chat/chat-markdown-plugins.test.ts
```

Expected: FAIL because `chat-markdown-plugins.ts` does not exist.

- [ ] **Step 3: Implement the transforms**

Create `apps/web/src/components/chat/chat-markdown-plugins.ts`:

```ts
import type { Link, PhrasingContent, Root, Text } from "mdast";
import type { Plugin } from "unified";
import { visit } from "unist-util-visit";

export const EVIDENCE_PROPERTY = "data-alinea-evidence-ref";

const EVIDENCE_RE = /\[\[ev:(\d+)\]\]/g;

interface Fence {
  char: "`" | "~";
  size: number;
}

function countRun(value: string, start: number, char: string): number {
  let end = start;
  while (value[end] === char) end += 1;
  return end - start;
}

function isEscaped(value: string, index: number): boolean {
  let slashes = 0;
  for (let i = index - 1; i >= 0 && value[i] === "\\"; i -= 1) slashes += 1;
  return slashes % 2 === 1;
}

function matchingBacktickEnd(value: string, start: number): number {
  const size = countRun(value, start, "`");
  let cursor = start + size;
  while (cursor < value.length) {
    const next = value.indexOf("`", cursor);
    if (next < 0) return -1;
    const nextSize = countRun(value, next, "`");
    if (nextSize === size) return next + nextSize;
    cursor = next + nextSize;
  }
  return -1;
}

function closingDoubleDollar(value: string, start: number): number {
  for (let i = start; i < value.length - 1; i += 1) {
    if (value[i] === "$" && value[i + 1] === "$" && !isEscaped(value, i)) return i;
  }
  return -1;
}

function openingFence(line: string): Fence | null {
  const marker = /^ {0,3}(`{3,}|~{3,})/.exec(line)?.[1];
  if (!marker) return null;
  return { char: marker[0] as Fence["char"], size: marker.length };
}

function closesFence(line: string, fence: Fence): boolean {
  const marker = /^ {0,3}(`{3,}|~{3,})[ \t]*$/.exec(line)?.[1];
  return Boolean(marker && marker[0] === fence.char && marker.length >= fence.size);
}

function addBlankLine(value: string): string {
  if (!value || value.endsWith("\n\n")) return value;
  return value.endsWith("\n") ? value + "\n" : value + "\n\n";
}

/**
 * remark-math treats same-line double-dollar spans as inline math. Chat prompts
 * define all complete double-dollar pairs as display math, so move them onto
 * flow lines. Code and unfinished streaming input stay unchanged.
 */
export function normalizeDisplayMath(markdown: string): string {
  let output = "";
  let index = 0;
  let fence: Fence | null = null;

  while (index < markdown.length) {
    const atLineStart = index === 0 || markdown[index - 1] === "\n";
    if (atLineStart) {
      const newline = markdown.indexOf("\n", index);
      const lineEnd = newline < 0 ? markdown.length : newline;
      const nextLine = newline < 0 ? lineEnd : newline + 1;
      const line = markdown.slice(index, lineEnd);

      if (fence) {
        output += markdown.slice(index, nextLine);
        if (closesFence(line, fence)) fence = null;
        index = nextLine;
        continue;
      }

      const opened = openingFence(line);
      if (opened) {
        fence = opened;
        output += markdown.slice(index, nextLine);
        index = nextLine;
        continue;
      }
    }

    if (markdown[index] === "`") {
      const end = matchingBacktickEnd(markdown, index);
      if (end < 0) return output + markdown.slice(index);
      output += markdown.slice(index, end);
      index = end;
      continue;
    }

    if (
      markdown[index] === "$" &&
      markdown[index + 1] === "$" &&
      !isEscaped(markdown, index)
    ) {
      const close = closingDoubleDollar(markdown, index + 2);
      if (close < 0) return output + markdown.slice(index);
      const content = markdown.slice(index + 2, close).trim();
      if (!content) {
        output += "$$$$";
        index = close + 2;
        continue;
      }
      output = addBlankLine(output);
      output += "$$\n" + content + "\n$$\n\n";
      index = close + 2;
      continue;
    }

    output += markdown[index];
    index += 1;
  }

  return output;
}

function evidenceLink(ref: number): Link {
  return {
    type: "link",
    url: "#",
    children: [{ type: "text", value: "" }],
    data: { hProperties: { [EVIDENCE_PROPERTY]: ref } },
  };
}

export function replaceEvidenceMarkers(tree: Root): void {
  visit(tree, "text", (node: Text, index, parent) => {
    if (index === undefined || !parent || !node.value.includes("[[ev:")) return;

    const replacement: PhrasingContent[] = [];
    let cursor = 0;
    for (const match of node.value.matchAll(EVIDENCE_RE)) {
      const start = match.index ?? 0;
      if (start > cursor) {
        replacement.push({ type: "text", value: node.value.slice(cursor, start) });
      }
      replacement.push(evidenceLink(Number(match[1])));
      cursor = start + match[0].length;
    }
    if (replacement.length === 0) return;
    if (cursor < node.value.length) {
      replacement.push({ type: "text", value: node.value.slice(cursor) });
    }

    const siblings = parent.children as PhrasingContent[];
    siblings.splice(index, 1, ...replacement);
    return index + replacement.length;
  });
}

export const remarkEvidence: Plugin<[], Root> = () => replaceEvidenceMarkers;
```

- [ ] **Step 4: Run the transform tests and verify GREEN**

Run:

```bash
pnpm --dir apps/web exec vitest run src/components/chat/chat-markdown-plugins.test.ts
```

Expected: 6 tests PASS.

- [ ] **Step 5: Commit the transform layer**

```bash
git add apps/web/src/components/chat/chat-markdown-plugins.ts apps/web/src/components/chat/chat-markdown-plugins.test.ts
git commit -m "feat(web): parse chat display math and evidence markers"
```

### Task 3: Add the safe GFM chat renderer

**Files:**
- Create: `apps/web/src/components/chat/ChatMarkdown.test.tsx`
- Create: `apps/web/src/components/chat/ChatMarkdown.tsx`

- [ ] **Step 1: Write failing GFM, evidence, and safety tests**

Create `apps/web/src/components/chat/ChatMarkdown.test.tsx`:

```tsx
import { fireEvent, render, screen } from "@testing-library/react";
import type { AnchorRef, EvidenceRef } from "@alinea/api-client";
import { describe, expect, test, vi } from "vitest";
import { ChatMarkdown } from "@/components/chat/ChatMarkdown";

function anchorRef(): AnchorRef {
  return {
    revision_id: "rev-1",
    block_id: "blk-1",
    start: null,
    end: null,
    quote: null,
    side: "source",
    display: "式(1)",
  };
}

function evidence(): EvidenceRef[] {
  return [{ ref: 1, display: "式(1)", anchor: anchorRef() }];
}

describe("ChatMarkdown GFM", () => {
  test("renders headings, emphasis, lists, rules, tables, quotes, and code blocks", () => {
    const markdown = [
      "### 実験設定",
      "",
      "**重要** *補足*",
      "",
      "- データセット",
      "- ベースライン",
      "",
      "1. 手順A",
      "2. 手順B",
      "",
      "> 注意事項",
      "",
      "---",
      "",
      "| 項目 | 値 |",
      "| --- | --- |",
      "| batch | 64 |",
      "",
      "```python",
      "print('ok')",
      "```",
    ].join("\n");
    const { container } = render(<ChatMarkdown text={markdown} evidence={[]} />);

    expect(screen.getByRole("heading", { level: 3, name: "実験設定" })).toBeInTheDocument();
    expect(screen.getByText("重要").tagName).toBe("STRONG");
    expect(screen.getByText("補足").tagName).toBe("EM");
    expect(screen.getAllByRole("list")).toHaveLength(2);
    expect(screen.getByText("注意事項").closest("blockquote")).not.toBeNull();
    expect(container.querySelector("hr")).not.toBeNull();
    expect(screen.getByRole("table")).toBeInTheDocument();
    expect(container.querySelector(".alinea-chat-table-scroll")).not.toBeNull();
    expect(screen.getByText("print('ok')").closest("pre")).toHaveClass(
      "alinea-chat-code-block",
    );
  });
});

describe("ChatMarkdown evidence", () => {
  test("renders verified markers inside headings and table cells as jump chips", () => {
    const onJump = vi.fn();
    render(
      <ChatMarkdown
        text={[
          "### 結論 [[ev:1]]",
          "",
          "| 根拠 |",
          "| --- |",
          "| [[ev:1]] |",
          "",
          "未解決 [[ev:99]]",
        ].join("\n")}
        evidence={evidence()}
        onEvidenceJump={onJump}
      />,
    );

    const chips = screen.getAllByRole("button", { name: "式(1)" });
    expect(chips).toHaveLength(2);
    fireEvent.click(chips[1] as HTMLButtonElement);
    expect(onJump).toHaveBeenCalledWith(anchorRef());
    expect(screen.queryByText("[[ev:99]]")).toBeNull();
  });

  test("keeps marker syntax literal inside inline and fenced code", () => {
    render(
      <ChatMarkdown
        text={["`[[ev:1]]`", "", "```txt", "[[ev:1]]", "```"].join("\n")}
        evidence={evidence()}
      />,
    );

    expect(screen.queryByRole("button", { name: "式(1)" })).toBeNull();
    expect(screen.getAllByText("[[ev:1]]")).toHaveLength(2);
  });
});

describe("ChatMarkdown safety", () => {
  test("keeps safe links external and removes dangerous link behavior", () => {
    render(
      <ChatMarkdown
        text={"[公式](https://example.com) [危険](javascript:alert(1))"}
        evidence={[]}
      />,
    );

    const safe = screen.getByRole("link", { name: "公式" });
    expect(safe).toHaveAttribute("href", "https://example.com");
    expect(safe).toHaveAttribute("target", "_blank");
    expect(safe).toHaveAttribute("rel", "noopener noreferrer");
    expect(screen.getByText("危険").closest("a")).toBeNull();
  });

  test("does not execute raw HTML or create external image requests", () => {
    const { container } = render(
      <ChatMarkdown
        text={'<script>alert("x")</script>\n\n![秘密](https://example.com/secret.png)'}
        evidence={[]}
      />,
    );

    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("img")).toBeNull();
    expect(screen.getByText("画像: 秘密")).toBeInTheDocument();
    expect(container.textContent).not.toContain('alert("x")');
  });
});
```

- [ ] **Step 2: Run the renderer tests and verify RED**

Run:

```bash
pnpm --dir apps/web exec vitest run src/components/chat/ChatMarkdown.test.tsx
```

Expected: FAIL because `ChatMarkdown.tsx` does not exist.

- [ ] **Step 3: Implement the minimal safe GFM renderer**

Create `apps/web/src/components/chat/ChatMarkdown.tsx`:

```tsx
"use client";

import { Fragment, type ReactNode } from "react";
import type { AnchorRef, EvidenceRef } from "@alinea/api-client";
import Markdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { EvidenceChip } from "@/components/ui/EvidenceChip";
import {
  EVIDENCE_PROPERTY,
  remarkEvidence,
} from "@/components/chat/chat-markdown-plugins";

export interface ChatMarkdownProps {
  text: string;
  evidence: EvidenceRef[];
  onEvidenceJump?: (anchor: AnchorRef) => void;
}

interface MarkdownElement {
  properties?: Record<string, unknown>;
}

function evidenceRefFromNode(node: MarkdownElement | undefined): number | null {
  const value = node?.properties?.[EVIDENCE_PROPERTY];
  if (typeof value === "number" && Number.isInteger(value)) return value;
  if (typeof value === "string" && /^\d+$/.test(value)) return Number(value);
  return null;
}

export function ChatMarkdown({
  text,
  evidence,
  onEvidenceJump,
}: ChatMarkdownProps): ReactNode {
  const byRef = new Map(evidence.map((item) => [item.ref, item]));

  const components: Components = {
    a({ node, href, children, ...props }) {
      const ref = evidenceRefFromNode(node);
      if (ref !== null) {
        const item = byRef.get(ref);
        if (!item) return null;
        return (
          <EvidenceChip
            anchor={{ type: "section", sectionNumber: item.display }}
            label={item.display}
            size="inline"
            onJump={() => onEvidenceJump?.(item.anchor)}
          />
        );
      }

      if (!href) return <Fragment>{children}</Fragment>;
      return (
        <a {...props} href={href} target="_blank" rel="noopener noreferrer">
          {children}
        </a>
      );
    },
    table({ node: _node, children, ...props }) {
      return (
        <div
          className="alinea-chat-table-scroll"
          role="region"
          aria-label="Markdown表"
          tabIndex={0}
        >
          <table {...props}>{children}</table>
        </div>
      );
    },
    pre({ node: _node, children, ...props }) {
      return (
        <pre {...props} className="alinea-chat-code-block">
          {children}
        </pre>
      );
    },
    img({ node: _node, alt }) {
      return alt ? <span className="alinea-chat-image-alt">画像: {alt}</span> : null;
    },
  };

  return (
    <div className="alinea-chat-markdown">
      <Markdown
        skipHtml
        remarkPlugins={[remarkGfm, remarkEvidence]}
        components={components}
      >
        {text}
      </Markdown>
    </div>
  );
}
```

- [ ] **Step 4: Run the renderer tests and verify GREEN**

Run:

```bash
pnpm --dir apps/web exec vitest run src/components/chat/ChatMarkdown.test.tsx
```

Expected: 5 tests PASS.

- [ ] **Step 5: Run the focused type check**

Run:

```bash
pnpm --filter @alinea/web typecheck
```

Expected: TypeScript exits 0. Preserve exported `Components` callback types and do not weaken the implementation with `any`.

- [ ] **Step 6: Commit the safe Markdown renderer**

```bash
git add apps/web/src/components/chat/ChatMarkdown.tsx apps/web/src/components/chat/ChatMarkdown.test.tsx
git commit -m "feat(web): render safe GFM in chat messages"
```

### Task 4: Add KaTeX rendering and narrow-panel layout

**Files:**
- Modify: `apps/web/src/components/chat/ChatMarkdown.test.tsx`
- Modify: `apps/web/src/components/chat/ChatMarkdown.tsx`
- Modify: `apps/web/src/lib/katex-render.test.ts`
- Modify: `apps/web/src/lib/katex-render.ts`
- Modify: `apps/web/src/styles/globals.css`

- [ ] **Step 1: Add failing math and streaming tests**

Append to `ChatMarkdown.test.tsx`:

```tsx
describe("ChatMarkdown math", () => {
  test("renders inline math and same-line or multiline double dollars as blocks", () => {
    const markdown = [
      "速度は $v_t$。",
      "",
      "前 $$\\underbrace{x_1 + x_2}_{long\\ label}$$ 後",
      "",
      "$$",
      "\\sum_{i=1}^{n} x_i",
      "$$",
    ].join("\n");
    const { container } = render(<ChatMarkdown text={markdown} evidence={[]} />);

    expect(container.querySelectorAll(".katex")).toHaveLength(3);
    expect(
      container.querySelectorAll(".alinea-chat-math-block .katex-display"),
    ).toHaveLength(2);
  });

  test("uses shared paper macros and keeps invalid latex readable", () => {
    const { container } = render(
      <ChatMarkdown
        text={"既知 $\\student(x)$\n\n$$\n\\notacommand{\n$$"}
        evidence={[]}
      />,
    );

    expect(container.querySelector(".katex")).not.toBeNull();
    expect(container.querySelector(".katex-error")).not.toBeNull();
    expect(container.textContent).toContain("\\notacommand{");
  });

  test("keeps unfinished math visible, then renders it when streaming closes", () => {
    const { container, rerender } = render(
      <ChatMarkdown text={"途中 $$\\frac{a}{b}"} evidence={[]} />,
    );

    expect(container.querySelector(".katex")).toBeNull();
    expect(container.textContent).toContain("$$\\frac{a}{b}");

    rerender(<ChatMarkdown text={"途中 $$\\frac{a}{b}$$"} evidence={[]} />);
    expect(
      container.querySelector(".alinea-chat-math-block .katex-display"),
    ).not.toBeNull();
  });
});
```

Replace the import in `apps/web/src/lib/katex-render.test.ts` with:

```ts
import {
  createKatexMacros,
  renderBlockMath,
  renderInlineMath,
} from "@/lib/katex-render";
```

Append:

```ts
test("returns a fresh shared macro map for plugin renderers", () => {
  const first = createKatexMacros();
  const second = createKatexMacros();

  first["\\temporary"] = "x";
  expect(second["\\temporary"]).toBeUndefined();
  expect(second["\\student"]).toBe("\\operatorname{student}");
});
```

- [ ] **Step 2: Run math tests and verify RED**

Run:

```bash
pnpm --dir apps/web exec vitest run src/components/chat/ChatMarkdown.test.tsx src/lib/katex-render.test.ts
```

Expected: FAIL because math plugins are not wired and `createKatexMacros` is not exported.

- [ ] **Step 3: Expose fresh shared KaTeX macros**

In `apps/web/src/lib/katex-render.ts`, add after `BASE_MACROS`:

```ts
export function createKatexMacros(): Record<string, string> {
  return { ...BASE_MACROS };
}
```

In `renderMath` replace `const macros = { ...BASE_MACROS };` with:

```ts
const macros = createKatexMacros();
```

- [ ] **Step 4: Wire remark-math, rehype-katex, and block detection**

Update `ChatMarkdown.tsx` imports:

```tsx
import {
  Children,
  Fragment,
  isValidElement,
  type ReactNode,
} from "react";
import rehypeKatex from "rehype-katex";
import remarkMath from "remark-math";
import { createKatexMacros } from "@/lib/katex-render";
import {
  EVIDENCE_PROPERTY,
  normalizeDisplayMath,
  remarkEvidence,
} from "@/components/chat/chat-markdown-plugins";
```

Add above `ChatMarkdown`:

```tsx
function isDisplayMathChild(children: ReactNode): boolean {
  return Children.toArray(children).some(
    (child) =>
      isValidElement<{ className?: string }>(child) &&
      (child.props.className?.split(/\s+/).includes("katex-display") ?? false),
  );
}

const KATEX_OPTIONS = {
  macros: createKatexMacros(),
  output: "html" as const,
  strict: "ignore" as const,
  trust: false,
};
```

Replace the `pre` renderer with:

```tsx
pre({ node: _node, children, ...props }) {
  if (isDisplayMathChild(children)) {
    return <div className="alinea-chat-math-block">{children}</div>;
  }
  return (
    <pre {...props} className="alinea-chat-code-block">
      {children}
    </pre>
  );
},
```

Before returning JSX, add:

```tsx
const markdown = normalizeDisplayMath(text);
```

Replace the `Markdown` element with:

```tsx
<Markdown
  skipHtml
  remarkPlugins={[remarkGfm, remarkMath, remarkEvidence]}
  rehypePlugins={[[rehypeKatex, KATEX_OPTIONS]]}
  components={components}
>
  {markdown}
</Markdown>
```

- [ ] **Step 5: Add scoped chat layout CSS**

Append to `apps/web/src/styles/globals.css`:

```css
/* チャット回答: GFM + KaTeX。サイドパネル幅を押し広げない。 */
.alinea-chat-markdown {
  min-width: 0;
  max-width: 100%;
  color: inherit;
  font: inherit;
  line-height: inherit;
  overflow-wrap: anywhere;
}
.alinea-chat-markdown > :first-child {
  margin-top: 0;
}
.alinea-chat-markdown > :last-child {
  margin-bottom: 0;
}
.alinea-chat-markdown :is(p, ul, ol, blockquote, pre, hr) {
  margin: 0 0 8px;
}
.alinea-chat-markdown :is(h1, h2, h3, h4, h5, h6) {
  margin: 12px 0 6px;
  color: var(--pr-text-body);
  font-family: var(--pr-font-ui);
  line-height: 1.45;
}
.alinea-chat-markdown h1 {
  font-size: 15px;
}
.alinea-chat-markdown h2 {
  font-size: 14px;
}
.alinea-chat-markdown :is(h3, h4, h5, h6) {
  font-size: 13px;
}
.alinea-chat-markdown :is(ul, ol) {
  padding-left: 20px;
}
.alinea-chat-markdown li + li {
  margin-top: 3px;
}
.alinea-chat-markdown blockquote {
  padding-left: 9px;
  border-left: 2px solid var(--pr-border-quote);
  color: var(--pr-text-sub);
}
.alinea-chat-markdown hr {
  border: 0;
  border-top: 1px solid var(--pr-border-hair);
}
.alinea-chat-markdown a {
  color: var(--pr-acc);
  font-weight: 600;
  overflow-wrap: anywhere;
}
.alinea-chat-markdown :not(pre) > code {
  padding: 1px 4px;
  border-radius: 3px;
  background: var(--pr-bg-inset);
  font-family: var(--pr-font-mono);
  font-size: 0.9em;
}
.alinea-chat-code-block {
  max-width: 100%;
  overflow-x: auto;
  padding: 9px 10px;
  border: 1px solid var(--pr-border-card);
  border-radius: 6px;
  background: var(--pr-bg-inset);
  font-family: var(--pr-font-mono);
  font-size: 11px;
  line-height: 1.55;
  white-space: pre;
}
.alinea-chat-table-scroll {
  max-width: 100%;
  margin: 2px 0 8px;
  overflow-x: auto;
  border: 1px solid var(--pr-border-card);
  border-radius: 6px;
}
.alinea-chat-table-scroll table {
  width: max-content;
  min-width: 100%;
  border-collapse: collapse;
  font-size: 11px;
  line-height: 1.55;
}
.alinea-chat-table-scroll :is(th, td) {
  min-width: 88px;
  max-width: 220px;
  padding: 6px 8px;
  border-right: 1px solid var(--pr-border-hair);
  border-bottom: 1px solid var(--pr-border-hair);
  text-align: left;
  vertical-align: top;
  white-space: normal;
}
.alinea-chat-table-scroll th {
  background: var(--pr-bg-inset);
  color: var(--pr-text-mid);
  font-weight: 700;
}
.alinea-chat-table-scroll :is(th, td):last-child {
  border-right: 0;
}
.alinea-chat-table-scroll tr:last-child td {
  border-bottom: 0;
}
.alinea-chat-math-block {
  max-width: 100%;
  margin: 8px 0;
  overflow-x: auto;
  overflow-y: hidden;
  padding: 6px 2px;
}
.alinea-chat-math-block .katex-display {
  width: max-content;
  min-width: 100%;
  margin: 0;
  text-align: center;
}
.alinea-chat-markdown .katex-error {
  display: inline-block;
  max-width: 100%;
  overflow-x: auto;
  padding: 2px 4px;
  border: 1px solid var(--pr-border-card);
  border-radius: 4px;
  background: var(--pr-bg-inset);
  color: var(--pr-text-body) !important;
  font-family: var(--pr-font-mono);
  font-size: 0.9em;
}
.alinea-chat-image-alt {
  color: var(--pr-text-muted);
  font-style: italic;
}
```

- [ ] **Step 6: Run math, renderer, and type tests**

Run:

```bash
pnpm --dir apps/web exec vitest run src/components/chat/chat-markdown-plugins.test.ts src/components/chat/ChatMarkdown.test.tsx src/lib/katex-render.test.ts
pnpm --filter @alinea/web typecheck
```

Expected: all focused tests PASS and TypeScript exits 0.

- [ ] **Step 7: Commit math and layout**

```bash
git add apps/web/src/components/chat/ChatMarkdown.tsx apps/web/src/components/chat/ChatMarkdown.test.tsx apps/web/src/lib/katex-render.ts apps/web/src/lib/katex-render.test.ts apps/web/src/styles/globals.css
git commit -m "feat(web): render chat latex without panel overflow"
```

### Task 5: Integrate the renderer into chat messages

**Files:**
- Modify: `apps/web/src/components/chat/ChatPanel.test.tsx`
- Modify: `apps/web/src/components/chat/ChatMessage.tsx`
- Delete: `apps/web/src/components/chat/EvidenceHighlight.tsx`

- [ ] **Step 1: Replace lightweight-parser tests with failing message integration tests**

Remove the `EvidenceHighlight` import and its standalone
`describe("EvidenceHighlight")` block from `ChatPanel.test.tsx`.

Add these tests to `describe("ChatMessage assistant (VT-VIEW-09)")`:

```tsx
test("renders assistant GFM tables and block math through ChatMarkdown", () => {
  const { container } = render(
    <ChatMessage
      message={assistantMessage({
        blocks: [
          {
            type: "markdown",
            text: [
              "### 比較",
              "",
              "| 手法 | 損失 |",
              "| --- | --- |",
              "| ours | $L$ |",
              "",
              "$$\\underbrace{x_1 + x_2}_{model}$$",
            ].join("\n"),
            evidence: [],
          },
        ],
      })}
    />,
  );

  expect(screen.getByRole("heading", { level: 3, name: "比較" })).toBeInTheDocument();
  expect(screen.getByRole("table")).toBeInTheDocument();
  expect(
    container.querySelector(".alinea-chat-math-block .katex-display"),
  ).not.toBeNull();
});

test("renders Markdown and math inside outside-knowledge asides", () => {
  const { container } = render(
    <ChatMessage
      message={assistantMessage({
        blocks: [
          {
            type: "aside",
            label: "outside_knowledge",
            text: "**一般則**は $x^2$ です。",
          },
        ],
      })}
    />,
  );

  expect(screen.getByText("一般則").tagName).toBe("STRONG");
  expect(container.querySelector(".katex")).not.toBeNull();
});
```

Add a user-message regression suite:

```tsx
describe("ChatMessage user", () => {
  test("keeps user-authored Markdown and LaTeX as plain text", () => {
    const message = assistantMessage({
      role: "user",
      blocks: [{ type: "markdown", text: "**そのまま** $x$", evidence: [] }],
    });
    const { container } = render(<ChatMessage message={message} />);

    expect(screen.getByText("**そのまま** $x$")).toBeInTheDocument();
    expect(container.querySelector("strong")).toBeNull();
    expect(container.querySelector(".katex")).toBeNull();
  });
});
```

- [ ] **Step 2: Run the integration tests and verify RED**

Run:

```bash
pnpm --dir apps/web exec vitest run src/components/chat/ChatPanel.test.tsx
```

Expected: the GFM table/block-math and aside formatting tests FAIL under the old `EvidenceHighlight` renderer.

- [ ] **Step 3: Delegate assistant Markdown blocks to ChatMarkdown**

In `ChatMessage.tsx`, replace the `EvidenceHighlight` import with:

```tsx
import { ChatMarkdown } from "@/components/chat/ChatMarkdown";
```

Replace the assistant Markdown block `<p>...</p>` with:

```tsx
<div
  key={i}
  style={{
    minWidth: 0,
    fontSize: 12.6,
    lineHeight: 1.85,
    color: "var(--pr-text-body)",
  }}
>
  <ChatMarkdown
    text={block.text}
    evidence={block.evidence ?? []}
    onEvidenceJump={onEvidenceJump}
  />
</div>
```

Replace the complete `AsideBox` function with:

```tsx
function AsideBox({ label, text }: { label: "outside_knowledge" | "speculation"; text: string }) {
  return (
    <div
      style={{
        minWidth: 0,
        fontSize: 12.3,
        lineHeight: 1.8,
        color: "var(--pr-text-sub)",
        background: "var(--pr-bg-knowledge)",
        borderRadius: 6,
        padding: "8px 10px",
      }}
    >
      <div style={{ marginBottom: 5 }}>
        <AIBadge variant={label === "speculation" ? "guess" : "external"} />
      </div>
      <ChatMarkdown text={text} evidence={[]} />
    </div>
  );
}
```

- [ ] **Step 4: Delete the superseded lightweight renderer**

Delete:

```text
apps/web/src/components/chat/EvidenceHighlight.tsx
```

Run:

```bash
rg -n "EvidenceHighlight" apps/web/src
```

Expected: no matches and rg exits 1.

- [ ] **Step 5: Run chat integration tests and verify GREEN**

Run:

```bash
pnpm --dir apps/web exec vitest run src/components/chat/ChatPanel.test.tsx src/components/chat/ChatMarkdown.test.tsx
```

Expected: all chat component tests PASS, including existing actions, streaming indicator, deep links, and evidence jumps.

- [ ] **Step 6: Commit chat integration**

```bash
git add apps/web/src/components/chat/ChatMessage.tsx apps/web/src/components/chat/ChatPanel.test.tsx apps/web/src/components/chat/EvidenceHighlight.tsx
git commit -m "feat(web): use rich renderer for assistant chat blocks"
```

### Task 6: Align documentation and run final verification

**Files:**
- Modify: `docs/05-chat.md`
- Modify: `plans/09-screens/1a-viewer-parallel-chat.md`
- Modify: `plans/12-testing.md`

- [ ] **Step 1: Update the product behavior statement**

In `docs/05-chat.md` §6, replace the “本文段落” row with:

```markdown
| 本文ブロック | GFM Markdown（見出し・リスト・表・引用・リンク・コード）として安全に描画し、インライン根拠チップを展開する。数式は `$...$`（インライン）/ `$$...$$`（ブロック）を KaTeX で描画し、表・コード・ブロック数式はパネル内で横スクロールさせる |
```

Add this acceptance item in §11:

```markdown
- [ ] AI 回答と「論文外の知識」「推測」本文の GFM 表・コード・`$...$` / `$$...$$` が構造化表示され、狭いパネルでも表・コード・数式が重ならず横幅を押し広げない
```

- [ ] **Step 2: Update screen and test traceability**

In `plans/09-screens/1a-viewer-parallel-chat.md`, replace the MessageMarkdown description with:

```markdown
- 段落(MessageMarkdown。font-size:12.6px、line-height:1.85、color:#24272B): `MessageBlock{type:'markdown'}` の text を GFM Markdown（見出し・強調・リスト・表・引用・リンク・インライン/フェンスコード）として安全に描画し、`$...$` / `$$...$$` は KaTeX のインライン/ブロック数式へ変換する。表・コード・ブロック数式はサイドパネル内で横スクロールし、`[[ev:n]]` は `EvidenceChip`(size='inline')に置換する。生 HTML と画像取得は無効。
```

Replace the `VT-VIEW-10` row in `plans/12-testing.md` with:

```markdown
| VT-VIEW-10 | ChatMessage / ChatMarkdown | 「AI生成」バッジ・「論文外の知識」「推測」ボックス・`[[ev:n]]`→EvidenceChip 展開・GFM（見出し/リスト/表/コード）・`$...$` / `$$...$$` の KaTeX・横スクロール用ラッパー・生 HTML/画像/危険 URL の無効化 |
```

- [ ] **Step 3: Run focused tests**

Run:

```bash
pnpm --dir apps/web exec vitest run src/components/chat/chat-markdown-plugins.test.ts src/components/chat/ChatMarkdown.test.tsx src/components/chat/ChatPanel.test.tsx src/lib/katex-render.test.ts
```

Expected: all focused tests PASS.

- [ ] **Step 4: Run the complete Web quality gates**

Run each command separately:

```bash
pnpm --filter @alinea/web test
pnpm --filter @alinea/web typecheck
pnpm --filter @alinea/web lint
pnpm --filter @alinea/web build
```

Expected: every command exits 0. Record exact test counts and any non-failing warnings in the implementation handoff.

- [ ] **Step 5: Check formatting, stale references, and worktree scope**

Run:

```bash
git diff --check
rg -n "EvidenceHighlight" apps/web/src
git status --short
```

Expected: `git diff --check` has no output; `rg` has no output and exits 1; status contains only files listed in this plan plus the pre-existing user-owned `image-1.png` and `image-2.png` if working in the original tree.

- [ ] **Step 6: Commit documentation**

```bash
git add docs/05-chat.md plans/09-screens/1a-viewer-parallel-chat.md plans/12-testing.md
git commit -m "docs: specify rich chat response rendering"
```

- [ ] **Step 7: Review the final commit range**

Run:

```bash
git log --oneline --decorate -7
git diff HEAD~6..HEAD --stat
```

Expected: dependency setup, transforms, safe GFM, math/layout, chat integration, and documentation appear as focused commits; no user-owned image is committed.
