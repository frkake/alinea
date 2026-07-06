import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

/** キーキャップ(plans/08 §5.22)。⌘K・「t で開閉」等。 */
export interface KeycapProps {
  children: ReactNode;
  mono?: boolean;
  className?: string;
}

export function Keycap({ children, mono = false, className }: KeycapProps) {
  return (
    <span
      className={cn(className)}
      style={{
        display: "inline-flex",
        alignItems: "center",
        border: "1px solid var(--pr-border-keycap)",
        borderRadius: 3,
        padding: "0 4px",
        fontSize: 9.5,
        background: "var(--pr-bg-control)",
        color: "var(--pr-text-sub)",
        fontFamily: mono ? "var(--pr-font-mono)" : undefined,
      }}
    >
      {children}
    </span>
  );
}
