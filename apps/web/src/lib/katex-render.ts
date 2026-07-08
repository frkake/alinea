import katex from "katex";

const BASE_MACROS: Record<string, string> = {
  "\\bm": "\\boldsymbol{#1}",
  "\\mathbbm": "\\mathbb{#1}",
  "\\student": "\\operatorname{student}",
  "\\studentold": "\\operatorname{student}_{\\operatorname{old}}",
  "\\teacher": "\\operatorname{teacher}",
  "\\verifier": "\\operatorname{verifier}",
  "\\ps": "p_s",
  "\\nll": "\\operatorname{NLL}",
  "\\Yreach": "\\mathcal{Y}_{\\operatorname{reach}}",
  "\\Dscaf": "\\mathcal{D}_{\\operatorname{scaf}}",
};

const BLOCK_ENV_RE =
  /\\begin\{(?:aligned|alignedat|array|matrix|pmatrix|bmatrix|Bmatrix|vmatrix|Vmatrix|cases|split|gathered|smallmatrix)\}/;
const UNESCAPED_AMP_RE = /(^|[^\\])&/;
const UNDEFINED_COMMAND_RE = /Undefined control sequence: (\\[A-Za-z]+|\\.)/;

function stripRenderOnlyCommands(latex: string): string {
  return latex
    .replace(/\\(?:notag|nonumber)\b/g, "")
    .replace(/\\label\{[^{}]*}/g, "")
    .trim();
}

function shouldWrapAligned(latex: string, display: boolean): boolean {
  return display && UNESCAPED_AMP_RE.test(latex) && !BLOCK_ENV_RE.test(latex);
}

function prepareLatex(latex: string, display: boolean): string {
  const stripped = stripRenderOnlyCommands(latex);
  if (!shouldWrapAligned(stripped, display)) return stripped;
  return `\\begin{aligned}${stripped}\\end{aligned}`;
}

function replaceUndefinedCommand(latex: string, command: string): string {
  if (!/^\\[A-Za-z]+$/.test(command)) return latex;
  const name = command.slice(1);
  const pattern = new RegExp(`\\\\${name}(?![A-Za-z])`, "g");
  return latex.replace(pattern, `\\operatorname{${name}}`);
}

function escapeHtml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function renderFallback(latex: string, display: boolean): string {
  const tag = display ? "div" : "span";
  return `<${tag} class="yk-math-fallback">${escapeHtml(latex)}</${tag}>`;
}

/**
 * KaTeX による数式レンダリング(訳文モードのブロック/インライン数式)。
 *
 * - align 由来の `&` や論文固有マクロを吸収してから描画する。
 * - それでも KaTeX が解釈できない場合は、赤い parse error ではなく安全な等幅 fallback を出す。
 * - 返り値は KaTeX 生成 HTML(信頼できる自前レンダリング結果)。呼び出し側は
 *   `dangerouslySetInnerHTML` で描画する。CSS は globals.css の `katex/dist/katex.min.css`。
 */
export function renderMath(latex: string, options?: { display?: boolean }): string {
  const display = options?.display ?? false;
  let prepared = prepareLatex(latex, display);
  const macros = { ...BASE_MACROS };
  for (let attempt = 0; attempt < 8; attempt += 1) {
    try {
      return katex.renderToString(prepared, {
        displayMode: display,
        throwOnError: true,
        output: "html",
        strict: "ignore",
        macros,
      });
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      const undefinedCommand = message.match(UNDEFINED_COMMAND_RE)?.[1];
      if (undefinedCommand) {
        const recovered = replaceUndefinedCommand(prepared, undefinedCommand);
        if (recovered !== prepared) {
          prepared = recovered;
          continue;
        }
      }
      break;
    }
  }
  return renderFallback(latex, display);
}

/** ブロック数式(独立行・中央寄せ)。 */
export function renderBlockMath(latex: string): string {
  return renderMath(latex, { display: true });
}

/** インライン数式(本文中)。 */
export function renderInlineMath(latex: string): string {
  return renderMath(latex, { display: false });
}
