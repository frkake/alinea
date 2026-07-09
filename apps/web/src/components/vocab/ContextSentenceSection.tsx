"use client";

import type { VocabHighlight } from "@alinea/api-client";

export interface ContextSentenceSectionProps {
  contextSentence: string;
  highlight: VocabHighlight;
  /** 出典表示("Rectified Flow · §2.1")から §以下だけを抜き出したもの。 */
  sectionRef: string;
  onOpenSource: () => void;
}

/**
 * 「文脈センテンス」(4d §4.2.6-2)。原文英語(イタリック・左罫線)+対象語ハイライト+
 * 「原文で見る →」。編集不可(原文の引用のため。決定)。
 */
export function ContextSentenceSection({
  contextSentence,
  highlight,
  sectionRef,
  onOpenSource,
}: ContextSentenceSectionProps) {
  const before = contextSentence.slice(0, highlight.start);
  const marked = contextSentence.slice(highlight.start, highlight.end);
  const after = contextSentence.slice(highlight.end);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 5 }}>
      <span
        style={{
          fontSize: 10.5,
          fontWeight: 700,
          color: "var(--pr-text-muted)",
          letterSpacing: "0.4px",
        }}
      >
        文脈センテンス
      </span>
      <div
        style={{
          fontSize: 11,
          lineHeight: 1.7,
          color: "#5B6067",
          borderLeft: "2px solid var(--pr-border-card)",
          paddingLeft: 9,
          fontFamily: "var(--pr-font-en)",
          fontStyle: "italic",
        }}
      >
        {before}
        <mark
          style={{
            background: "var(--pr-ann-important-chip-bg)",
            borderRadius: 2,
            padding: "0 1px",
            fontStyle: "normal",
          }}
        >
          {marked}
        </mark>
        {after}
        <span style={{ fontFamily: "var(--pr-font-ui)", color: "var(--pr-text-muted)", fontSize: 9.5, fontStyle: "normal" }}>
          {" "}
          — {sectionRef} ·{" "}
        </span>
        <button
          type="button"
          onClick={onOpenSource}
          style={{
            fontFamily: "var(--pr-font-ui)",
            color: "var(--pr-text-muted)",
            fontSize: 9.5,
            fontStyle: "normal",
            background: "none",
            border: "none",
            padding: 0,
            cursor: "pointer",
            textDecoration: "underline",
          }}
        >
          原文で見る →
        </button>
      </div>
    </div>
  );
}

/** 出典表示("Rectified Flow · §2.1")から § 以下だけを抜き出す(4d §4.2.6-2)。 */
export function extractSectionRef(display: string): string {
  const parts = display.split(" · ");
  const last = parts[parts.length - 1];
  return last && last.startsWith("§") ? last : display;
}
