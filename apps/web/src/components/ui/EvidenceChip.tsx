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
  /**
   * `figure-footer`(1h §4.6 決定): h15px/9px/radius 3px・背景なし
   * (概要図フッタ・原文引用の位置チップなど 1h 実測値専用)。
   */
  size?: "inline" | "header" | "figure-footer";
  onJump: (anchor: EvidenceAnchor) => void;
}

export function EvidenceChip({ anchor, label, size = "inline", onJump }: EvidenceChipProps) {
  const style: CSSProperties =
    size === "figure-footer"
      ? {
          display: "inline-flex",
          alignItems: "center",
          height: 15,
          padding: "0 6px",
          border: "1px solid var(--pr-am)",
          color: "var(--pr-a)",
          borderRadius: 3,
          fontSize: 9,
          fontWeight: 600,
          verticalAlign: 2,
          cursor: "pointer",
          fontFamily: "inherit",
        }
      : {
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
