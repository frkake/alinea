import type { ReactNode } from "react";
import { renderMath } from "@/lib/katex-render";

/**
 * プレーンテキスト中の `$…$`(インライン数式)区間だけを KaTeX で SSR する
 * (plans/09-screens/4c §2.4。docs/09 §7「数式描画は…サーバーサイドレンダリング可能であること」)。
 *
 * `$` を含まない文字列にはコストが発生しない(単純な文字列分割)。実際の LaTeX → HTML
 * 変換は `@/lib/katex-render` の `renderMath`(既存の訳文モード向けレンダラ)を再利用する。
 */
export function renderInlineMath(text: string): ReactNode {
  if (!text.includes("$")) return text;

  const parts = text.split(/(\$[^$]+\$)/g);
  return parts.map((part, i) => {
    if (part.startsWith("$") && part.endsWith("$") && part.length > 1) {
      // KaTeX 自前レンダリング結果(信頼できる HTML)。
      const html = renderMath(part.slice(1, -1), { display: false });
      return <span key={i} dangerouslySetInnerHTML={{ __html: html }} />;
    }
    return <span key={i}>{part}</span>;
  });
}
