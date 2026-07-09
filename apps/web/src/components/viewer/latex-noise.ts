import type { DocBlock, Inline } from "@/components/viewer/document-types";

function inlineText(inlines: Inline[] | undefined): string {
  const parts: string[] = [];
  for (const inline of inlines ?? []) {
    if (inline.t === "text" || inline.t === "code_inline" || inline.t === "math_inline") {
      parts.push(inline.v ?? "");
    } else if (inline.t === "emphasis") {
      parts.push(inline.v ?? inlineText(inline.children));
    } else if (inline.t === "url") {
      parts.push(inline.v ?? inline.href ?? "");
    }
  }
  return parts.join(" ");
}

const NOISE_PATTERNS = [
  /\biation\s*\{[^}]*\}/i,
  /\b(?:ForestGreen|RoyalBlue|DarkPink|Gray)\s*\{(?:RGB|gray)\}\s*\{/,
  /\b(?:RGB|gray)\s*\{[0-9.,\s]+\}/,
  /\{\{\s*[A-Za-z][A-Za-z0-9]*\s*\}\}/,
  /^\{[A-Z][A-Za-z]+ [A-Z][A-Za-z]+,\s*et al\.\}$/,
  /\[[0-9]+\]\s*\{[^}]*#[0-9]/,
  /#[0-9]/,
  /▶[^◀]*◀/,
  /\b(?:MICHELE|ANISHA|ROSHANAK)\s*\{/,
  /すなわち\s*i\.e\.,.*例えば\s*e\.g\.,.*et al\./,
];

export function isLatexSetupNoiseBlock(block: DocBlock): boolean {
  if (block.type !== "paragraph") return false;
  const text = inlineText(block.inlines).replace(/\s+/g, " ").trim();
  if (!text || text.length > 240) return false;
  return NOISE_PATTERNS.some((pattern) => pattern.test(text));
}
