"use client";

import { useRef, useState, type CSSProperties, type TouchEvent } from "react";
import { createPortal } from "react-dom";
import { Z_INDEX } from "@yakudoku/tokens";
import type { LicenseCard, PaperBib, RevisionInfo, TimelineEntry } from "@yakudoku/api-client";
import { SidePanelTabs, type SidePanelTabId } from "@/components/ui/SidePanelTabs";
import { ChatPanel } from "@/components/chat/ChatPanel";
import { AnnotationListPanel } from "@/components/viewer/AnnotationListPanel";
import { InfoPanel } from "@/components/viewer/InfoPanel";

/** モバイル縮退のボトムシートで提供する 3 タブのみ(mobile.md §4.5 決定)。 */
export const MOBILE_SHEET_TABS: readonly SidePanelTabId[] = ["chat", "annotations", "info"];

export interface MobileBottomSheetProps {
  open: boolean;
  onClose: () => void;
  activeTab: SidePanelTabId;
  onTabChange: (tab: SidePanelTabId) => void;
  counts?: Partial<Record<SidePanelTabId, number>>;
  itemId: string;
  paper: PaperBib;
  revision: RevisionInfo;
  licenseCard: LicenseCard;
  ingestTimeline: TimelineEntry[];
}

/**
 * サイドパネルの代わりに開く閲覧専用ボトムシート(mobile.md §4.5)。
 * height 60dvh・上角 radius 16px・グラバー・backdrop タップ / 下スワイプで閉じる。
 * タブ本体はデスクトップのタブコンポーネントを readOnly で再利用する(決定)。
 */
export function MobileBottomSheet({
  open,
  onClose,
  activeTab,
  onTabChange,
  counts = {},
  itemId,
  paper,
  revision,
  licenseCard,
  ingestTimeline,
}: MobileBottomSheetProps) {
  const touchStartY = useRef<number | null>(null);
  const [dragY, setDragY] = useState(0);

  if (!open) return null;

  const onTouchStart = (e: TouchEvent<HTMLDivElement>) => {
    touchStartY.current = e.touches[0]?.clientY ?? null;
  };
  const onTouchMove = (e: TouchEvent<HTMLDivElement>) => {
    if (touchStartY.current == null) return;
    const dy = (e.touches[0]?.clientY ?? touchStartY.current) - touchStartY.current;
    setDragY(Math.max(0, dy));
  };
  const onTouchEnd = () => {
    if (dragY > 80) onClose();
    setDragY(0);
    touchStartY.current = null;
  };

  const backdropStyle: CSSProperties = {
    position: "fixed",
    inset: 0,
    background: "rgba(0,0,0,0.4)",
    zIndex: Z_INDEX.modal,
  };

  const sheetStyle: CSSProperties = {
    position: "fixed",
    left: 0,
    right: 0,
    bottom: 0,
    height: "60dvh",
    background: "var(--pr-bg-card)",
    borderTopLeftRadius: 16,
    borderTopRightRadius: 16,
    boxShadow: "var(--pr-shadow-modal)",
    zIndex: Z_INDEX.modal,
    display: "flex",
    flexDirection: "column",
    minHeight: 0,
    transform: `translateY(${dragY}px)`,
    transition: dragY === 0 ? "transform 150ms ease" : undefined,
  };

  const active = MOBILE_SHEET_TABS.includes(activeTab) ? activeTab : "chat";

  return createPortal(
    <>
      <div style={backdropStyle} onClick={onClose} aria-hidden="true" />
      <div role="dialog" aria-label="論文パネル" style={sheetStyle}>
        <div
          onTouchStart={onTouchStart}
          onTouchMove={onTouchMove}
          onTouchEnd={onTouchEnd}
          style={{ display: "flex", justifyContent: "center", padding: "8px 0 4px", flex: "none" }}
        >
          <span
            aria-hidden="true"
            style={{ width: 36, height: 4, borderRadius: 999, background: "var(--pr-border-control)" }}
          />
        </div>
        <SidePanelTabs
          active={active}
          counts={counts}
          tabs={MOBILE_SHEET_TABS}
          onChange={onTabChange}
        />
        <div style={{ flex: 1, minHeight: 0, overflowY: "auto" }}>
          {active === "chat" ? (
            <ChatPanel itemId={itemId} readOnly />
          ) : active === "annotations" ? (
            <AnnotationListPanel readOnly />
          ) : (
            <InfoPanel
              itemId={itemId}
              paper={paper}
              revision={revision}
              licenseCard={licenseCard}
              ingestTimeline={ingestTimeline}
              readOnly
            />
          )}
        </div>
      </div>
    </>,
    document.body,
  );
}
