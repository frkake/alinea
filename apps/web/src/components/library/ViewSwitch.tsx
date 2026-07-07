"use client";

import { SegmentedControl } from "@/components/ui/SegmentedControl";
import type { LibraryView } from "@/components/library/types";

/** ビュー切替(1e §4.5 / 4a §4.5)。カード ⇄ テーブル。 */
export interface ViewSwitchProps {
  view: LibraryView;
  onViewChange: (view: LibraryView) => void;
}

const OPTIONS: ReadonlyArray<{ value: LibraryView; label: string }> = [
  { value: "card", label: "カード" },
  { value: "table", label: "テーブル" },
];

export function ViewSwitch({ view, onViewChange }: ViewSwitchProps) {
  return (
    <SegmentedControl
      options={OPTIONS}
      value={view}
      onChange={onViewChange}
      size="sm"
      ariaLabel="表示形式"
    />
  );
}
