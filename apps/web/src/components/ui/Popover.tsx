"use client";

import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type CSSProperties,
  type ReactNode,
  type RefObject,
} from "react";
import { createPortal } from "react-dom";
import { Z_INDEX } from "@yakudoku/tokens";

/** ポップオーバー(plans/08 §5.10)。position:fixed + アンカー矩形からの手動配置。 */
export interface PopoverProps {
  open: boolean;
  onClose: () => void;
  anchorRef: RefObject<HTMLElement | null>;
  width: number;
  placement?: "bottom-start" | "bottom-end";
  caret?: boolean;
  caretOffset?: { side: "left" | "right"; px: number };
  children: ReactNode;
}

export function Popover({
  open,
  onClose,
  anchorRef,
  width,
  placement = "bottom-start",
  caret = true,
  caretOffset,
  children,
}: PopoverProps) {
  const panelRef = useRef<HTMLDivElement>(null);
  const [mounted, setMounted] = useState(false);
  const [pos, setPos] = useState<{ top: number; left: number }>({ top: 0, left: 0 });

  useEffect(() => {
    setMounted(true);
  }, []);

  const reposition = useCallback(() => {
    const anchor = anchorRef.current;
    if (!anchor) return;
    const r = anchor.getBoundingClientRect();
    const gap = caret ? 8 : 4;
    const top = r.bottom + gap;
    const left = placement === "bottom-end" ? r.right - width : r.left;
    setPos({ top, left });
  }, [anchorRef, placement, width, caret]);

  useLayoutEffect(() => {
    if (!open) return;
    reposition();
    window.addEventListener("resize", reposition);
    window.addEventListener("scroll", reposition, true);
    return () => {
      window.removeEventListener("resize", reposition);
      window.removeEventListener("scroll", reposition, true);
    };
  }, [open, reposition]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    const onDown = (e: MouseEvent) => {
      const target = e.target as Node;
      if (panelRef.current?.contains(target)) return;
      if (anchorRef.current?.contains(target)) return;
      onClose();
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onDown);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onDown);
    };
  }, [open, onClose, anchorRef]);

  if (!open || !mounted) return null;

  const panelStyle: CSSProperties = {
    position: "fixed",
    top: pos.top,
    left: pos.left,
    width,
    background: "var(--pr-bg-pop)",
    border: "1px solid var(--pr-border-pop)",
    borderRadius: 10,
    boxShadow: "var(--pr-shadow-pop)",
    overflow: caret ? "visible" : "hidden",
    zIndex: Z_INDEX.popover,
  };

  const caretStyle: CSSProperties = {
    position: "absolute",
    top: -5,
    width: 9,
    height: 9,
    background: "var(--pr-bg-pop)",
    borderLeft: "1px solid var(--pr-border-pop)",
    borderTop: "1px solid var(--pr-border-pop)",
    transform: "rotate(45deg)",
    ...(caretOffset
      ? { [caretOffset.side]: caretOffset.px }
      : placement === "bottom-end"
        ? { right: 16 }
        : { left: 16 }),
  };

  return createPortal(
    <div ref={panelRef} role="dialog" style={panelStyle}>
      {caret ? <span style={caretStyle} aria-hidden="true" /> : null}
      {children}
    </div>,
    document.body,
  );
}
