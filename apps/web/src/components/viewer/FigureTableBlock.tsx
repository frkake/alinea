"use client";

import type { ReactNode } from "react";
import type { TranslationUnitItem } from "@alinea/api-client";
import { InlineRenderer } from "@/components/viewer/InlineRenderer";
import { TranslationInlineContent, hasTranslatedText } from "@/components/viewer/translation-content";
import { renderInlineMath } from "@/lib/katex-render";
import type { DocBlock } from "@/components/viewer/document-types";

interface TableCell {
  text: string;
  header: boolean;
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

function parseHtmlTable(raw: string): TableRows | null {
  if (!/<table[\s>]/i.test(raw)) return null;
  const rows: TableRows = [];
  for (const tr of raw.matchAll(/<tr\b[^>]*>([\s\S]*?)<\/tr>/gi)) {
    const cells: TableCell[] = [];
    for (const cell of (tr[1] ?? "").matchAll(/<(th|td)\b[^>]*>([\s\S]*?)<\/\1>/gi)) {
      const tag = cell[1] ?? "td";
      cells.push({ header: tag.toLowerCase() === "th", text: stripTags(cell[2] ?? "") });
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
      /\\(?:textbf|textit|emph|mathrm|mathbf|mathit|operatorname|textsuperscript|textsubscript)\{([^{}]*)\}/g,
      "$1",
    );
    if (next === prev) break;
  }
  return next;
}

function replaceLatexSymbols(value: string): string {
  return value
    .replace(/\\pm\b/g, "±")
    .replace(/\\mp\b/g, "∓")
    .replace(/\\times\b/g, "×")
    .replace(/\\cdot\b/g, "·")
    .replace(/\\leq?\b/g, "≤")
    .replace(/\\geq?\b/g, "≥")
    .replace(/\\neq\b/g, "≠")
    .replace(/\\approx\b/g, "≈")
    .replace(/\\infty\b/g, "∞")
    .replace(/\\uparrow\b/g, "↑")
    .replace(/\\downarrow\b/g, "↓")
    .replace(/\\rightarrow\b|\\to\b/g, "→")
    .replace(/\\leftarrow\b/g, "←")
    .replace(/\\alpha\b/g, "α")
    .replace(/\\beta\b/g, "β")
    .replace(/\\gamma\b/g, "γ")
    .replace(/\\delta\b/g, "δ")
    .replace(/\\lambda\b/g, "λ")
    .replace(/\\mu\b/g, "μ")
    .replace(/\\pi\b/g, "π")
    .replace(/\\sigma\b/g, "σ")
    .replace(/\\theta\b/g, "θ");
}

function cleanLatexCell(value: string): string {
  return replaceLatexSymbols(unwrapLatexCommands(value))
    .replace(/\\(?:multicolumn|multirow)\{[^{}]*\}\{[^{}]*\}\{([^{}]*)\}/g, "$1")
    .replace(/\\(?:hline|toprule|midrule|bottomrule|cmidrule)(?:\([^)]*\))?(?:\{[^{}]*\})?/g, "")
    .replace(/\\(?:addlinespace|smallskip|medskip|bigskip)\b/g, "")
    .replace(/\\[%&_#$]/g, (m) => m.slice(1))
    .replace(/\\textbackslash\b/g, "\\")
    .replace(/\\,/g, " ")
    .replace(/~/g, " ")
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
    .replace(/\\begin\{tabularx?[\w*]*}(?:\{[^{}]*}){0,3}/g, "")
    .replace(/\\end\{tabularx?[\w*]*}/g, "");
}

function parseLatexTable(raw: string): TableRows | null {
  if (!/\\begin\{tabular|\\\\/.test(raw)) return null;
  const body = stripLatexTableShell(raw);
  const rows = body
    .split(/\\\\(?:\[[^\]]*])?/)
    .map((row) => row.trim())
    .filter(Boolean)
    .map((row) =>
      row
        .split(/(?<!\\)&/)
        .map((cell) => cleanLatexCell(cell))
        .filter((cell) => cell.length > 0),
    )
    .filter((row) => row.length > 0);
  if (rows.length === 0) return null;
  return rows.map((row, rowIndex) => row.map((text) => ({ text, header: rowIndex === 0 })));
}

function parseTable(raw: string | null | undefined): TableRows | null {
  if (!raw) return null;
  return parseHtmlTable(raw) ?? parseLatexTable(raw);
}

function renderableFigureHtml(raw: string | null | undefined): string | null {
  if (!raw || !/<svg[\s>]/i.test(raw)) return null;
  return raw;
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
          width: "100%",
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
  const rows = block.type === "table" ? parseTable(block.raw) : null;
  const figureHtml = block.type === "figure" && !block.asset_url ? renderableFigureHtml(block.raw) : null;
  const hasCaption = (block.caption ?? []).length > 0;
  const hasTranslation = showTranslatedCaption && hasTranslatedText(unit);
  const captionLabel = mediaLabel(block);

  return (
    <figure
      data-block-id={block.id}
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
      {figureHtml ? (
        <div
          aria-label={mediaLabel(block)}
          role="img"
          style={{
            display: "block",
            maxWidth: "100%",
            maxHeight: 540,
            overflow: "auto",
            margin: "0 auto 10px",
            textAlign: "center",
          }}
          dangerouslySetInnerHTML={{ __html: figureHtml }}
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
