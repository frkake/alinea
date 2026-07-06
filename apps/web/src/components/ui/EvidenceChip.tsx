"use client";

import type { CSSProperties } from "react";

/** 根拠チップ(plans/08 §5.18)。 */
export type EvidenceAnchor =
  | { type: "section"; sectionNumber: string }
  | { type: "paragraph"; sectionNumber: string; para: number }
  | { type: "equation"; eqNumber: number }
  | { type: "figure"; figNumber: number }
  | { type: "table"; tableNumber: number };

export interface EvidenceChipProps {
  anchor: EvidenceAnchor;
  /** 表示文字列は生成側が確定(例 '式(5) · §2.1')。 */
  label: string;
  size?: "inline" | "header";
  onJump: (anchor: EvidenceAnchor) => void;
}

export function EvidenceChip({ anchor, label, size = "inline", onJump }: EvidenceChipProps) {
  const style: CSSProperties = {
    display: "inline-flex",
    alignItems: "center",
    height: size === "header" ? 17 : 16,
    padding: "0 6px",
    border: "1px solid var(--pr-acc-m)",
    color: "var(--pr-acc)",
    background: "var(--pr-acc-s)",
    borderRadius: 4,
    fontSize: size === "header" ? 10 : 9.5,
    fontWeight: 600,
    verticalAlign: 2,
    cursor: "pointer",
    fontFamily: "inherit",
  };
  return (
    <button
      type="button"
      style={style}
      onClick={() => {
        onJump(anchor);
      }}
    >
      {label}
    </button>
  );
}
