"use client";

import { useState, type CSSProperties } from "react";
import { cn } from "@/lib/cn";
import { MagnifierIcon } from "@/components/icons";
import { Keycap } from "@/components/ui/Keycap";

/** 検索ボックス(plans/08 §5.13)。ショートカット登録は呼び出し側が行い、本体は表示のみ。 */
export interface SearchBoxProps {
  variant: "global" | "in-paper";
  value: string;
  onChange: (v: string) => void;
  onFocusChange?: (focused: boolean) => void;
  placeholder: string;
  shortcutLabel: "⌘K" | "/";
  className?: string;
}

export function SearchBox({
  variant,
  value,
  onChange,
  onFocusChange,
  placeholder,
  shortcutLabel,
  className,
}: SearchBoxProps) {
  const [focused, setFocused] = useState(false);
  const isGlobal = variant === "global";

  const containerStyle: CSSProperties = isGlobal
    ? {
        display: "flex",
        alignItems: "center",
        gap: 8,
        width: 460,
        height: 32,
        borderRadius: 7,
        padding: "0 12px",
        fontSize: 12,
        background: focused ? "var(--pr-bg-card)" : "var(--pr-bg-inset)",
        color: focused ? "var(--pr-text)" : "var(--pr-text-icon)",
        border: focused ? "1.5px solid var(--pr-acc)" : "1.5px solid transparent",
        boxShadow: focused ? "0 0 0 3px var(--pr-acc-s)" : undefined,
      }
    : {
        display: "flex",
        alignItems: "center",
        gap: 6,
        width: 150,
        maxWidth: "100%",
        height: 26,
        borderRadius: 6,
        padding: "0 10px",
        fontSize: 11.5,
        background: "var(--pr-bg-inset)",
        color: "var(--pr-text-icon)",
        border: focused ? "1.5px solid var(--pr-acc)" : "1.5px solid transparent",
      };

  return (
    <div className={cn(className)} style={containerStyle}>
      <MagnifierIcon size={isGlobal ? 12 : 11} style={{ flex: "none" }} />
      <input
        type="search"
        value={value}
        placeholder={placeholder}
        aria-label={placeholder}
        onChange={(e) => {
          onChange(e.target.value);
        }}
        onFocus={() => {
          setFocused(true);
          onFocusChange?.(true);
        }}
        onBlur={() => {
          setFocused(false);
          onFocusChange?.(false);
        }}
        style={{
          flex: 1,
          minWidth: 0,
          border: "none",
          outline: "none",
          background: "transparent",
          color: "inherit",
          fontSize: "inherit",
          fontFamily: "inherit",
        }}
      />
      {isGlobal && focused ? (
        <span style={{ fontSize: 10, color: "var(--pr-text-muted)", flex: "none" }}>
          esc で閉じる
        </span>
      ) : (
        <span style={{ marginLeft: "auto", flex: "none" }}>
          <Keycap mono={shortcutLabel === "⌘K"}>{shortcutLabel}</Keycap>
        </span>
      )}
    </div>
  );
}
