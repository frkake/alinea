import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

/** タグチップ(plans/08 §5.22)。1e/4a/3a。 */
export interface TagChipProps {
  children: ReactNode;
  /** カード内は 9.5px、既定は 10px。 */
  size?: "default" | "card";
  className?: string;
}

export function TagChip({ children, size = "default", className }: TagChipProps) {
  return (
    <span
      className={cn(className)}
      style={{
        display: "inline-flex",
        alignItems: "center",
        height: 17,
        padding: "0 6px",
        borderRadius: 3,
        background: "var(--pr-bg-inset)",
        color: "var(--pr-text-sub)",
        fontSize: size === "card" ? 9.5 : 10,
      }}
    >
      {children}
    </span>
  );
}
