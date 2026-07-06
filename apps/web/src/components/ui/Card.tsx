import type { HTMLAttributes, ReactNode } from "react";
import { cn } from "@/lib/cn";

/** 基本の白カード(plans/08 §5.9)。 */
export interface CardProps extends HTMLAttributes<HTMLElement> {
  padding?: "none" | "md";
  as?: "div" | "section" | "article";
  children?: ReactNode;
}

export function Card({
  padding = "none",
  as = "div",
  className,
  style,
  children,
  ...rest
}: CardProps) {
  const Tag = as;
  return (
    <Tag
      className={cn(className)}
      style={{
        background: "var(--pr-bg-card)",
        border: "1px solid var(--pr-border-card)",
        borderRadius: 10,
        overflow: "hidden",
        padding: padding === "md" ? "14px 18px" : undefined,
        ...style,
      }}
      {...rest}
    >
      {children}
    </Tag>
  );
}
