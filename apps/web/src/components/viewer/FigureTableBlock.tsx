"use client";

import type { ReactNode } from "react";
import type { TranslationUnitItem } from "@alinea/api-client";
import { InlineRenderer } from "@/components/viewer/InlineRenderer";
import { TranslationInlineContent, hasTranslatedText } from "@/components/viewer/translation-content";
import { renderInlineMath } from "@/lib/katex-render";
import { cleanLatexDisplayTextOutsideMath } from "@/components/viewer/latex-display-clean";
import type { DocBlock } from "@/components/viewer/document-types";

interface TableCell {
  text: string;
  header: boolean;
  colSpan?: number;
  rowSpan?: number;
}

type TableRows = TableCell[][];

export interface FigureTableBlockProps {
  block: DocBlock;
  unit?: TranslationUnitItem | null;
  showTranslatedCaption?: boolean;
  onCitationClick?: (refId: string) => void;
  onRefClick?: (ref: string, kind?: string | null) => void;
}

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
  return decodeEntities(value.replace(/<[^>]*>/g, " ")).replace(/\s+/g, " ").trim();
}

function htmlSpan(attrs: string, name: "colspan" | "rowspan"): number | undefined {
  const match = attrs.match(new RegExp(`\\b${name}\\s*=\\s*(?:"([^"]+)"|'([^']+)'|([^\\s>]+))`, "i"));
  const value = Number.parseInt(match?.[1] ?? match?.[2] ?? match?.[3] ?? "", 10);
  return Number.isFinite(value) && value > 1 ? value : undefined;
}

function parseHtmlTable(raw: string): TableRows | null {
  if (!/<table[\s>]/i.test(raw)) return null;
  const rows: TableRows = [];
  for (const tr of raw.matchAll(/<tr\b[^>]*>([\s\S]*?)<\/tr>/gi)) {
    const cells: TableCell[] = [];
    for (const cell of (tr[1] ?? "").matchAll(/<(th|td)\b([^>]*)>([\s\S]*?)<\/\1>/gi)) {
      const tag = cell[1] ?? "td";
      const attrs = cell[2] ?? "";
      cells.push({
        header: tag.toLowerCase() === "th",
        text: stripTags(cell[3] ?? ""),
        colSpan: htmlSpan(attrs, "colspan"),
        rowSpan: htmlSpan(attrs, "rowspan"),
      });
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
    if (Number.isFinite(parsed) && parsed > 1) colSpan = parsed;
    if (body) text = `${body.body}${text.slice(body.end)}`;
  }

  if (text.startsWith("\\multirow")) {
    const index = skipOptional(text, "\\multirow".length);
    const span = readBraced(text, index);
    const width = span ? readBraced(text, span.end) : null;
    const body = width ? readBraced(text, width.end) : null;
    const parsed = Math.abs(Number.parseInt(span?.body ?? "", 10));
    if (Number.isFinite(parsed) && parsed > 1) rowSpan = parsed;
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
  return cleanLatexDisplayTextOutsideMath(structural)
    .replace(/\s+/g, " ")
    .trim();
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
  const rows = splitLatexRows(body)
    .map((row) => row.trim())
    .filter(Boolean)
    .map((row) =>
      splitLatexCells(row)
        .map((cell) => parseLatexSpanCell(cell))
        .filter((cell) => cell.text.length > 0),
    )
    .filter((row) => row.length > 0);
  if (rows.length === 0) return null;
  return rows.map((row, rowIndex) => row.map((cell) => ({ ...cell, header: rowIndex === 0 })));
}

function parseTable(raw: string | null | undefined): TableRows | null {
  if (!raw) return null;
  return parseHtmlTable(raw) ?? parseLatexTable(raw);
}

function looksLikeUndelimitedMath(text: string): boolean {
  const trimmed = text.trim();
  if (!trimmed) return false;
  if (/\\[A-Za-z]+|[{}]/.test(trimmed)) return true;
  return /[_^]/.test(trimmed) && /^[A-Za-z0-9_^{}/+=().,\-\s]+$/.test(trimmed);
}

function renderCellText(text: string): ReactNode {
  if (looksLikeUndelimitedMath(text)) {
    return <span dangerouslySetInnerHTML={{ __html: renderInlineMath(text) }} />;
  }
  const parts: ReactNode[] = [];
  let index = 0;
  const math = /(\$[^$]+\$|\\\([^]*?\\\))/g;
  for (const match of text.matchAll(math)) {
    const raw = match[0];
    const start = match.index ?? 0;
    if (start > index) parts.push(text.slice(index, start));
    const latex = raw.startsWith("$") ? raw.slice(1, -1) : raw.slice(2, -2);
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
    <div style={{ overflowX: "auto", margin: "10px 0 8px" }}>
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
                      background: cell.header ? "var(--pr-bg-muted)" : "transparent",
                      fontWeight: cell.header ? 600 : 400,
                    }}
                  >
                    {renderCellText(cell.text)}
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
  onCitationClick,
  onRefClick,
}: FigureTableBlockProps) {
  const rows = parseTable(block.raw);
  const hasCaption = (block.caption ?? []).length > 0;
  const hasTranslation = showTranslatedCaption && hasTranslatedText(unit);
  const captionLabel = mediaLabel(block);

  return (
    <figure
      data-block-id={block.id}
      data-block-type={block.type}
      style={{
        margin: "20px 0",
        padding: "12px 14px",
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
            <TranslationInlineContent
              unit={unit}
              onCitationClick={onCitationClick}
              onRefClick={onRefClick}
            />
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
          <div style={{ marginTop: 4, color: "var(--pr-text-muted)", fontFamily: "var(--pr-font-en)" }}>
            Original:{" "}
            <InlineRenderer
              inlines={block.caption ?? []}
              onCitationClick={onCitationClick}
              onRefClick={onRefClick}
            />
          </div>
        ) : null}
      </figcaption>
    </figure>
  );
}
