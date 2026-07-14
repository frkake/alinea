import type { Link, Paragraph, Root, Text } from "mdast";
import type { Plugin } from "unified";
import { visit } from "unist-util-visit";

export const EVIDENCE_PROPERTY = "data-alinea-evidence-ref";

const EVIDENCE_MARKER_RE = /\[\[ev:(\d+)\]\]/g;

interface InlineMathNode {
  type: "inlineMath";
  value: string;
  position?: {
    start?: { offset?: number };
    end?: { offset?: number };
  };
}

interface DisplayMathNode {
  type: "math";
  value: string;
  meta: null;
  data: {
    hName: "pre";
    hChildren: Array<{
      type: "element";
      tagName: "code";
      properties: { className: string[] };
      children: Array<{ type: "text"; value: string }>;
    }>;
  };
}

function isInlineMath(
  node: Paragraph["children"][number],
): node is Paragraph["children"][number] & InlineMathNode {
  return (node as { type?: unknown }).type === "inlineMath";
}

function isDoubleDollarInlineMath(node: InlineMathNode, markdown: string): boolean {
  const start = node.position?.start?.offset;
  const end = node.position?.end?.offset;
  return (
    start !== undefined &&
    end !== undefined &&
    markdown.slice(start, start + 2) === "$$" &&
    markdown.slice(end - 2, end) === "$$"
  );
}

function splitParagraphOnDisplayMath(
  paragraph: Paragraph,
  markdown: string,
): Array<Paragraph | DisplayMathNode> | undefined {
  const blocks: Array<Paragraph | DisplayMathNode> = [];
  let pending: Paragraph["children"] = [];
  let foundDisplayMath = false;

  for (const child of paragraph.children) {
    if (isInlineMath(child) && isDoubleDollarInlineMath(child, markdown)) {
      foundDisplayMath = true;
      if (pending.length > 0) blocks.push({ type: "paragraph", children: pending });
      blocks.push({
        type: "math",
        value: child.value,
        meta: null,
        data: {
          hName: "pre",
          hChildren: [
            {
              type: "element",
              tagName: "code",
              properties: { className: ["language-math", "math-display"] },
              children: [{ type: "text", value: child.value }],
            },
          ],
        },
      });
      pending = [];
      continue;
    }
    pending.push(child);
  }

  if (!foundDisplayMath) return undefined;
  if (pending.length > 0) blocks.push({ type: "paragraph", children: pending });
  return blocks;
}

/** Promotes same-line `$$…$$` expressions to flow math while preserving Markdown containers. */
export function remarkDisplayMath(markdown: string): Plugin<[], Root> {
  return () => (tree) => {
    visit(tree, "paragraph", (paragraph, index, parent) => {
      if (index === undefined || parent === undefined) return;

      const blocks = splitParagraphOnDisplayMath(paragraph, markdown);
      if (blocks === undefined) return;
      parent.children.splice(index, 1, ...blocks);
    });
  };
}

function evidenceLink(reference: number): Link {
  return {
    type: "link",
    url: "#",
    children: [{ type: "text", value: "" }],
    data: { hProperties: { [EVIDENCE_PROPERTY]: reference } },
  };
}

/** Replaces verified `[[ev:N]]` markers in MDAST text nodes with evidence links. */
export function replaceEvidenceMarkers(tree: Root): void {
  const protectedTextNodes = new Set<Text>();
  visit(tree, ["link", "linkReference"], (node) => {
    visit(node, "text", (text) => {
      protectedTextNodes.add(text);
    });
  });

  visit(tree, "text", (node, index, parent) => {
    if (index === undefined || parent === undefined) return;
    if (protectedTextNodes.has(node)) return;

    const replacement: Array<Text | Link> = [];
    let cursor = 0;
    for (const match of node.value.matchAll(EVIDENCE_MARKER_RE)) {
      const start = match.index ?? 0;
      if (start > cursor)
        replacement.push({ type: "text", value: node.value.slice(cursor, start) });
      replacement.push(evidenceLink(Number(match[1])));
      cursor = start + match[0].length;
    }

    if (replacement.length === 0) return;
    if (cursor < node.value.length)
      replacement.push({ type: "text", value: node.value.slice(cursor) });
    parent.children.splice(index, 1, ...replacement);
  });
}

export const remarkEvidence: Plugin<[], Root> = () => (tree) => {
  replaceEvidenceMarkers(tree);
};
