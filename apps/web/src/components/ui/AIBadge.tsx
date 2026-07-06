import type { CSSProperties, ReactNode } from "react";

/** AI 生成マーク「✦」(plans/08 §5.19)。 */
export function AiMark(): ReactNode {
  return <span style={{ color: "var(--pr-acc)" }}>✦</span>;
}

/** AIBadge(plans/08 §5.19)。3 変種。 */
export interface AIBadgeProps {
  variant: "generated" | "external" | "guess";
}

const LABELS: Record<AIBadgeProps["variant"], string> = {
  generated: "AI生成",
  external: "論文外の知識",
  guess: "推測",
};

export function AIBadge({ variant }: AIBadgeProps) {
  const base: CSSProperties = {
    display: "inline-flex",
    alignItems: "center",
    height: 15,
    padding: "0 5px",
    borderRadius: 3,
  };

  const style: CSSProperties =
    variant === "generated"
      ? {
          ...base,
          border: "1px solid var(--pr-border-control)",
          fontSize: 9,
          fontWeight: 600,
          color: "var(--pr-text-icon)",
        }
      : {
          ...base,
          background: "var(--pr-bg-knowledge-label)",
          fontSize: 9,
          fontWeight: 700,
          color: "var(--pr-text-eq)",
        };

  return <span style={style}>{LABELS[variant]}</span>;
}
