"use client";

import { useEffect, useState, type CSSProperties } from "react";
import { createPortal } from "react-dom";
import { create } from "zustand";
import { Z_INDEX } from "@yakudoku/tokens";

/** トースト(plans/08 §5.20)。一括操作バーの視覚言語を流用。 */
export interface ToastOptions {
  kind: "info" | "success" | "error";
  message: string;
  action?: { label: string; onClick: () => void };
}

interface ToastState {
  current: (ToastOptions & { id: number }) | null;
  show: (options: ToastOptions) => void;
  dismiss: () => void;
}

/** Zustand ストア yk-toast。同時 1 件のみ(後着優先で置換)。 */
const useToastStore = create<ToastState>((set) => ({
  current: null,
  show: (options) => {
    set({ current: { ...options, id: Date.now() } });
  },
  dismiss: () => {
    set({ current: null });
  },
}));

/** 呼び出し用フック: toast({ kind, message, action })。 */
export function useToast(): (options: ToastOptions) => void {
  return useToastStore((s) => s.show);
}

const PREFIX: Record<ToastOptions["kind"], { text: string; color: string } | null> = {
  success: { text: "✓ ", color: "var(--pr-green)" },
  error: { text: "× ", color: "var(--pr-warn)" },
  info: null,
};

/** ルートに 1 度だけ設置するトースト描画口。BulkActionBar 表示中は上へ退避可(barVisible)。 */
export function ToastViewport({ barVisible = false }: { barVisible?: boolean }) {
  const current = useToastStore((s) => s.current);
  const dismiss = useToastStore((s) => s.dismiss);
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
  }, []);

  useEffect(() => {
    if (!current) return;
    const timeout = current.action ? 6000 : 4000;
    const t = window.setTimeout(dismiss, timeout);
    return () => {
      window.clearTimeout(t);
    };
  }, [current, dismiss]);

  if (!mounted || !current) return null;

  const prefix = PREFIX[current.kind];
  const style: CSSProperties = {
    position: "fixed",
    bottom: barVisible ? 74 : 22,
    left: "50%",
    transform: "translateX(-50%)",
    zIndex: Z_INDEX.toast,
    background: "var(--pr-elev-bg)",
    color: "var(--pr-elev-fg)",
    borderRadius: 10,
    padding: "10px 18px",
    boxShadow: "var(--pr-shadow-bar)",
    fontSize: 12,
    display: "inline-flex",
    alignItems: "center",
  };

  return createPortal(
    <div role="status" aria-live="polite" style={style}>
      <span>
        {prefix ? <span style={{ color: prefix.color }}>{prefix.text}</span> : null}
        {current.message}
      </span>
      {current.action ? (
        <button
          type="button"
          onClick={() => {
            current.action?.onClick();
            dismiss();
          }}
          style={{
            marginLeft: 14,
            color: "var(--pr-elev-fg)",
            fontWeight: 600,
            background: "transparent",
            border: "none",
            cursor: "pointer",
            fontSize: 12,
            fontFamily: "inherit",
          }}
        >
          {current.action.label}
        </button>
      ) : null}
    </div>,
    document.body,
  );
}
