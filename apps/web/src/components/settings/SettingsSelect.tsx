"use client";

import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { Popover } from "@/components/ui/Popover";

/** 画面固有セレクト(4f §4.7.1)。button + Popover(listbox)の合成。 */
export interface SettingsSelectProps<T extends string> {
  options: ReadonlyArray<{ value: T; label: string }>;
  value: T;
  onChange: (value: T) => void;
  width?: number;
  ariaLabel: string;
}

export function SettingsSelect<T extends string>({
  options,
  value,
  onChange,
  width = 160,
  ariaLabel,
}: SettingsSelectProps<T>) {
  const anchorRef = useRef<HTMLButtonElement>(null);
  const optionRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const [open, setOpen] = useState(false);
  const selectedIndex = Math.max(
    0,
    options.findIndex((o) => o.value === value),
  );
  const current = options.find((o) => o.value === value);

  useEffect(() => {
    if (open) optionRefs.current[selectedIndex]?.focus();
  }, [open, selectedIndex]);

  const commit = (v: T) => {
    onChange(v);
    setOpen(false);
    anchorRef.current?.focus();
  };

  const onListKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const active = optionRefs.current.findIndex((el) => el === document.activeElement);
    if (e.key === "ArrowDown") {
      e.preventDefault();
      optionRefs.current[Math.min(options.length - 1, active + 1)]?.focus();
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      optionRefs.current[Math.max(0, active - 1)]?.focus();
    }
  };

  return (
    <>
      <button
        ref={anchorRef}
        type="button"
        aria-label={ariaLabel}
        aria-haspopup="listbox"
        aria-expanded={open}
        onClick={() => {
          setOpen((v) => !v);
        }}
        style={{
          position: "relative",
          display: "inline-flex",
          alignItems: "center",
          height: 28,
          width,
          padding: "0 24px 0 10px",
          border: "1px solid var(--pr-border-control)",
          borderRadius: 6,
          background: "var(--pr-bg-control)",
          color: "var(--pr-text)",
          fontSize: 11.5,
          fontFamily: "inherit",
          cursor: "pointer",
          textAlign: "left",
        }}
      >
        <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {current?.label ?? value}
        </span>
        <span
          aria-hidden="true"
          style={{ position: "absolute", right: 10, fontSize: 9, color: "var(--pr-text-muted)" }}
        >
          ▾
        </span>
      </button>
      <Popover
        open={open}
        onClose={() => {
          setOpen(false);
        }}
        anchorRef={anchorRef}
        width={width}
        placement="bottom-start"
        caret={false}
      >
        <div role="listbox" aria-label={ariaLabel} onKeyDown={onListKeyDown} style={{ padding: 4 }}>
          {options.map((opt, index) => {
            const selected = opt.value === value;
            return (
              <button
                key={opt.value}
                ref={(el) => {
                  optionRefs.current[index] = el;
                }}
                type="button"
                role="option"
                aria-selected={selected}
                onClick={() => {
                  commit(opt.value);
                }}
                style={{
                  display: "flex",
                  alignItems: "center",
                  width: "100%",
                  height: 28,
                  padding: "0 10px",
                  border: "none",
                  borderRadius: 6,
                  background: selected ? "var(--pr-acc-s)" : "transparent",
                  color: selected ? "var(--pr-acc)" : "var(--pr-text-mid)",
                  fontSize: 11.5,
                  fontWeight: selected ? 600 : 400,
                  cursor: "pointer",
                  fontFamily: "inherit",
                  textAlign: "left",
                }}
              >
                {opt.label}
              </button>
            );
          })}
        </div>
      </Popover>
    </>
  );
}
