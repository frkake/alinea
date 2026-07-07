"use client";

import { useEffect, type CSSProperties, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { Z_INDEX } from "@yakudoku/tokens";

/**
 * 左からオーバーレイする汎用ドロワー(mobile.md §4.3 の目次ドロワー・§5.1 のナビドロワーで共用)。
 * width 280px・height 100dvh・backdrop rgba(0,0,0,0.4)・backdrop タップ / Esc で閉じる。
 */
export interface DrawerProps {
  open: boolean;
  onClose: () => void;
  width?: number;
  ariaLabel: string;
  children: ReactNode;
}

export function Drawer({ open, onClose, width = 280, ariaLabel, children }: DrawerProps) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  const backdropStyle: CSSProperties = {
    position: "fixed",
    inset: 0,
    background: "rgba(0,0,0,0.4)",
    zIndex: Z_INDEX.modal,
  };

  const panelStyle: CSSProperties = {
    position: "fixed",
    top: 0,
    left: 0,
    width,
    height: "100dvh",
    background: "var(--pr-bg-pane)",
    borderRight: "1px solid var(--pr-border-pane)",
    zIndex: Z_INDEX.modal,
    overflowY: "auto",
    transform: "translateX(0)",
    transition: "transform 200ms ease",
  };

  return createPortal(
    <>
      <div
        style={backdropStyle}
        onClick={onClose}
        aria-hidden="true"
      />
      <div role="dialog" aria-label={ariaLabel} style={panelStyle}>
        {children}
      </div>
    </>,
    document.body,
  );
}
