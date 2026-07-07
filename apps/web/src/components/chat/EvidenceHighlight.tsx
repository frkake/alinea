"use client";

import { Fragment, type ReactNode } from "react";
import type { AnchorRef, EvidenceRef } from "@yakudoku/api-client";
import { EvidenceChip } from "@/components/ui/EvidenceChip";
import { renderInlineMath } from "@/lib/katex-render";

export interface EvidenceHighlightProps {
  /** MarkdownBlock.text。インライン根拠は `[[ev:n]]` トークン(plans/03 §10.2)。 */
  text: string;
  /** 対応する EvidenceRef 群(ref 番号 → 表示・アンカー)。 */
  evidence: EvidenceRef[];
  /** 根拠チップクリック → 本文該当ブロックへジャンプ+双方向強調(1a §5.2)。 */
  onEvidenceJump?: (anchor: AnchorRef) => void;
}

/** `[[ev:n]]` / `**bold**` / `$math$` を分割するトークナイザ(M0 の軽量 Markdown)。 */
const TOKEN_RE = /\[\[ev:(\d+)\]\]|\*\*([^*]+)\*\*|\$([^$]+)\$/g;

/**
 * チャット回答本文のインライン描画(1a §4.5)。`[[ev:n]]` を `EvidenceChip`(再利用)に、
 * `**…**` を太字に、`$…$` を KaTeX インライン数式に置換する。チップは ¶/式/図 粒度の
 * `display` を表示し、クリックで本文へジャンプする(チャット⇔本文の双方向リンク)。
 */
export function EvidenceHighlight({ text, evidence, onEvidenceJump }: EvidenceHighlightProps) {
  const byRef = new Map<number, EvidenceRef>();
  for (const e of evidence) byRef.set(e.ref, e);

  const nodes: ReactNode[] = [];
  let cursor = 0;
  let key = 0;
  for (const match of text.matchAll(TOKEN_RE)) {
    const start = match.index ?? 0;
    if (start > cursor) {
      nodes.push(<Fragment key={key++}>{text.slice(cursor, start)}</Fragment>);
    }
    const [full, evRef, bold, math] = match;
    if (evRef !== undefined) {
      const ev = byRef.get(Number(evRef));
      if (ev) {
        nodes.push(
          <EvidenceChip
            key={key++}
            // EvidenceChip の anchor 値は onJump 経由でのみ使われる。ここでは
            // クロージャで実 AnchorRef を渡すため、表示専用のダミーを与える。
            anchor={{ type: "section", sectionNumber: ev.display }}
            label={ev.display}
            size="inline"
            onJump={() => onEvidenceJump?.(ev.anchor)}
          />,
        );
      }
      // 実在しない ev 参照はトークンごと除去(サーバー側で除去済みだが二重防御)。
    } else if (bold !== undefined) {
      nodes.push(<b key={key++}>{bold}</b>);
    } else if (math !== undefined) {
      nodes.push(
        <span
          key={key++}
          // KaTeX の信頼できる自前レンダリング出力。
          dangerouslySetInnerHTML={{ __html: renderInlineMath(math) }}
        />,
      );
    }
    cursor = start + full.length;
  }
  if (cursor < text.length) {
    nodes.push(<Fragment key={key++}>{text.slice(cursor)}</Fragment>);
  }

  return <>{nodes}</>;
}
