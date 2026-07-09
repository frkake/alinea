"use client";

import { useEffect, useRef, type CSSProperties, type ReactNode, type RefObject } from "react";
import { createPortal } from "react-dom";
import { Z_INDEX } from "@alinea/tokens";

/** モーダル(plans/08 §5.11)。 */
export interface ModalProps {
  open: boolean;
  onClose: () => void;
  width?: number;
  dismissible?: boolean;
  labelledBy: string;
  initialFocusRef?: RefObject<HTMLElement | null>;
  children: ReactNode;
}

const FOCUSABLE =
  'a[href],button:not([disabled]),textarea,input,select,[tabindex]:not([tabindex="-1"])';

export function Modal({
  open,
  onClose,
  width = 460,
  dismissible = true,
  labelledBy,
  initialFocusRef,
  children,
}: ModalProps) {
  const dialogRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const previouslyFocused = document.activeElement as HTMLElement | null;
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    const focusFirst = () => {
      if (initialFocusRef?.current) {
        initialFocusRef.current.focus();
        return;
      }
      const first = dialogRef.current?.querySelector<HTMLElement>(FOCUSABLE);
      first?.focus();
    };
    focusFirst();

    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && dismissible) {
        onClose();
        return;
      }
      if (e.key !== "Tab") return;
      const nodes = dialogRef.current?.querySelectorAll<HTMLElement>(FOCUSABLE);
      if (!nodes || nodes.length === 0) return;
      const list = Array.from(nodes);
      const first = list[0];
      const last = list[list.length - 1];
      if (!first || !last) return;
      const activeEl = document.activeElement;
      if (e.shiftKey && activeEl === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && activeEl === last) {
        e.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = prevOverflow;
      previouslyFocused?.focus();
    };
  }, [open, dismissible, onClose, initialFocusRef]);

  if (!open) return null;

  const scrimStyle: CSSProperties = {
    position: "fixed",
    inset: 0,
    background: "var(--pr-scrim)",
    zIndex: Z_INDEX.modal,
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
  };

  const dialogStyle: CSSProperties = {
    width,
    maxWidth: "calc(100vw - 32px)",
    background: "var(--pr-bg-card)",
    borderRadius: 14,
    boxShadow: "var(--pr-shadow-modal)",
    overflow: "hidden",
  };

  return createPortal(
    <div
      style={scrimStyle}
      onMouseDown={(e) => {
        if (e.target === e.currentTarget && dismissible) onClose();
      }}
    >
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={labelledBy}
        style={dialogStyle}
      >
        {children}
      </div>
    </div>,
    document.body,
  );
}
