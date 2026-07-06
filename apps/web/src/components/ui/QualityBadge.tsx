import type { CSSProperties } from "react";

/** 品質バッジ(plans/08 §5.3)。 */
export interface QualityBadgeProps {
  level: "A" | "B";
  size?: 18 | 17;
}

const TITLES: Record<"A" | "B", string> = {
  A: "品質レベルA: LaTeXソースから完全構造化",
  B: "品質レベルB: PDF由来",
};

export function QualityBadge({ level, size = 18 }: QualityBadgeProps) {
  const style: CSSProperties = {
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
    width: size,
    height: size,
    borderRadius: 4,
    fontSize: size === 17 ? 10 : 10.5,
    fontWeight: 700,
    background: level === "A" ? "var(--pr-acc-s)" : "var(--pr-bg-inset)",
    color: level === "A" ? "var(--pr-acc)" : "var(--pr-text-sub2)",
  };
  return (
    <span style={style} title={TITLES[level]}>
      {level}
    </span>
  );
}
