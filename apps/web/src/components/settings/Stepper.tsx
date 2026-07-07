"use client";

/** 画面固有ステッパー(4f §4.7.5)。display カテゴリの数値設定に使用。 */
export interface StepperProps {
  value: number;
  min: number;
  max: number;
  step: number;
  onChange: (next: number) => void;
  formatValue?: (value: number) => string;
  ariaLabel: string;
}

const buttonStyle = {
  width: 22,
  height: 22,
  border: "1px solid var(--pr-border-control)",
  borderRadius: 6,
  fontSize: 12,
  color: "var(--pr-text-mid)",
  background: "var(--pr-bg-control)",
  cursor: "pointer",
  fontFamily: "inherit" as const,
  display: "inline-flex",
  alignItems: "center",
  justifyContent: "center",
  padding: 0,
};

/** 浮動小数の丸め誤差を吸収する(0.1px 単位で四捨五入)。 */
function round(v: number): number {
  return Math.round(v * 100) / 100;
}

export function Stepper({ value, min, max, step, onChange, formatValue, ariaLabel }: StepperProps) {
  const atMin = value <= min;
  const atMax = value >= max;
  const label = formatValue ? formatValue(value) : String(value);

  return (
    <div style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
      <button
        type="button"
        aria-label={`${ariaLabel}を減らす`}
        disabled={atMin}
        onClick={() => {
          onChange(round(Math.max(min, value - step)));
        }}
        style={{ ...buttonStyle, opacity: atMin ? 0.4 : 1, cursor: atMin ? "not-allowed" : "pointer" }}
      >
        −
      </button>
      <span
        role="status"
        aria-label={ariaLabel}
        style={{ width: 52, textAlign: "center", fontSize: 11.5 }}
      >
        {label}
      </span>
      <button
        type="button"
        aria-label={`${ariaLabel}を増やす`}
        disabled={atMax}
        onClick={() => {
          onChange(round(Math.min(max, value + step)));
        }}
        style={{ ...buttonStyle, opacity: atMax ? 0.4 : 1, cursor: atMax ? "not-allowed" : "pointer" }}
      >
        +
      </button>
    </div>
  );
}
