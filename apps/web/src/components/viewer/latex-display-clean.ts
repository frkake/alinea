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
    .replace(/\\emptyset\b/g, "∅")
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
    .replace(/\\theta\b/g, "θ")
    .replace(/\\dots\b|\\ldots\b/g, "…");
}

function unwrapKnownCommands(value: string): string {
  let next = value;
  for (let i = 0; i < 6; i += 1) {
    const prev = next;
    next = next
      .replace(/\\mybox(?:\[[^\]]*])?\{[^{}]*\}\{([^{}]*)\}/g, "$1")
      .replace(
        /\\(?:textbf|textit|emph|mathrm|mathbf|mathit|operatorname|textsuperscript|textsubscript|underline|textsc|text)\{([^{}]*)\}/g,
        "$1",
      );
    if (next === prev) break;
  }
  return next;
}

function cleanLatexSegment(value: string): string {
  return replaceLatexSymbols(unwrapKnownCommands(value))
    .replace(/LABEL:section:\\?[A-Za-z]*(?:\{\})?[A-Za-z0-9:_-]+/g, "the referenced section")
    .replace(/LABEL:figure:\\?[A-Za-z]*(?:\{\})?[A-Za-z0-9:_-]+/g, "the referenced figure")
    .replace(/LABEL:table:\\?[A-Za-z]*(?:\{\})?[A-Za-z0-9:_-]+/g, "the referenced table")
    .replace(/LABEL:(?:eq|equation):\\?[A-Za-z]*(?:\{\})?[A-Za-z0-9:_-]+/g, "the referenced equation")
    .replace(/\\(?:begin|end)\{(?:itemize|enumerate|description|center|flushleft|flushright)\}(?:\[[^\]]*])?/g, " ")
    .replace(/\\item(?:\[([^\]]+)])?/g, (_match, label: string | undefined) =>
      label ? ` ${label} ` : " • ",
    )
    .replace(/\\(?:citep?|citet|citealp|citeauthor|citeyear)\{[^{}]*\}/g, "")
    .replace(/\\(?:vspace|hspace|addlinespace|smallskip|medskip|bigskip)\*?(?:\[[^\]]*])?\{[^{}]*\}/g, " ")
    .replace(/\\(?:hrule|toprule|midrule|bottomrule|hline)\b/g, " ")
    .replace(/\\(?:!|,|;|:)/g, "")
    .replace(/\\([%&_#$])/g, "$1")
    .replace(/\\[A-Za-z]+\*?(?:\[[^\]]*])?(?:\{[^{}]*\})*/g, "")
    .replace(/[_^]\{[^{}]*\}/g, "")
    .replace(/[{}]/g, "")
    .replace(/~/g, " ")
    .replace(/\s+([,.;:)])/g, "$1")
    .replace(/([([])\s+/g, "$1")
    .replace(/\s+/g, " ")
    .trim();
}

export function cleanLatexDisplayText(value: string): string {
  if (!/[\\{}]|LABEL:/.test(value)) return value;
  return cleanLatexSegment(value);
}

export function cleanLatexDisplayTextOutsideMath(value: string): string {
  if (!/[\\{}]|LABEL:/.test(value)) return value;
  const parts: string[] = [];
  const math = /(\$[^$]+\$|\\\([\s\S]*?\\\))/g;
  let index = 0;
  for (const match of value.matchAll(math)) {
    const start = match.index ?? 0;
    if (start > index) parts.push(cleanLatexSegment(value.slice(index, start)));
    parts.push(match[0]);
    index = start + match[0].length;
  }
  if (index < value.length) parts.push(cleanLatexSegment(value.slice(index)));
  return parts.join(" ").replace(/\s+/g, " ").trim();
}
