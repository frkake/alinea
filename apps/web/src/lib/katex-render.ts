import katex from "katex";

/**
 * KaTeX による数式レンダリング(訳文モードのブロック/インライン数式)。
 *
 * - `throwOnError:false` で、パース失敗時も落とさず元の LaTeX を赤字表示にする
 *   (P3「黙って壊れない」— 数式を勝手に消さない)。
 * - 返り値は KaTeX 生成 HTML(信頼できる自前レンダリング結果)。呼び出し側は
 *   `dangerouslySetInnerHTML` で描画する。CSS は globals.css の `katex/dist/katex.min.css`。
 */
export function renderMath(latex: string, options?: { display?: boolean }): string {
  return katex.renderToString(latex, {
    displayMode: options?.display ?? false,
    throwOnError: false,
    output: "html",
    strict: "ignore",
  });
}

/** ブロック数式(独立行・中央寄せ)。 */
export function renderBlockMath(latex: string): string {
  return renderMath(latex, { display: true });
}

/** インライン数式(本文中)。 */
export function renderInlineMath(latex: string): string {
  return renderMath(latex, { display: false });
}
