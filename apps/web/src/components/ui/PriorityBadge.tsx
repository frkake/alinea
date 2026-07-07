import type { CSSProperties } from "react";

/** 優先度バッジ(plans/08 §5.4)。テキスト変種(既定)+ 1d 用チップ変種(plans/09-screens/1d §4.5)。 */
export interface PriorityBadgeProps {
  priority: "high" | "mid" | "low";
  withPrefix?: boolean;
  /** 'text'(既定・1e/4a) / 'chip'(1d すぐ読むキュー行。常に「優先: {label}」表示、背景付き)。 */
  variant?: "text" | "chip";
}

const PRIORITY: Record<"high" | "mid" | "low", { label: string; color: string; weight: number }> =
  {
    high: { label: "高", color: "var(--pr-warn)", weight: 600 },
    mid: { label: "中", color: "var(--pr-text-sub2)", weight: 400 },
    low: { label: "低", color: "var(--pr-text-muted)", weight: 400 },
  };

/** 1d §4.5 のチップ配色(高=警告色/中・低=グレー、text 変種とは重みが異なる)。 */
const CHIP: Record<"high" | "mid" | "low", { background: string; color: string; weight: number }> =
  {
    high: { background: "var(--pr-warn-bg)", color: "var(--pr-warn)", weight: 700 },
    mid: { background: "var(--pr-bg-inset)", color: "var(--pr-text-sub2)", weight: 600 },
    low: { background: "var(--pr-bg-inset)", color: "var(--pr-text-sub2)", weight: 600 },
  };

export function PriorityBadge({ priority, withPrefix = false, variant = "text" }: PriorityBadgeProps) {
  const p = PRIORITY[priority];
  const label = `優先: ${p.label}`;

  if (variant === "chip") {
    const c = CHIP[priority];
    const style: CSSProperties = {
      display: "inline-flex",
      alignItems: "center",
      height: 17,
      padding: "0 6px",
      borderRadius: 3,
      background: c.background,
      color: c.color,
      fontSize: 9.5,
      fontWeight: c.weight,
    };
    return <span style={style}>{label}</span>;
  }

  const style: CSSProperties = { fontSize: 11, color: p.color, fontWeight: p.weight };
  return <span style={style}>{withPrefix ? label : p.label}</span>;
}
