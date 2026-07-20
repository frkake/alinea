"use client";

import type { CSSProperties } from "react";
import { CountBadge } from "@/components/ui/CountBadge";

/** サイドパネルタブ(plans/08 §5.16)。排他タブ。 */
export type SidePanelTabId =
  | "chat"
  | "notes"
  | "annotations"
  | "figures"
  | "resources"
  | "vocab-candidates"
  | "info";

const TAB_LABELS: Record<SidePanelTabId, string> = {
  chat: "チャット",
  notes: "メモ",
  annotations: "注釈",
  figures: "図表",
  resources: "リソース",
  "vocab-candidates": "単語候補",
  info: "情報",
};

const ALL_TABS: readonly SidePanelTabId[] = [
  "chat",
  "notes",
  "annotations",
  "figures",
  "resources",
  "vocab-candidates",
  "info",
];

export interface SidePanelTabsProps {
  active: SidePanelTabId;
  counts: Partial<Record<SidePanelTabId, number>>;
  onChange: (tab: SidePanelTabId) => void;
  /** 表示するタブ(既定は 6 タブ全て)。M0 は消費側が chat/figures/info の 3 タブに絞る。 */
  tabs?: readonly SidePanelTabId[];
  /** 親コンテナ側で罫線を持つ場合は false。 */
  borderBottom?: boolean;
}

export function SidePanelTabs({
  active,
  counts,
  onChange,
  tabs = ALL_TABS,
  borderBottom = true,
}: SidePanelTabsProps) {
  return (
    <div
      role="tablist"
      style={{
        display: "flex",
        flexWrap: "nowrap",
        borderBottom: borderBottom ? "1px solid var(--pr-border-soft)" : undefined,
        padding: "0 6px",
        minWidth: 0,
        width: "max-content",
      }}
    >
      {tabs.map((tab) => {
        const isActive = tab === active;
        const style: CSSProperties = {
          flex: "none",
          padding: "10px 7px 8px",
          fontSize: 12,
          border: "none",
          background: "transparent",
          cursor: "pointer",
          fontFamily: "inherit",
          color: isActive ? "var(--pr-acc)" : "var(--pr-text-sub2)",
          fontWeight: isActive ? 600 : 400,
          boxShadow: isActive ? "inset 0 -2px var(--pr-acc)" : undefined,
          whiteSpace: "nowrap",
        };
        const count = counts[tab];
        return (
          <button
            key={tab}
            type="button"
            role="tab"
            aria-selected={isActive}
            style={style}
            onClick={() => {
              onChange(tab);
            }}
          >
            {TAB_LABELS[tab]}
            {typeof count === "number" ? <CountBadge count={count} variant="tab" /> : null}
          </button>
        );
      })}
    </div>
  );
}
