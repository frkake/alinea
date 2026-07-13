import katex from "katex";

const BASE_MACROS: Record<string, string> = {
  "\\bm": "\\boldsymbol{#1}",
  "\\mathbbm": "\\mathbb{#1}",
  "\\student": "\\operatorname{student}",
};

const UNDEFINED_COMMAND_RE = /Undefined control sequence: (\\[A-Za-z]+|\\.)/;
const ENVIRONMENT_BOUNDARY_RE = /^\\(begin|end)\{([A-Za-z]+\*?)\}/;

function stripRenderOnlyCommands(latex: string): string {
  return latex
    .replace(/\\(?:notag|nonumber)\b/g, "")
    .replace(/\\label\{[^{}]*}/g, "")
    .trim();
}

function hasTopLevelAlignmentTab(latex: string): boolean {
  const environments: string[] = [];
  let braceDepth = 0;
  let index = 0;
  while (index < latex.length) {
    const char = latex[index];
    if (char === "\\") {
      const boundary = latex.slice(index).match(ENVIRONMENT_BOUNDARY_RE);
      if (boundary) {
        const [, kind, name] = boundary;
        if (kind === "begin") environments.push(name ?? "");
        else if (environments.at(-1) === name) environments.pop();
        index += boundary[0].length;
        continue;
      }
      if (latex[index + 1] === "\\") {
        index += 2;
        continue;
      }
      index += 1;
      while (index < latex.length && /[A-Za-z@]/.test(latex[index] ?? "")) index += 1;
      if (index < latex.length && !/[A-Za-z@]/.test(latex[index - 1] ?? "")) index += 1;
      continue;
    }
    if (char === "{") braceDepth += 1;
    else if (char === "}") braceDepth = Math.max(0, braceDepth - 1);
    else if (char === "&" && braceDepth === 0 && environments.length === 0) return true;
    index += 1;
  }
  return false;
}

function shouldWrapAligned(latex: string, display: boolean): boolean {
  return display && hasTopLevelAlignmentTab(latex);
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

function renderFallback(display: boolean): string {
  const tag = display ? "div" : "span";
  const label = "数式を表示できません";
  return `<${tag} class="alinea-math-fallback" role="img" aria-label="${label}">［数式］</${tag}>`;
}

/** Returns an isolated KaTeX macro map so renderers cannot share mutations. */
export function createKatexMacros(): Record<string, string> {
  return { ...BASE_MACROS };
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
  const macros = createKatexMacros();
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
  return renderFallback(display);
}

/** ブロック数式(独立行・中央寄せ)。 */
export function renderBlockMath(latex: string): string {
  return renderMath(latex, { display: true });
}

/** インライン数式(本文中)。 */
export function renderInlineMath(latex: string): string {
  return renderMath(latex, { display: false });
}
