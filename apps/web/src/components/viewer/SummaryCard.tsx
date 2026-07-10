"use client";

import type { CSSProperties } from "react";
import { AIBadge, AiMark } from "@/components/ui/AIBadge";

export interface SummaryCardProps {
  /** LibraryItemSummary.summary_3line。フィールド名は互換維持だが内容は構造化された論文概要。 */
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

const FALLBACK_LABELS = ["課題", "提案", "仕組み", "検証", "結果", "限界"];

function splitSummaryLine(line: string, index: number): { label: string; body: string } {
  const match = line.match(/^([^:：]{1,12})[:：]\s*(.+)$/);
  if (match?.[1] && match[2]) return { label: match[1], body: match[2] };
  return { label: FALLBACK_LABELS[index] ?? `要点 ${index + 1}`, body: line };
}

/** ひと目で研究の全体像が分かる論文概要カード。 */
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
          <AiMark />論文概要
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
          {lines.map((line, index) => {
            const item = splitSummaryLine(line, index);
            return (
              <div key={`${item.label}-${index}`} style={{ display: "grid", gridTemplateColumns: "58px 1fr", gap: 10 }}>
                <span style={{ color: "var(--pr-acc)", fontWeight: 700 }}>{item.label}</span>
                <span>{item.body}</span>
              </div>
            );
          })}
        </div>
      ) : (
        <div style={{ fontSize: 12, color: "var(--pr-text-muted)" }}>✦ 要約を生成しています…</div>
      )}
    </div>
  );
}
