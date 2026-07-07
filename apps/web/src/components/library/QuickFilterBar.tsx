"use client";

import type { QuickFacet } from "@yakudoku/api-client";
import { FilterChip } from "@/components/ui/FilterChip";
import { QUICK_LABELS, QUICK_ORDER, type Quick } from "@/components/library/types";

/**
 * クイックフィルタ行(1e §4.6 / 4a §4.6)。ステータス系 5 ピル。
 * 件数は facets の quick.*。facets 未取得時はラベルのみ(件数非表示)。
 * M0 スコープ: 属性ドロップダウン・保存フィルタチップは非表示(M1)。
 */
export interface QuickFilterBarProps {
  facets: QuickFacet | undefined;
  quick: Quick;
  onQuickChange: (quick: Quick) => void;
}

export function QuickFilterBar({ facets, quick, onQuickChange }: QuickFilterBarProps) {
  return (
    <div
      role="group"
      aria-label="クイックフィルタ"
      style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}
    >
      {QUICK_ORDER.map((value) => (
        <FilterChip
          key={value}
          label={QUICK_LABELS[value]}
          count={facets ? facets[value] : undefined}
          selected={quick === value}
          onClick={() => {
            onQuickChange(value);
          }}
        />
      ))}
    </div>
  );
}
