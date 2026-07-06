import type { CSSProperties } from "react";

/** 優先度バッジ(plans/08 §5.4)。テキストのみ。 */
export interface PriorityBadgeProps {
  priority: "high" | "mid" | "low";
  withPrefix?: boolean;
}

const PRIORITY: Record<"high" | "mid" | "low", { label: string; color: string; weight: number }> =
  {
    high: { label: "高", color: "var(--pr-warn)", weight: 600 },
    mid: { label: "中", color: "var(--pr-text-sub2)", weight: 400 },
    low: { label: "低", color: "var(--pr-text-muted)", weight: 400 },
  };

export function PriorityBadge({ priority, withPrefix = false }: PriorityBadgeProps) {
  const p = PRIORITY[priority];
  const style: CSSProperties = { fontSize: 11, color: p.color, fontWeight: p.weight };
  return <span style={style}>{withPrefix ? `優先: ${p.label}` : p.label}</span>;
}
