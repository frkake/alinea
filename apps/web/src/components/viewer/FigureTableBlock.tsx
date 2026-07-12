"use client";

import type { ReactNode } from "react";
import type { TranslationUnitItem } from "@alinea/api-client";
import { InlineRenderer } from "@/components/viewer/InlineRenderer";
import {
  TranslationInlineContent,
  hasTranslatedText,
} from "@/components/viewer/translation-content";
import { renderInlineMath } from "@/lib/katex-render";
import { cleanLatexDisplayTextOutsideMath } from "@/components/viewer/latex-display-clean";
import type { CanonicalTableCell, DocBlock, Inline } from "@/components/viewer/document-types";

interface TableCell {
  text: string;
  header: boolean;
  colSpan?: number;
  rowSpan?: number;
  translatable?: boolean;
  canonical?: boolean;
}

type TableRows = TableCell[][];

export interface FigureTableBlockProps {
  block: DocBlock;
  unit?: TranslationUnitItem | null;
  showTranslatedCaption?: boolean;
  tableTranslation?: TableTranslationAction | null;
  onCitationClick?: (refId: string) => void;
  onRefClick?: (ref: string, kind?: string | null) => void;
}

export interface TableTranslationAction {
  status: "idle" | "pending" | "succeeded" | "error";
  start: () => void;
  retry: () => void;
  error: string | null;
}

interface TableTranslationContent {
  kind: "table";
  version: 1;
  caption: Inline[] | null;
  cells: (string | null)[][] | null;
}

const MAX_TABLE_ROWS = 512;
const MAX_TABLE_CELLS = 4_096;
const MAX_TABLE_CELLS_PER_ROW = 512;
const MAX_TABLE_RAW_BYTES = 256_000;
const MAX_TABLE_CELL_SOURCE_CHARS = 8_192;
const MAX_TABLE_NESTING = 32;
const MAX_TABLE_SPAN = 512;
const MAX_TRANSLATED_CELL_CHARS = 16_384;
const MAX_TRANSLATED_TABLE_CHARS = 512_000;
const MAX_CAPTION_INLINES = 2_048;
const MAX_CAPTION_DEPTH = 16;
const MAX_CAPTION_STRING_CHARS = 32_768;
const SUPPORTED_INLINE_TYPES = new Set([
  "text",
  "math_inline",
  "citation",
  "ref",
  "footnote_ref",
  "url",
  "emphasis",
  "code_inline",
]);
const INLINE_KEYS = new Set(["t", "v", "ref", "kind", "href", "children"]);
const MATH_FRAGMENT_RE =
  /(?<!\\)(?:\$\$(?:\\.|[^$])*?\$\$|\$(?:\\.|[^$])*?\$)|\\\((?:\\.|[^\\])*?\\\)|\\\[(?:\\.|[^\\])*?\\\]/gs;

function decodeEntities(value: string): string {
  return value
    .replace(/&nbsp;/g, " ")
    .replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'");
}

function stripTags(value: string): string {
  return decodeEntities(value.replace(/<[^>]*>/g, " "))
    .replace(/\s+/g, " ")
    .trim();
}

function htmlSpan(attrs: string, name: "colspan" | "rowspan"): number | undefined {
  const match = attrs.match(
    new RegExp(`\\b${name}\\s*=\\s*(?:"([^"]+)"|'([^']+)'|([^\\s>]+))`, "i"),
  );
  const value = Number.parseInt(match?.[1] ?? match?.[2] ?? match?.[3] ?? "", 10);
  return Number.isFinite(value) && value > 1 && value <= MAX_TABLE_SPAN ? value : undefined;
}

function rawFitsLegacyBudget(raw: string): boolean {
  if (raw.length > MAX_TABLE_RAW_BYTES) return false;
  return new TextEncoder().encode(raw).byteLength <= MAX_TABLE_RAW_BYTES;
}

function htmlCellNestingIsBounded(raw: string): boolean {
  const voidTags = new Set(["br", "hr", "img", "input", "meta", "link", "source", "wbr"]);
  let insideCell = false;
  let depth = 0;
  for (const match of raw.matchAll(/<(\/)?([A-Za-z][\w:-]*)\b[^>]*>/g)) {
    const closing = match[1] === "/";
    const tag = (match[2] ?? "").toLowerCase();
    if (tag === "td" || tag === "th") {
      insideCell = !closing;
      depth = 0;
      continue;
    }
    if (!insideCell || voidTags.has(tag) || match[0].endsWith("/>")) continue;
    if (closing) depth = Math.max(0, depth - 1);
    else {
      depth += 1;
      if (depth > MAX_TABLE_NESTING) return false;
    }
  }
  return true;
}

function parseHtmlTable(raw: string): TableRows | null {
  if (!/<table[\s>]/i.test(raw) || !htmlCellNestingIsBounded(raw)) return null;
  const rows: TableRows = [];
  let totalCells = 0;
  for (const tr of raw.matchAll(/<tr\b[^>]*>([\s\S]*?)<\/tr>/gi)) {
    if (rows.length >= MAX_TABLE_ROWS) return null;
    const cells: TableCell[] = [];
    for (const cell of (tr[1] ?? "").matchAll(/<(th|td)\b([^>]*)>([\s\S]*?)<\/\1>/gi)) {
      if (cells.length >= MAX_TABLE_CELLS_PER_ROW || totalCells >= MAX_TABLE_CELLS) return null;
      const tag = cell[1] ?? "td";
      const attrs = cell[2] ?? "";
      const text = stripTags(cell[3] ?? "");
      if (text.length > MAX_TABLE_CELL_SOURCE_CHARS) return null;
      cells.push({
        header: tag.toLowerCase() === "th",
        text,
        colSpan: htmlSpan(attrs, "colspan"),
        rowSpan: htmlSpan(attrs, "rowspan"),
      });
      totalCells += 1;
    }
    if (cells.length > 0) rows.push(cells);
  }
  return rows.length > 0 ? rows : null;
}

function unwrapLatexCommands(value: string): string {
  let next = value;
  for (let i = 0; i < 4; i += 1) {
    const prev = next;
    next = next.replace(
      /\\(?:textbf|textit|emph|mathrm|mathbf|mathit|operatorname|textsuperscript|textsubscript|makecell|thead|shortstack)\{([^{}]*)\}/g,
      "$1",
    );
    if (next === prev) break;
  }
  return next;
}

function readBraced(value: string, start: number): { body: string; end: number } | null {
  if (value[start] !== "{") return null;
  let depth = 0;
  for (let i = start; i < value.length; i += 1) {
    const ch = value[i];
    if (ch === "\\" && i + 1 < value.length) {
      i += 1;
      continue;
    }
    if (ch === "{") depth += 1;
    else if (ch === "}") {
      depth -= 1;
      if (depth === 0) return { body: value.slice(start + 1, i), end: i + 1 };
    }
  }
  return null;
}

function skipOptional(value: string, start: number): number {
  let index = start;
  while (value[index] === "[") {
    const end = value.indexOf("]", index + 1);
    if (end < 0) break;
    index = end + 1;
  }
  return index;
}

function parseLatexSpanCell(rawCell: string): TableCell {
  let text = rawCell.trim();
  let colSpan: number | undefined;
  let rowSpan: number | undefined;

  if (text.startsWith("\\multicolumn")) {
    const index = "\\multicolumn".length;
    const span = readBraced(text, index);
    const align = span ? readBraced(text, span.end) : null;
    const body = align ? readBraced(text, align.end) : null;
    const parsed = Number.parseInt(span?.body ?? "", 10);
    if (Number.isFinite(parsed) && parsed > 1 && parsed <= MAX_TABLE_SPAN) colSpan = parsed;
    if (body) text = `${body.body}${text.slice(body.end)}`;
  }

  if (text.startsWith("\\multirow")) {
    const index = skipOptional(text, "\\multirow".length);
    const span = readBraced(text, index);
    const width = span ? readBraced(text, span.end) : null;
    const body = width ? readBraced(text, width.end) : null;
    const parsed = Math.abs(Number.parseInt(span?.body ?? "", 10));
    if (Number.isFinite(parsed) && parsed > 1 && parsed <= MAX_TABLE_SPAN) rowSpan = parsed;
    if (body) text = `${body.body}${text.slice(body.end)}`;
  }

  return {
    text: cleanLatexCell(text),
    header: false,
    colSpan,
    rowSpan,
  };
}

function cleanLatexCell(value: string): string {
  const structural = unwrapLatexCommands(value)
    .replace(/\\(?:multicolumn|multirow)\{[^{}]*\}\{[^{}]*\}\{([^{}]*)\}/g, "$1")
    .replace(/\\(?:hline|toprule|midrule|bottomrule|cmidrule)(?:\([^)]*\))?(?:\{[^{}]*\})?/g, "")
    .replace(/\\(?:addlinespace|smallskip|medskip|bigskip)\b/g, "")
    .replace(/\\textbackslash\b/g, "\\")
    .replace(/\\\\(?:\[[^\]]*])?/g, " ");
  return cleanLatexDisplayTextOutsideMath(structural).replace(/\s+/g, " ").trim();
}

function stripLatexTableShell(raw: string): string {
  return raw
    .replace(/%.*$/gm, "")
    .replace(/\\caption(?:\[[^\]]*])?\{[^{}]*(?:\{[^{}]*}[^{}]*)*}/g, "")
    .replace(/\\label\{[^{}]*}/g, "")
    .replace(/\\begin\{table\*?}[\s\S]*?(?=\\begin\{tabular)/g, "")
    .replace(/\\end\{table\*?}/g, "")
    .replace(/\\begin\{table\*?}(?:\[[^\]]*])?/g, "")
    .replace(/\\begin\{tcolorbox}(?:\[[^\]]*])?/g, "")
    .replace(/\\end\{tcolorbox}/g, "")
    .replace(/\\begin\{adjustbox}(?:\{[^{}]*})?/g, "")
    .replace(/\\end\{adjustbox}/g, "")
    .replace(/\\(?:centering|small|footnotesize|scriptsize|normalsize)\b/g, "")
    .replace(/\\setstretch\{[^{}]*}/g, "")
    .replace(/\\begin\{tabularx?[\w*]*}(?:\{[^{}]*}){0,3}/g, "")
    .replace(/\\end\{tabularx?[\w*]*}/g, "");
}

function splitLatexRows(value: string): string[] {
  const rows: string[] = [];
  let depth = 0;
  let math = false;
  let start = 0;
  for (let i = 0; i < value.length; i += 1) {
    const ch = value[i];
    if (ch === "$") math = !math;
    if (!math) {
      if (ch === "{") depth += 1;
      else if (ch === "}") depth = Math.max(0, depth - 1);
    }
    if (!math && depth === 0 && ch === "\\" && value[i + 1] === "\\") {
      rows.push(value.slice(start, i));
      i += 1;
      while (value[i + 1] === " " || value[i + 1] === "\n") i += 1;
      if (value[i + 1] === "[") {
        const end = value.indexOf("]", i + 2);
        if (end >= 0) i = end;
      }
      start = i + 1;
    }
  }
  rows.push(value.slice(start));
  return rows;
}

function splitLatexCells(value: string): string[] {
  const cells: string[] = [];
  let depth = 0;
  let math = false;
  let start = 0;
  for (let i = 0; i < value.length; i += 1) {
    const ch = value[i];
    if (ch === "$") math = !math;
    if (!math) {
      if (ch === "{") depth += 1;
      else if (ch === "}") depth = Math.max(0, depth - 1);
    }
    if (!math && depth === 0 && ch === "&" && value[i - 1] !== "\\") {
      cells.push(value.slice(start, i));
      start = i + 1;
    }
  }
  cells.push(value.slice(start));
  return cells;
}

function parseLatexTable(raw: string): TableRows | null {
  if (!/\\begin\{tabular|\\\\/.test(raw)) return null;
  const body = stripLatexTableShell(raw);
  const sourceRows = splitLatexRows(body)
    .map((row) => row.trim())
    .filter(Boolean);
  if (sourceRows.length > MAX_TABLE_ROWS) return null;
  const rows: TableRows = [];
  let totalCells = 0;
  for (const sourceRow of sourceRows) {
    const sourceCells = splitLatexCells(sourceRow);
    if (sourceCells.length > MAX_TABLE_CELLS_PER_ROW) return null;
    const row = sourceCells
      .map((cell) => parseLatexSpanCell(cell))
      .filter((cell) => cell.text.length > 0);
    if (row.some((cell) => cell.text.length > MAX_TABLE_CELL_SOURCE_CHARS)) return null;
    totalCells += row.length;
    if (totalCells > MAX_TABLE_CELLS) return null;
    if (row.length > 0) rows.push(row);
  }
  if (rows.length === 0) return null;
  return rows.map((row, rowIndex) => row.map((cell) => ({ ...cell, header: rowIndex === 0 })));
}

function latexNestingIsBounded(raw: string): boolean {
  let depth = 0;
  for (let index = 0; index < raw.length; index += 1) {
    if (raw[index] === "\\") {
      index += 1;
      continue;
    }
    if (raw[index] === "{") {
      depth += 1;
      if (depth > MAX_TABLE_NESTING) return false;
    } else if (raw[index] === "}") {
      depth -= 1;
      if (depth < 0) return false;
    }
  }
  return depth === 0;
}

function parseTable(raw: string | null | undefined): TableRows | null {
  if (!raw || !rawFitsLegacyBudget(raw)) return null;
  if (/<table[\s>]/i.test(raw)) return parseHtmlTable(raw);
  if (!latexNestingIsBounded(raw)) return null;
  return parseLatexTable(raw);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value != null && typeof value === "object" && !Array.isArray(value);
}

function hasControl(value: string): boolean {
  return /\p{Cc}/u.test(value);
}

function hasExactKeys(value: Record<string, unknown>, expected: readonly string[]): boolean {
  const keys = Object.keys(value).sort();
  return keys.length === expected.length && keys.every((key, index) => key === expected[index]);
}

function protectedMath(value: string): string[] {
  return Array.from(value.matchAll(MATH_FRAGMENT_RE), (match) => match[0]);
}

function mathMultisetMatches(translated: string, expected: string[]): boolean {
  const actual = protectedMath(translated);
  if (actual.length !== expected.length) return false;
  const counts = new Map<string, number>();
  for (const fragment of expected) counts.set(fragment, (counts.get(fragment) ?? 0) + 1);
  for (const fragment of actual) {
    const remaining = counts.get(fragment) ?? 0;
    if (remaining === 0) return false;
    if (remaining === 1) counts.delete(fragment);
    else counts.set(fragment, remaining - 1);
  }
  return counts.size === 0;
}

function isInline(
  value: unknown,
  depth: number,
  budget: { nodes: number; chars: number },
): value is Inline {
  if (depth > MAX_CAPTION_DEPTH || !isRecord(value)) return false;
  if (Object.keys(value).some((key) => !INLINE_KEYS.has(key))) return false;
  if (typeof value.t !== "string" || !SUPPORTED_INLINE_TYPES.has(value.t)) return false;
  budget.nodes += 1;
  if (budget.nodes > MAX_CAPTION_INLINES) return false;
  for (const key of ["v", "ref", "kind", "href"] as const) {
    const item = value[key];
    if (item !== undefined && item !== null && typeof item !== "string") return false;
    if (typeof item === "string" && (item.length > MAX_CAPTION_STRING_CHARS || hasControl(item))) {
      return false;
    }
    if (typeof item === "string") {
      budget.chars += item.length;
      if (budget.chars > MAX_TRANSLATED_TABLE_CHARS) return false;
    }
  }
  if (value.children !== undefined && value.children !== null) {
    if (
      value.t !== "emphasis" ||
      !Array.isArray(value.children) ||
      !value.children.every((child) => isInline(child, depth + 1, budget))
    ) {
      return false;
    }
  }
  return true;
}

function canonicalRows(block: DocBlock): CanonicalTableCell[][] | null {
  const grid: unknown = block.source_grid;
  if (
    !isRecord(grid) ||
    !hasExactKeys(grid, ["reason", "rows", "source_format", "supported"]) ||
    grid.supported !== true ||
    (grid.source_format !== "html" && grid.source_format !== "latex") ||
    (grid.reason !== null && typeof grid.reason !== "string") ||
    !Array.isArray(grid.rows) ||
    grid.rows.length === 0 ||
    grid.rows.length > MAX_TABLE_ROWS
  ) {
    return null;
  }
  let totalCells = 0;
  for (let rowIndex = 0; rowIndex < grid.rows.length; rowIndex += 1) {
    const row = grid.rows[rowIndex];
    if (!Array.isArray(row) || row.length === 0 || row.length > MAX_TABLE_CELLS_PER_ROW) {
      return null;
    }
    totalCells += row.length;
    if (totalCells > MAX_TABLE_CELLS) return null;
    for (let cellIndex = 0; cellIndex < row.length; cellIndex += 1) {
      const cell = row[cellIndex];
      if (
        !isRecord(cell) ||
        !hasExactKeys(cell, [
          "colspan",
          "header",
          "id",
          "latex_body_end",
          "latex_body_start",
          "latex_wrappers",
          "math",
          "rowspan",
          "source",
          "translatable",
        ]) ||
        cell.id !== `r${rowIndex}c${cellIndex}` ||
        typeof cell.source !== "string" ||
        cell.source.length > MAX_TABLE_CELL_SOURCE_CHARS ||
        hasControl(cell.source) ||
        typeof cell.header !== "boolean" ||
        typeof cell.translatable !== "boolean" ||
        !Number.isInteger(cell.rowspan) ||
        Number(cell.rowspan) < 1 ||
        Number(cell.rowspan) > MAX_TABLE_SPAN ||
        rowIndex + Number(cell.rowspan) > grid.rows.length ||
        !Number.isInteger(cell.colspan) ||
        Number(cell.colspan) < 1 ||
        Number(cell.colspan) > MAX_TABLE_SPAN ||
        !Array.isArray(cell.math) ||
        !cell.math.every((part) => typeof part === "string" && !hasControl(part)) ||
        (cell.latex_body_start !== null &&
          (!Number.isInteger(cell.latex_body_start) || Number(cell.latex_body_start) < 0)) ||
        (cell.latex_body_end !== null &&
          (!Number.isInteger(cell.latex_body_end) || Number(cell.latex_body_end) < 0)) ||
        (cell.latex_body_start === null) !== (cell.latex_body_end === null) ||
        (typeof cell.latex_body_start === "number" &&
          typeof cell.latex_body_end === "number" &&
          cell.latex_body_end < cell.latex_body_start) ||
        !Array.isArray(cell.latex_wrappers) ||
        !cell.latex_wrappers.every((wrapper) => wrapper === "multicolumn" || wrapper === "multirow")
      ) {
        return null;
      }
    }
  }
  return grid.rows as CanonicalTableCell[][];
}

function strictTableTranslation(
  unit: TranslationUnitItem | null,
  gridRows: CanonicalTableCell[][],
): TableTranslationContent | null {
  const value = unit?.content_ja;
  if (!isRecord(value)) return null;
  if (
    !hasExactKeys(value, ["caption", "cells", "kind", "version"]) ||
    value.kind !== "table" ||
    value.version !== 1
  ) {
    return null;
  }
  const caption = value.caption;
  const inlineBudget = { nodes: 0, chars: 0 };
  if (
    caption !== null &&
    (!Array.isArray(caption) ||
      caption.length > MAX_CAPTION_INLINES ||
      !caption.every((inline) => isInline(inline, 0, inlineBudget)))
  ) {
    return null;
  }
  const cells = value.cells;
  if (cells === null) {
    return { kind: "table", version: 1, caption, cells: null };
  }
  if (!Array.isArray(cells) || cells.length !== gridRows.length) return null;
  const checkedCells: (string | null)[][] = [];
  let totalChars = 0;
  for (let rowIndex = 0; rowIndex < gridRows.length; rowIndex += 1) {
    const row = cells[rowIndex];
    const sourceRow = gridRows[rowIndex];
    if (!sourceRow || !Array.isArray(row) || row.length !== sourceRow.length) return null;
    const checkedRow: (string | null)[] = [];
    for (let cellIndex = 0; cellIndex < sourceRow.length; cellIndex += 1) {
      const translated = row[cellIndex];
      if (translated !== null && typeof translated !== "string") return null;
      const sourceCell = sourceRow[cellIndex];
      if (!sourceCell) return null;
      if (sourceCell.translatable) {
        if (typeof translated !== "string" || !translated.trim()) return null;
      } else if (translated !== null) {
        return null;
      }
      if (typeof translated === "string") {
        if (translated.length > MAX_TRANSLATED_CELL_CHARS || hasControl(translated)) return null;
        if (!mathMultisetMatches(translated, sourceCell.math)) return null;
        totalChars += translated.length;
        if (totalChars > MAX_TRANSLATED_TABLE_CHARS) return null;
      }
      checkedRow.push(translated);
    }
    checkedCells.push(checkedRow);
  }
  return { kind: "table", version: 1, caption, cells: checkedCells };
}

function tableCellsComplete(
  content: TableTranslationContent | null,
  gridRows: CanonicalTableCell[][],
): boolean {
  const cells = content?.cells;
  if (!cells) return false;
  return gridRows.every((row, rowIndex) =>
    row.every((cell, cellIndex) => {
      const translated = cells[rowIndex]?.[cellIndex];
      return !cell.translatable || (typeof translated === "string" && translated.trim().length > 0);
    }),
  );
}

function rowsFromCanonical(
  gridRows: CanonicalTableCell[][],
  content: TableTranslationContent | null,
): TableRows {
  return gridRows.map((row, rowIndex) =>
    row.map((cell, cellIndex) => ({
      text: content?.cells?.[rowIndex]?.[cellIndex] ?? cell.source,
      header: cell.header,
      colSpan: cell.colspan > 1 ? cell.colspan : undefined,
      rowSpan: cell.rowspan > 1 ? cell.rowspan : undefined,
      translatable: cell.translatable,
      canonical: true,
    })),
  );
}

function looksLikeUndelimitedMath(text: string): boolean {
  const trimmed = text.trim();
  if (!trimmed) return false;
  if (/\\[A-Za-z]+|[{}]/.test(trimmed)) return true;
  return /[_^]/.test(trimmed) && /^[A-Za-z0-9_^{}/+=().,\-\s]+$/.test(trimmed);
}

function renderCellText(text: string, canonical = false): ReactNode {
  if (!canonical && protectedMath(text).length === 0 && looksLikeUndelimitedMath(text)) {
    return <span dangerouslySetInnerHTML={{ __html: renderInlineMath(text) }} />;
  }
  const parts: ReactNode[] = [];
  let index = 0;
  for (const match of text.matchAll(MATH_FRAGMENT_RE)) {
    const raw = match[0];
    const start = match.index ?? 0;
    if (start > index) parts.push(text.slice(index, start));
    const latex = raw.startsWith("$$")
      ? raw.slice(2, -2)
      : raw.startsWith("$")
        ? raw.slice(1, -1)
        : raw.slice(2, -2);
    parts.push(
      <span
        key={`${start}-${raw.length}`}
        dangerouslySetInnerHTML={{ __html: renderInlineMath(latex) }}
      />,
    );
    index = start + raw.length;
  }
  if (index < text.length) parts.push(text.slice(index));
  return parts.length > 0 ? parts : text;
}

function TableView({ rows }: { rows: TableRows }) {
  return (
    <div
      style={{
        overflowX: "auto",
        width: "100%",
        maxWidth: "100%",
        minWidth: 0,
        boxSizing: "border-box",
        margin: "10px 0 8px",
      }}
    >
      <table
        style={{
          minWidth: "100%",
          width: "max-content",
          borderCollapse: "collapse",
          fontFamily: "var(--pr-font-ui)",
          fontSize: 12.5,
          color: "var(--pr-text-body)",
        }}
      >
        <tbody>
          {rows.map((row, rowIndex) => (
            <tr key={rowIndex}>
              {row.map((cell, cellIndex) => {
                const Cell = cell.header ? "th" : "td";
                return (
                  <Cell
                    key={`${rowIndex}-${cellIndex}`}
                    colSpan={cell.colSpan}
                    rowSpan={cell.rowSpan}
                    style={{
                      border: "1px solid var(--pr-border-card)",
                      padding: "6px 8px",
                      textAlign: "left",
                      verticalAlign: "top",
                      maxWidth: 520,
                      overflowWrap: "anywhere",
                      wordBreak: "break-word",
                      background: cell.header ? "var(--pr-bg-muted)" : "transparent",
                      fontWeight: cell.header ? 600 : 400,
                    }}
                  >
                    {renderCellText(cell.text, cell.canonical === true)}
                  </Cell>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function mediaLabel(block: DocBlock): string {
  if (block.number) return block.type === "figure" ? `図${block.number}` : `表${block.number}`;
  return block.label ?? (block.type === "figure" ? "図" : "表");
}

export function FigureTableBlock({
  block,
  unit = null,
  showTranslatedCaption = true,
  tableTranslation = null,
  onCitationClick,
  onRefClick,
}: FigureTableBlockProps) {
  const gridRows = block.type === "table" ? canonicalRows(block) : null;
  const typedContent = gridRows ? strictTableTranslation(unit, gridRows) : null;
  const rows = gridRows ? rowsFromCanonical(gridRows, typedContent) : parseTable(block.raw);
  const hasCaption = (block.caption ?? []).length > 0;
  const legacyCaption = Array.isArray(unit?.content_ja) && hasTranslatedText(unit);
  const typedCaption = typedContent?.caption ?? null;
  const hasTranslation =
    showTranslatedCaption &&
    ((typedCaption != null && typedCaption.length > 0) || (typedContent == null && legacyCaption));
  const hasTargets = gridRows?.some((row) => row.some((cell) => cell.translatable)) ?? false;
  const needsCellTranslation =
    block.type === "table" &&
    gridRows != null &&
    hasTargets &&
    !tableCellsComplete(typedContent, gridRows);
  const captionLabel = mediaLabel(block);

  return (
    <figure
      data-block-id={block.id}
      data-block-type={block.type}
      style={{
        margin: "20px 0",
        padding: "12px 14px",
        maxWidth: "100%",
        minWidth: 0,
        boxSizing: "border-box",
        border: "1px solid var(--pr-border-card)",
        borderRadius: 8,
        fontFamily: "var(--pr-font-ui)",
        color: "var(--pr-text-mid)",
      }}
    >
      {block.asset_url && (!rows || block.type === "figure") ? (
        <img
          src={block.asset_url}
          alt={mediaLabel(block)}
          loading="lazy"
          style={{
            display: "block",
            maxWidth: "100%",
            maxHeight: block.type === "figure" ? 540 : 420,
            objectFit: "contain",
            margin: "0 auto 10px",
          }}
        />
      ) : null}
      {rows ? <TableView rows={rows} /> : null}
      <figcaption style={{ fontSize: 12.5, lineHeight: 1.75, overflowWrap: "anywhere" }}>
        {hasTranslation ? (
          <div style={{ color: "var(--pr-text-body)", fontFamily: "var(--pr-jp)" }}>
            <span style={{ fontWeight: 700 }}>{captionLabel}</span>{" "}
            {typedCaption ? (
              <InlineRenderer
                inlines={typedCaption}
                onCitationClick={onCitationClick}
                onRefClick={onRefClick}
              />
            ) : (
              <TranslationInlineContent
                unit={unit}
                onCitationClick={onCitationClick}
                onRefClick={onRefClick}
              />
            )}
          </div>
        ) : (
          <div>
            <span style={{ fontWeight: 700, color: "var(--pr-text-body)" }}>{captionLabel}</span>
            {hasCaption ? (
              <span>
                {" "}
                <InlineRenderer
                  inlines={block.caption ?? []}
                  onCitationClick={onCitationClick}
                  onRefClick={onRefClick}
                />
              </span>
            ) : null}
          </div>
        )}
        {hasTranslation && hasCaption ? (
          <div
            style={{ marginTop: 4, color: "var(--pr-text-muted)", fontFamily: "var(--pr-font-en)" }}
          >
            Original:{" "}
            <InlineRenderer
              inlines={block.caption ?? []}
              onCitationClick={onCitationClick}
              onRefClick={onRefClick}
            />
          </div>
        ) : null}
      </figcaption>
      {needsCellTranslation && tableTranslation ? (
        <div
          role="status"
          style={{
            marginTop: 10,
            display: "flex",
            alignItems: "center",
            flexWrap: "wrap",
            gap: 8,
            fontSize: 12,
            color: tableTranslation.status === "error" ? "var(--pr-warn)" : "var(--pr-text-muted)",
          }}
        >
          {tableTranslation.status === "error" ? (
            <>
              <span>{tableTranslation.error ?? "表の翻訳に失敗しました"}</span>
              <button type="button" onClick={tableTranslation.retry} style={actionButtonStyle}>
                再試行
              </button>
            </>
          ) : tableTranslation.status === "succeeded" ? (
            <span>表を翻訳しました</span>
          ) : (
            <button
              type="button"
              onClick={tableTranslation.start}
              disabled={tableTranslation.status === "pending"}
              style={{
                ...actionButtonStyle,
                opacity: tableTranslation.status === "pending" ? 0.65 : 1,
              }}
            >
              {tableTranslation.status === "pending" ? "この表を翻訳中…" : "この表を翻訳"}
            </button>
          )}
        </div>
      ) : null}
    </figure>
  );
}

const actionButtonStyle = {
  border: "1px solid var(--pr-border-card)",
  borderRadius: 6,
  padding: "5px 9px",
  background: "var(--pr-bg-muted)",
  color: "var(--pr-acc)",
  cursor: "pointer",
  fontFamily: "var(--pr-font-ui)",
  fontSize: 12,
} as const;
