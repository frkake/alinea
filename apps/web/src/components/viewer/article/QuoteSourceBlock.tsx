"use client";

import type { QuoteContentOut } from "@alinea/api-client";
import type { AnchorRef } from "@/components/viewer/article/types";

/** 原文引用ブロック(1h §4.8)。 */
export function QuoteSourceBlock({
  quote,
  onJumpToAnchor,
}: {
  quote: QuoteContentOut;
  onJumpToAnchor: (anchor: AnchorRef) => void;
}) {
  return (
    <div
      style={{
        borderLeft: "3px solid var(--pr-a)",
        background: "var(--pr-bg-card)",
        borderRadius: "0 8px 8px 0",
        padding: "12px 16px",
        display: "flex",
        flexDirection: "column",
        gap: 6,
      }}
    >
      <div
        style={{
          fontFamily: "var(--pr-font-en, 'Source Serif 4'), Georgia, serif",
          fontStyle: "italic",
          fontSize: 13,
          lineHeight: 1.75,
          color: "var(--pr-text-en)",
        }}
      >
        &quot;{quote.text_en}&quot;
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 7, fontSize: 10, color: "var(--pr-text-muted)" }}>
        <span>原文引用</span>
        <span
          style={{
            display: "inline-flex",
            alignItems: "center",
            height: 15,
            padding: "0 6px",
            border: "1px solid var(--pr-am)",
            color: "var(--pr-a)",
            borderRadius: 3,
            fontWeight: 600,
            fontSize: 9,
          }}
        >
          {quote.anchor.display}
        </span>
        <button
          type="button"
          onClick={() => onJumpToAnchor(quote.anchor)}
          style={{
            border: "none",
            background: "transparent",
            padding: 0,
            cursor: "pointer",
            fontFamily: "inherit",
            fontSize: 10,
            color: "var(--pr-a)",
            fontWeight: 600,
          }}
        >
          原文で見る →
        </button>
      </div>
    </div>
  );
}
