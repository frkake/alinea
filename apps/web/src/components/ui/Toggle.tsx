"use client";

import type { CSSProperties } from "react";

/** トグルスイッチ(plans/08 §5.8)。 */
export interface ToggleProps {
  checked: boolean;
  onChange: (next: boolean) => void;
  disabled?: boolean;
  ariaLabel: string;
}

export function Toggle({ checked, onChange, disabled = false, ariaLabel }: ToggleProps) {
  const trackStyle: CSSProperties = {
    position: "relative",
    display: "inline-block",
    width: 30,
    height: 17,
    borderRadius: 9,
    border: "none",
    padding: 0,
    cursor: disabled ? "default" : "pointer",
    background: checked ? "var(--pr-acc)" : "var(--pr-border-check)",
    transition: "background 120ms ease-out",
    opacity: disabled ? 0.5 : 1,
    pointerEvents: disabled ? "none" : "auto",
  };

  const knobStyle: CSSProperties = {
    position: "absolute",
    top: 2,
    left: checked ? undefined : 2,
    right: checked ? 2 : undefined,
    width: 13,
    height: 13,
    borderRadius: "50%",
    background: "#FFFFFF",
    transition: "left 120ms ease-out, right 120ms ease-out",
  };

  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={ariaLabel}
      disabled={disabled}
      style={trackStyle}
      onClick={() => {
        onChange(!checked);
      }}
    >
      <span style={knobStyle} />
    </button>
  );
}
