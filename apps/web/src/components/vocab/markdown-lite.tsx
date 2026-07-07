import { Fragment, type ReactNode } from "react";

/**
 * 生成テキストの Markdown サブセット(`**bold**` / `*italic*`)を表示用ノードへ変換する
 * (plans/09-screens/4d §4.2.6 決定: HTML は保存せず、表示時にのみ変換する)。
 */
const TOKEN_RE = /\*\*([^*]+)\*\*|\*([^*]+)\*/g;

export function renderMarkdownLite(text: string): ReactNode {
  const nodes: ReactNode[] = [];
  let cursor = 0;
  let key = 0;
  for (const match of text.matchAll(TOKEN_RE)) {
    const start = match.index ?? 0;
    if (start > cursor) {
      nodes.push(<Fragment key={key++}>{text.slice(cursor, start)}</Fragment>);
    }
    const [full, bold, italic] = match;
    if (bold !== undefined) {
      nodes.push(<b key={key++}>{bold}</b>);
    } else if (italic !== undefined) {
      nodes.push(<i key={key++}>{italic}</i>);
    }
    cursor = start + full.length;
  }
  if (cursor < text.length) {
    nodes.push(<Fragment key={key++}>{text.slice(cursor)}</Fragment>);
  }
  return <>{nodes}</>;
}
