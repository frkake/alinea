"use client";

import type { TocNode } from "@yakudoku/api-client";
import { Drawer } from "@/components/ui/Drawer";
import { TocRowGroup } from "@/components/viewer/TocTree";

export interface TocDrawerProps {
  open: boolean;
  onClose: () => void;
  toc: TocNode[];
  activeSectionId: string | null;
  onSectionClick: (sectionId: string) => void;
}

/**
 * モバイル縮退時の目次(mobile.md §4.3)。左からオーバーレイするドロワー。
 * 中身は TocPane の行コンポーネント(TocRow/TocRowGroup)を再利用する。
 * 行タップでその節へスクロールし、ドロワーを閉じる。
 */
export function TocDrawer({ open, onClose, toc, activeSectionId, onSectionClick }: TocDrawerProps) {
  const regular = toc.filter((n) => !n.on_demand);

  return (
    <Drawer open={open} onClose={onClose} width={280} ariaLabel="目次">
      <nav
        aria-label="目次"
        style={{
          display: "flex",
          flexDirection: "column",
          gap: 1,
          padding: "10px 8px",
          fontSize: 12.3,
          color: "var(--pr-text-nav)",
        }}
      >
        <div
          style={{
            fontSize: 11,
            fontWeight: 600,
            color: "var(--pr-text-icon)",
            padding: "0 8px 8px",
          }}
        >
          目次
        </div>
        {regular.map((node) => (
          <TocRowGroup
            key={node.section_id}
            node={node}
            activeSectionId={activeSectionId}
            onSectionClick={(sectionId) => {
              onSectionClick(sectionId);
              onClose();
            }}
          />
        ))}
      </nav>
    </Drawer>
  );
}
