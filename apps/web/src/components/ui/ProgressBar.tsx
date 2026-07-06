/** プログレスバー(plans/08 §5.12)。 */
export interface ProgressBarProps {
  value: number;
  color?: "accent" | "green";
  height?: 3 | 4;
  className?: string;
}

export function ProgressBar({ value, color = "accent", height = 3, className }: ProgressBarProps) {
  const clamped = Math.max(0, Math.min(100, value));
  return (
    <div
      className={className}
      role="progressbar"
      aria-valuenow={Math.round(clamped)}
      aria-valuemin={0}
      aria-valuemax={100}
      style={{
        height,
        borderRadius: 2,
        background: "var(--pr-border-soft)",
        overflow: "hidden",
        width: "100%",
      }}
    >
      <div
        style={{
          height: "100%",
          width: `${clamped}%`,
          borderRadius: 2,
          background: color === "green" ? "var(--pr-green)" : "var(--pr-acc)",
        }}
      />
    </div>
  );
}
