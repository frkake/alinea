import { Fragment, type ReactNode } from "react";
import { renderInlineMath, renderBlockMath } from "@/lib/katex-render";
import { SmartInlineLink } from "@/components/viewer/SmartInlineLink";

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

const RAW_URL_RE = /https?:\/\/[^\s<>"']+/gi;
const TRAILING_URL_PUNCT_RE = /[.,;:!?、。]/;

function splitTrailingUrlPunctuation(value: string): { url: string; trailing: string } {
  let url = value;
  let trailing = "";
  while (url.length > 0 && TRAILING_URL_PUNCT_RE.test(url[url.length - 1] ?? "")) {
    trailing = `${url[url.length - 1]}${trailing}`;
    url = url.slice(0, -1);
  }
  return { url, trailing };
}

function renderPlainText(text: string, keyPrefix: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  let cursor = 0;
  let key = 0;
  for (const match of text.matchAll(RAW_URL_RE)) {
    const start = match.index ?? 0;
    if (start > cursor)
      nodes.push(<Fragment key={`${keyPrefix}-t${key++}`}>{text.slice(cursor, start)}</Fragment>);
    const { url, trailing } = splitTrailingUrlPunctuation(match[0]);
    nodes.push(<SmartInlineLink key={`${keyPrefix}-u${key++}`} href={url} label={url} />);
    if (trailing) nodes.push(<Fragment key={`${keyPrefix}-p${key++}`}>{trailing}</Fragment>);
    cursor = start + match[0].length;
  }
  if (cursor < text.length)
    nodes.push(<Fragment key={`${keyPrefix}-t${key++}`}>{text.slice(cursor)}</Fragment>);
  return nodes;
}

function renderInline(text: string, includeMath: boolean, keyPrefix: string): ReactNode[] {
  const re = inlineTokenRe(includeMath);
  const nodes: ReactNode[] = [];
  let cursor = 0;
  let key = 0;
  for (const match of text.matchAll(re)) {
    const start = match.index ?? 0;
    if (start > cursor)
      nodes.push(...renderPlainText(text.slice(cursor, start), `${keyPrefix}-t${key++}`));
    const g = match.groups ?? {};
    if (g.dmath !== undefined) {
      nodes.push(
        <span
          key={`${keyPrefix}-m${key++}`}
          dangerouslySetInnerHTML={{ __html: renderBlockMath(g.dmath) }}
        />,
      );
    } else if (g.imath !== undefined) {
      nodes.push(
        <span
          key={`${keyPrefix}-m${key++}`}
          dangerouslySetInnerHTML={{ __html: renderInlineMath(g.imath) }}
        />,
      );
    } else if (g.linktext !== undefined) {
      nodes.push(
        <SmartInlineLink
          key={`${keyPrefix}-a${key++}`}
          href={g.linkhref ?? ""}
          label={g.linktext}
        />,
      );
    } else if (g.code !== undefined) {
      nodes.push(
        <code
          key={`${keyPrefix}-c${key++}`}
          style={{ fontFamily: "var(--pr-font-mono)", fontSize: "0.9em" }}
        >
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
  if (cursor < text.length)
    nodes.push(...renderPlainText(text.slice(cursor), `${keyPrefix}-t${key++}`));
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
                <li key={li}>
                  {renderInline((m as RegExpExecArray)[1] ?? "", includeMath, `p${gi}-l${li}`)}
                </li>
              ))}
            </ul>
          );
        }
        return (
          <p key={gi} style={{ margin: 0 }}>
            {renderInline(lines.join(" "), includeMath, `p${gi}`)}
          </p>
        );
      })}
    </>
  );
}
