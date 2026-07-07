"use client";

import type { CSSProperties } from "react";
import { AIBadge, AiMark } from "@/components/ui/AIBadge";

export interface SummaryCardProps {
  /** LibraryItemSummary.summary_3line(要素 3。番号は表示側で付与)。null=生成前。 */
  lines: string[] | null;
  /** 「詳細要約 →」= panel=chat + QuickAction 'detailed_summary'(docs/04 §3)。 */
  onDetailedSummary?: () => void;
}

const cardStyle: CSSProperties = {
  background: "var(--pr-bg-card)",
  border: "1px solid var(--pr-border-card)",
  borderRadius: 10,
  padding: "16px 20px",
  marginBottom: 26,
  fontFamily: "var(--pr-font-ui)",
};

/** ✦ 3行要約カード(1b §4.5-2)。 */
export function SummaryCard({ lines, onDetailedSummary }: SummaryCardProps) {
  return (
    <div style={cardStyle} data-testid="summary-card">
      <div style={{ display: "flex", alignItems: "center", gap: 7, marginBottom: 10 }}>
        <span
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 4,
            color: "var(--pr-acc)",
            fontSize: 11.5,
            fontWeight: 700,
          }}
        >
          <AiMark />3行要約
        </span>
        <AIBadge variant="generated" />
        <button
          type="button"
          onClick={onDetailedSummary}
          style={{
            marginLeft: "auto",
            border: "none",
            background: "transparent",
            cursor: "pointer",
            fontFamily: "inherit",
            fontSize: 10.5,
            color: "var(--pr-acc)",
          }}
        >
          詳細要約 →
        </button>
      </div>
      {lines && lines.length > 0 ? (
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            gap: 7,
            fontSize: 13,
            lineHeight: 1.75,
            color: "var(--pr-text-en)",
          }}
        >
          {lines.map((line, i) => (
            <div key={i} style={{ display: "flex", gap: 9 }}>
              <span style={{ color: "var(--pr-text-muted)", fontWeight: 600 }}>{i + 1}</span>
              <span>{line}</span>
            </div>
          ))}
        </div>
      ) : (
        <div style={{ fontSize: 12, color: "var(--pr-text-muted)" }}>✦ 要約を生成しています…</div>
      )}
    </div>
  );
}
