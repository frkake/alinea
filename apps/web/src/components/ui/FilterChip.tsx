"use client";

import type { CSSProperties, MouseEvent } from "react";
import { cn } from "@/lib/cn";

/** フィルタチップ(plans/08 §5.6)。 */
export interface FilterChipProps {
  label: string;
  count?: number;
  selected?: boolean;
  /** 注釈色等。CSS 変数文字列可(先頭 7×7px 円)。 */
  dotColor?: string;
  /** true でアクセント「適用中(解除可能)」スタイル + 末尾「×」。 */
  removable?: boolean;
  size?: "md" | "sm";
  onClick?: () => void;
  onRemove?: () => void;
  className?: string;
}

export function FilterChip({
  label,
  count,
  selected = false,
  dotColor,
  removable = false,
  size = "md",
  onClick,
  onRemove,
  className,
}: FilterChipProps) {
  const dims =
    size === "sm"
      ? { height: 20, padding: "0 8px", fontSize: 10.5 }
      : { height: 22, padding: "0 10px", fontSize: 11 };

  const style: CSSProperties = {
    display: "inline-flex",
    alignItems: "center",
    gap: dotColor ? 4 : 5,
    height: dims.height,
    padding: dims.padding,
    fontSize: dims.fontSize,
    borderRadius: 999,
    cursor: "pointer",
    fontFamily: "inherit",
  };

  if (removable) {
    Object.assign(style, {
      border: "1px solid var(--pr-acc-m)",
      color: "var(--pr-acc)",
      background: "var(--pr-acc-s)",
      fontWeight: 600,
    });
  } else if (selected) {
    Object.assign(style, {
      border: "none",
      color: "#FFFFFF",
      background: "var(--pr-elev-bg)",
      fontWeight: 600,
    });
  } else {
    Object.assign(style, {
      border: "1px solid var(--pr-border-control)",
      color: "var(--pr-text-mid)",
      background: "var(--pr-bg-control)",
      fontWeight: 400,
    });
  }

  const handleRemove = (e: MouseEvent<HTMLSpanElement>) => {
    e.stopPropagation();
    onRemove?.();
  };

  return (
    <button type="button" className={cn(className)} style={style} onClick={onClick}>
      {dotColor ? (
        <span
          style={{
            width: 7,
            height: 7,
            borderRadius: "50%",
            background: dotColor,
            flex: "none",
          }}
        />
      ) : null}
      <span>{label}</span>
      {typeof count === "number" ? (
        <span style={{ opacity: 0.7 }}>{count}</span>
      ) : null}
      {removable ? (
        <span
          role="button"
          aria-label={`${label} を解除`}
          tabIndex={0}
          onClick={handleRemove}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === " ") {
              e.preventDefault();
              e.stopPropagation();
              onRemove?.();
            }
          }}
          style={{ marginLeft: 2, cursor: "pointer" }}
        >
          ×
        </span>
      ) : null}
    </button>
  );
}
