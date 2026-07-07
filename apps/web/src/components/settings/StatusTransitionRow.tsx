"use client";

import { SegmentedControl } from "@/components/ui/SegmentedControl";
import type { StatusTransition } from "@/components/settings/types";

/** ステータスの自動遷移(4f §4.5.2)。 */
export interface StatusTransitionRowProps {
  value: StatusTransition;
  onChange: (next: StatusTransition) => void;
}

const OPTIONS: ReadonlyArray<{ value: StatusTransition; label: string }> = [
  { value: "auto", label: "自動適用" },
  { value: "suggest", label: "提案する(既定)" },
  { value: "off", label: "提案しない" },
];

export function StatusTransitionRow({ value, onChange }: StatusTransitionRowProps) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 9, padding: "14px 18px" }}>
      <span style={{ fontSize: 12, fontWeight: 600 }}>ステータスの自動遷移</span>
      <div style={{ alignSelf: "flex-start" }}>
        <SegmentedControl
          options={OPTIONS}
          value={value}
          onChange={onChange}
          size="lg"
          ariaLabel="ステータスの自動遷移"
        />
      </div>
      <span style={{ fontSize: 10, color: "var(--pr-text-muted)" }}>
        ステータスは勝手に変わりません。3 分以上読んだとき・最終ページ付近で 1 回だけ提案します
      </span>
    </div>
  );
}
