"use client";

import { useRef, type CSSProperties, type KeyboardEvent } from "react";
import { cn } from "@/lib/cn";

/** セグメンテッドコントロール(plans/08 §5.1)。 */
export interface SegmentedControlProps<T extends string> {
  options: ReadonlyArray<{ value: T; label: string }>;
  value: T;
  onChange: (value: T) => void;
  size?: "sm" | "md" | "lg";
  ariaLabel: string;
  className?: string;
}

const SEG_SIZE: Record<"sm" | "md" | "lg", { height: number; padding: string; fontSize: number }> =
  {
    sm: { height: 22, padding: "0 10px", fontSize: 11 },
    md: { height: 24, padding: "0 11px", fontSize: 11.5 },
    lg: { height: 26, padding: "0 14px", fontSize: 11.5 },
  };

export function SegmentedControl<T extends string>({
  options,
  value,
  onChange,
  size = "md",
  ariaLabel,
  className,
}: SegmentedControlProps<T>) {
  const dims = SEG_SIZE[size];
  const refs = useRef<Array<HTMLButtonElement | null>>([]);

  const move = (from: number, delta: number) => {
    const count = options.length;
    const next = (from + delta + count) % count;
    const opt = options[next];
    if (!opt) return;
    onChange(opt.value);
    refs.current[next]?.focus();
  };

  const onKeyDown = (e: KeyboardEvent<HTMLButtonElement>, index: number) => {
    if (e.key === "ArrowRight" || e.key === "ArrowDown") {
      e.preventDefault();
      move(index, 1);
    } else if (e.key === "ArrowLeft" || e.key === "ArrowUp") {
      e.preventDefault();
      move(index, -1);
    }
  };

  return (
    <div
      role="radiogroup"
      aria-label={ariaLabel}
      className={cn(className)}
      style={{
        display: "inline-flex",
        gap: 2,
        padding: 2,
        borderRadius: 7,
        background: "var(--pr-bg-muted)",
      }}
    >
      {options.map((opt, index) => {
        const selected = opt.value === value;
        const style: CSSProperties = {
          display: "inline-flex",
          alignItems: "center",
          justifyContent: "center",
          height: dims.height,
          padding: dims.padding,
          fontSize: dims.fontSize,
          borderRadius: 5,
          border: "none",
          cursor: "pointer",
          background: selected ? "var(--pr-bg-seg-selected)" : "transparent",
          color: selected ? "var(--pr-text)" : "var(--pr-text-sub)",
          fontWeight: selected ? 600 : 400,
          boxShadow: selected ? "var(--pr-shadow-seg)" : undefined,
          fontFamily: "inherit",
        };
        return (
          <button
            key={opt.value}
            ref={(el) => {
              refs.current[index] = el;
            }}
            type="button"
            role="radio"
            aria-checked={selected}
            tabIndex={selected ? 0 : -1}
            className={selected ? "yk-seg-selected" : undefined}
            style={style}
            onClick={() => {
              onChange(opt.value);
            }}
            onKeyDown={(e) => {
              onKeyDown(e, index);
            }}
          >
            {opt.label}
          </button>
        );
      })}
    </div>
  );
}
