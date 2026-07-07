"use client";

import { SegmentedControl } from "@/components/ui/SegmentedControl";
import { useTheme } from "@/components/ThemeProvider";
import type { ThemePref } from "@/lib/theme";

/** テーマ切替(plans/08 §8)。`data-theme` は ThemeProvider が <html> に適用(FOUC 防止は layout 済み)。 */
const THEME_OPTIONS = [
  { value: "light", label: "ライト" },
  { value: "dark", label: "ダーク" },
  { value: "system", label: "システム" },
] as const satisfies ReadonlyArray<{ value: ThemePref; label: string }>;

export interface ThemeToggleProps {
  size?: "sm" | "md" | "lg";
}

/**
 * ライト/ダーク/システムの切替コントロール(1c §5.1・plans/08 §8)。
 * 設定画面(4f)から使う想定。ビューアヘッダには置かない(1c §5.1 決定)。
 */
export function ThemeToggle({ size = "md" }: ThemeToggleProps) {
  const { theme, setTheme } = useTheme();
  return (
    <SegmentedControl
      options={THEME_OPTIONS}
      value={theme}
      onChange={(v) => setTheme(v)}
      size={size}
      ariaLabel="テーマ"
    />
  );
}
