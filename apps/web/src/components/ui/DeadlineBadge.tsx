import type { CSSProperties } from "react";

/** 締切バッジ(plans/08 §5.5)。 */
export interface DeadlineBadgeProps {
  /** 'M/D' 表示文字列(整形は呼び出し側)。null は未設定。 */
  date: string | null;
  variant?: "chip" | "text";
  withLabel?: boolean;
  /** chip 変種の文字サイズ(既定 9.5px)。4c は親の 11px を継承するため 11 を渡す(plans/09-screens/4c §3)。 */
  fontSize?: 9.5 | 11;
}

export function DeadlineBadge({
  date,
  variant = "chip",
  withLabel = false,
  fontSize = 9.5,
}: DeadlineBadgeProps) {
  if (variant === "text") {
    if (date === null) {
      return <span style={{ fontSize: 11, color: "var(--pr-text-muted)" }}>—</span>;
    }
    return (
      <span style={{ fontSize: 11, color: "var(--pr-warn)", fontWeight: 600 }}>
        {withLabel ? `締切 ${date}` : date}
      </span>
    );
  }

  if (date === null) {
    return <span style={{ fontSize: 11, color: "var(--pr-text-muted)" }}>—</span>;
  }

  const style: CSSProperties = {
    display: "inline-flex",
    alignItems: "center",
    height: 17,
    padding: "0 6px",
    borderRadius: 3,
    background: "var(--pr-warn-bg)",
    color: "var(--pr-warn)",
    fontSize,
    fontWeight: 600,
  };
  return <span style={style}>{withLabel ? `締切 ${date}` : date}</span>;
}
