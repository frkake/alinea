"use client";

import { useRef, useState, type CSSProperties } from "react";
import { STATUS_COLORS, STATUS_LABELS, type ReadingStatus } from "@alinea/tokens";
import { cn } from "@/lib/cn";
import { Popover } from "@/components/ui/Popover";

/**
 * ステータスピル(plans/08 §5.2)。
 * NOTE: 型 ReadingStatus は本来 @alinea/api-client を正とするが(§5.2)、
 * 当パッケージ未実装のため単一ソースの @alinea/tokens から取得する(後続で差し替え)。
 */
export interface StatusPillProps {
  status: ReadingStatus;
  size?: "md" | "sm";
  variant?: "pill" | "dot-label";
  interactive?: boolean;
  onChange?: (next: ReadingStatus) => void;
  className?: string;
}

const STATUS_ORDER: readonly ReadingStatus[] = [
  "planned",
  "up_next",
  "reading",
  "done",
  "reread",
  "on_hold",
];

export function StatusPill({
  status,
  size = "md",
  variant = "pill",
  interactive = false,
  onChange,
  className,
}: StatusPillProps) {
  const anchorRef = useRef<HTMLButtonElement>(null);
  const [open, setOpen] = useState(false);
  const dotSize = size === "sm" ? 6 : 7;
  const label = STATUS_LABELS[status];
  const dotColor = STATUS_COLORS[status];

  const dot = (
    <span
      style={{
        width: dotSize,
        height: dotSize,
        borderRadius: "50%",
        background: dotColor,
        flex: "none",
      }}
    />
  );

  if (variant === "dot-label") {
    return (
      <span
        className={cn(className)}
        style={{ display: "inline-flex", alignItems: "center", gap: 5, fontSize: 11 }}
      >
        {dot}
        <span>{label}</span>
      </span>
    );
  }

  const dims =
    size === "sm"
      ? { height: 20, padding: "0 8px", fontSize: 10 }
      : { height: 24, padding: "0 9px", fontSize: 11.5 };

  const style: CSSProperties = {
    display: "inline-flex",
    alignItems: "center",
    gap: 5,
    height: dims.height,
    padding: dims.padding,
    fontSize: dims.fontSize,
    fontWeight: 500,
    border: "1px solid var(--pr-border-control)",
    borderRadius: 999,
    background: "var(--pr-bg-control)",
    color: "var(--pr-text-mid)",
    cursor: interactive ? "pointer" : "default",
    fontFamily: "inherit",
  };

  const pill = (
    <button
      ref={anchorRef}
      type="button"
      className={cn(className)}
      style={style}
      disabled={!interactive}
      aria-haspopup={interactive ? "menu" : undefined}
      aria-expanded={interactive ? open : undefined}
      onClick={
        interactive
          ? () => {
              setOpen((v) => !v);
            }
          : undefined
      }
    >
      {dot}
      <span>{label}</span>
      {interactive ? (
        <span style={{ color: "var(--pr-text-muted)", fontSize: 9 }}>▾</span>
      ) : null}
    </button>
  );

  if (!interactive) return pill;

  return (
    <>
      {pill}
      <Popover
        open={open}
        onClose={() => {
          setOpen(false);
        }}
        anchorRef={anchorRef}
        width={180}
        placement="bottom-start"
        caret={false}
      >
        <div role="menu" style={{ padding: 4 }}>
          {STATUS_ORDER.map((s) => (
            <button
              key={s}
              type="button"
              role="menuitemradio"
              aria-checked={s === status}
              onClick={() => {
                onChange?.(s);
                setOpen(false);
              }}
              style={{
                display: "flex",
                alignItems: "center",
                gap: 6,
                width: "100%",
                padding: "6px 8px",
                border: "none",
                borderRadius: 6,
                background: s === status ? "var(--pr-acc-s)" : "transparent",
                color: s === status ? "var(--pr-acc)" : "var(--pr-text-mid)",
                fontSize: 11.5,
                fontWeight: s === status ? 600 : 400,
                cursor: "pointer",
                fontFamily: "inherit",
                textAlign: "left",
              }}
            >
              <span
                style={{
                  width: 7,
                  height: 7,
                  borderRadius: "50%",
                  background: STATUS_COLORS[s],
                  flex: "none",
                }}
              />
              {STATUS_LABELS[s]}
            </button>
          ))}
        </div>
      </Popover>
    </>
  );
}
