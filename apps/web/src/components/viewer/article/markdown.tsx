import { Fragment, type ReactNode } from "react";
import { renderInlineMath, renderBlockMath } from "@/lib/katex-render";

/**
 * 記事ブロック(paragraph)の Markdown サブセット描画(1h §4.7 決定)。
 *
 * 許容サブセット: 強調(`**bold**` / `*italic*`)・インラインコード(`` `code` ``)・
 * 単純な箇条書き(`- ` / `* ` 行)・リンク(`[text](url)`。`target="_blank" rel="noopener"`)。
 * 生 HTML はエスケープする(React のテキストノードとして描画するため既定でエスケープ済み)。
 * 数式は `includeMath` の時のみ `$…$` / `$$…$$` を KaTeX でレンダリングする
 * (`include_math=false` の記事では数式記法はプレーンテキストのまま表示)。
 */

function inlineTokenRe(includeMath: boolean): RegExp {
  const math = includeMath ? "\\$\\$(?<dmath>[^$]+)\\$\\$|\\$(?<imath>[^$\\n]+)\\$|" : "";
  return new RegExp(
    `${math}\\[(?<linktext>[^\\]]+)\\]\\((?<linkhref>[^)]+)\\)|\`(?<code>[^\`]+)\`|\\*\\*(?<bold>[^*]+)\\*\\*|\\*(?<italic>[^*]+)\\*`,
    "g",
  );
}

function renderInline(text: string, includeMath: boolean, keyPrefix: string): ReactNode[] {
  const re = inlineTokenRe(includeMath);
  const nodes: ReactNode[] = [];
  let cursor = 0;
  let key = 0;
  for (const match of text.matchAll(re)) {
    const start = match.index ?? 0;
    if (start > cursor) nodes.push(<Fragment key={`${keyPrefix}-t${key++}`}>{text.slice(cursor, start)}</Fragment>);
    const g = match.groups ?? {};
    if (g.dmath !== undefined) {
      nodes.push(
        <span key={`${keyPrefix}-m${key++}`} dangerouslySetInnerHTML={{ __html: renderBlockMath(g.dmath) }} />,
      );
    } else if (g.imath !== undefined) {
      nodes.push(
        <span key={`${keyPrefix}-m${key++}`} dangerouslySetInnerHTML={{ __html: renderInlineMath(g.imath) }} />,
      );
    } else if (g.linktext !== undefined) {
      nodes.push(
        <a key={`${keyPrefix}-a${key++}`} href={g.linkhref} target="_blank" rel="noopener">
          {g.linktext}
        </a>,
      );
    } else if (g.code !== undefined) {
      nodes.push(
        <code key={`${keyPrefix}-c${key++}`} style={{ fontFamily: "var(--pr-font-mono)", fontSize: "0.9em" }}>
          {g.code}
        </code>,
      );
    } else if (g.bold !== undefined) {
      nodes.push(<b key={`${keyPrefix}-b${key++}`}>{g.bold}</b>);
    } else if (g.italic !== undefined) {
      nodes.push(<i key={`${keyPrefix}-i${key++}`}>{g.italic}</i>);
    }
    cursor = start + match[0].length;
  }
  if (cursor < text.length) nodes.push(<Fragment key={`${keyPrefix}-t${key++}`}>{text.slice(cursor)}</Fragment>);
  return nodes;
}

const LIST_ITEM_RE = /^[-*]\s+(.*)$/;

export function renderArticleMarkdown(markdown: string, includeMath: boolean): ReactNode {
  const groups = markdown.split(/\n{2,}/);
  return (
    <>
      {groups.map((group, gi) => {
        const lines = group.split("\n").filter((l) => l.trim() !== "");
        if (lines.length === 0) return null;
        const listMatches = lines.map((l) => LIST_ITEM_RE.exec(l));
        if (listMatches.every((m) => m !== null)) {
          return (
            <ul key={gi} style={{ margin: 0, paddingLeft: 20 }}>
              {listMatches.map((m, li) => (
                <li key={li}>{renderInline((m as RegExpExecArray)[1] ?? "", includeMath, `p${gi}-l${li}`)}</li>
              ))}
            </ul>
          );
        }
        return <p key={gi} style={{ margin: 0 }}>{renderInline(lines.join(" "), includeMath, `p${gi}`)}</p>;
      })}
    </>
  );
}
