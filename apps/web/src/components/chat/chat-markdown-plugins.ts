import type { Link, Root, Text } from "mdast";
import type { Plugin } from "unified";
import { visit } from "unist-util-visit";

export const EVIDENCE_PROPERTY = "data-alinea-evidence-ref";

const EVIDENCE_MARKER_RE = /\[\[ev:(\d+)\]\]/g;

function isEscaped(value: string, index: number): boolean {
  let slashCount = 0;
  for (let cursor = index - 1; cursor >= 0 && value[cursor] === "\\"; cursor -= 1) slashCount += 1;
  return slashCount % 2 === 1;
}

function isDollarPair(value: string, index: number): boolean {
  return value[index] === "$" && value[index + 1] === "$" && !isEscaped(value, index);
}

function lineEnd(value: string, start: number): number {
  const end = value.indexOf("\n", start);
  return end === -1 ? value.length : end;
}

function isLineStart(value: string, index: number): boolean {
  return index === 0 || value[index - 1] === "\n";
}

function containerPrefixLength(line: string): number {
  let cursor = 0;
  while (cursor < line.length) {
    const blockquote = /^( {0,3}>[ \t]?)/.exec(line.slice(cursor));
    if (blockquote !== null) {
      cursor += blockquote[0].length;
      continue;
    }

    const listIndent = /^( {4,})/.exec(line.slice(cursor));
    if (listIndent !== null) {
      cursor += listIndent[0].length;
      continue;
    }

    break;
  }
  return cursor;
}

function codeFenceEnd(value: string, start: number): number | undefined {
  if (!isLineStart(value, start)) return undefined;

  const openingEnd = lineEnd(value, start);
  const rawOpeningLine = value.slice(start, openingEnd);
  const openingLine = rawOpeningLine.endsWith("\r") ? rawOpeningLine.slice(0, -1) : rawOpeningLine;
  const prefix = openingLine.slice(0, containerPrefixLength(openingLine));
  const opening = /^( {0,3})(`{3,}|~{3,})(.*)$/.exec(openingLine.slice(prefix.length));
  if (opening === null) return undefined;

  const fence = opening[2];
  if (fence === undefined) return undefined;
  const fenceCharacter = fence.charAt(0);
  const info = opening[3] ?? "";
  if (fenceCharacter === "`" && info.includes("`")) return undefined;

  const closing = new RegExp(`^ {0,3}${fenceCharacter}{${fence.length},}[ \\t]*$`);
  let cursor = openingEnd === value.length ? value.length : openingEnd + 1;
  while (cursor < value.length) {
    const candidateEnd = lineEnd(value, cursor);
    const rawCandidate = value.slice(cursor, candidateEnd);
    const candidate = rawCandidate.endsWith("\r") ? rawCandidate.slice(0, -1) : rawCandidate;
    if (candidate.startsWith(prefix) && closing.test(candidate.slice(prefix.length)))
      return candidateEnd === value.length ? candidateEnd : candidateEnd + 1;
    cursor = candidateEnd === value.length ? value.length : candidateEnd + 1;
  }

  return value.length;
}

function backtickRunLength(value: string, start: number): number {
  let end = start;
  while (value[end] === "`") end += 1;
  return end - start;
}

function inlineCodeEnd(value: string, start: number): number | undefined {
  const delimiterLength = backtickRunLength(value, start);
  let cursor = start + delimiterLength;
  while (cursor < value.length) {
    if (value[cursor] !== "`") {
      cursor += 1;
      continue;
    }

    const candidateLength = backtickRunLength(value, cursor);
    if (candidateLength === delimiterLength) return cursor + delimiterLength;
    cursor += candidateLength;
  }
  return undefined;
}

function isOwnLineDelimiter(value: string, index: number): boolean {
  const start = value.lastIndexOf("\n", index) + 1;
  const nextLine = value.indexOf("\n", index + 2);
  const end = nextLine === -1 ? value.length : nextLine;
  return /^[ \t\r]*$/.test(value.slice(start, index)) && /^[ \t\r]*$/.test(value.slice(index + 2, end));
}

function displayMathClosingDelimiter(value: string, opening: number): number | undefined {
  let cursor = opening + 2;
  while (cursor < value.length) {
    const fencedEnd = codeFenceEnd(value, cursor);
    if (fencedEnd !== undefined) {
      cursor = fencedEnd;
      continue;
    }

    if (value[cursor] === "`") {
      const codeEnd = inlineCodeEnd(value, cursor);
      if (codeEnd !== undefined) {
        cursor = codeEnd;
        continue;
      }
    }

    if (isDollarPair(value, cursor)) return cursor;
    cursor += 1;
  }
  return undefined;
}

/** Converts inline `$$…$$` pairs into flow display-math blocks without touching code. */
export function normalizeDisplayMath(markdown: string): string {
  let normalized = "";
  let cursor = 0;
  let sourceCursor = 0;

  while (cursor < markdown.length) {
    const fencedEnd = codeFenceEnd(markdown, cursor);
    if (fencedEnd !== undefined) {
      cursor = fencedEnd;
      continue;
    }

    if (markdown[cursor] === "`") {
      const codeEnd = inlineCodeEnd(markdown, cursor);
      if (codeEnd !== undefined) {
        cursor = codeEnd;
        continue;
      }
    }

    if (!isDollarPair(markdown, cursor)) {
      cursor += 1;
      continue;
    }

    const closing = displayMathClosingDelimiter(markdown, cursor);
    if (closing === undefined) return normalized + markdown.slice(sourceCursor);

    if (isOwnLineDelimiter(markdown, cursor) && isOwnLineDelimiter(markdown, closing)) {
      cursor = closing + 2;
      continue;
    }

    normalized += `${markdown.slice(sourceCursor, cursor)}\n\n$$\n${markdown.slice(cursor + 2, closing).trim()}\n$$\n\n`;
    sourceCursor = closing + 2;
    cursor = sourceCursor;
  }

  return normalized + markdown.slice(sourceCursor);
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
  visit(tree, "text", (node, index, parent) => {
    if (index === undefined || parent === undefined) return;

    const replacement: Array<Text | Link> = [];
    let cursor = 0;
    for (const match of node.value.matchAll(EVIDENCE_MARKER_RE)) {
      const start = match.index ?? 0;
      if (start > cursor) replacement.push({ type: "text", value: node.value.slice(cursor, start) });
      replacement.push(evidenceLink(Number(match[1])));
      cursor = start + match[0].length;
    }

    if (replacement.length === 0) return;
    if (cursor < node.value.length) replacement.push({ type: "text", value: node.value.slice(cursor) });
    parent.children.splice(index, 1, ...replacement);
  });
}

export const remarkEvidence: Plugin<[], Root> = () => (tree) => {
  replaceEvidenceMarkers(tree);
};
