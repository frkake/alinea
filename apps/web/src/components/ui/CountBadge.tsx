import type { CSSProperties } from "react";

/** 数値カウントの 3 変種(plans/08 §5.7)。 */
export interface CountBadgeProps {
  count: number;
  variant: "annotation" | "tab" | "nav";
}

export function CountBadge({ count, variant }: CountBadgeProps) {
  if (variant === "annotation") {
    const style: CSSProperties = {
      display: "inline-flex",
      alignItems: "center",
      justifyContent: "center",
      minWidth: 15,
      height: 15,
      borderRadius: 8,
      padding: "0 4px",
      background: "var(--pr-ann-important-count-bg)",
      color: "var(--pr-ann-important-chip-fg)",
      fontSize: 9.5,
      fontWeight: 600,
    };
    return <span style={style}>{count}</span>;
  }

  if (variant === "tab") {
    return (
      <span style={{ fontSize: 10, color: "var(--pr-text-muted)", marginLeft: 3 }}>{count}</span>
    );
  }

  // nav: アクティブ項目内では color を継承(=アクセント)。
  return <span style={{ fontSize: 10.5, color: "var(--pr-text-muted)" }}>{count}</span>;
}
