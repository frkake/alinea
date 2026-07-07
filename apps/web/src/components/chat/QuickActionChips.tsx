"use client";

import type { CSSProperties } from "react";

/** 常設サジェストチップ 5 種の quick_action(plans/03 §10.2・1a §4.5)。 */
export type QuickActionId =
  | "summary_3line"
  | "beginner_explain"
  | "contributions_limits"
  | "experiment_setup"
  | "implementation_points";

/** 文言・順序は 1a §4.5 の逐語(勝手な言い換え禁止)。 */
export const QUICK_ACTIONS: ReadonlyArray<{ id: QuickActionId; label: string }> = [
  { id: "summary_3line", label: "3行要約" },
  { id: "beginner_explain", label: "初心者向け解説" },
  { id: "contributions_limits", label: "貢献と限界" },
  { id: "experiment_setup", label: "実験設定の整理" },
  { id: "implementation_points", label: "実装の要点" },
];

export interface QuickActionChipsProps {
  onPick: (quickAction: QuickActionId) => void;
  /** ストリーミング中は非活性(1a §5.3)。 */
  disabled?: boolean;
}

const chipStyle = (disabled: boolean): CSSProperties => ({
  display: "inline-flex",
  alignItems: "center",
  height: 21,
  padding: "0 8px",
  border: "1px solid var(--pr-border-control)",
  borderRadius: 999,
  fontSize: 10.5,
  color: "var(--pr-text-mid)",
  background: "transparent",
  fontFamily: "inherit",
  cursor: disabled ? "default" : "pointer",
  opacity: disabled ? 0.45 : 1,
});

/** サジェストチップ行(1a §4.5)。押下で quick_action を即送信(入力欄を経由しない)。 */
export function QuickActionChips({ onPick, disabled = false }: QuickActionChipsProps) {
  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: 5 }}>
      {QUICK_ACTIONS.map((qa) => (
        <button
          key={qa.id}
          type="button"
          disabled={disabled}
          style={chipStyle(disabled)}
          onClick={() => onPick(qa.id)}
        >
          {qa.label}
        </button>
      ))}
    </div>
  );
}
