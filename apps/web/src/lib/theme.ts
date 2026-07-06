/** テーマ状態(plans/08 §8)。<html> の 3 属性で全状態を表す。 */

export type ThemePref = "light" | "dark" | "system";
export type ResolvedTheme = "light" | "dark";
export type AccentKey = "slate" | "green" | "purple" | "terracotta";
export type BodyFont = "serif" | "sans";

export const THEME_COOKIE = "yk_theme";
export const ACCENT_COOKIE = "yk_accent";
export const FONT_COOKIE = "yk_font";

export const DEFAULT_THEME: ThemePref = "system";
export const DEFAULT_ACCENT: AccentKey = "slate";
export const DEFAULT_BODY_FONT: BodyFont = "serif";

export const ACCENT_KEYS: readonly AccentKey[] = ["slate", "green", "purple", "terracotta"];

export function isThemePref(v: string | undefined): v is ThemePref {
  return v === "light" || v === "dark" || v === "system";
}

export function isAccentKey(v: string | undefined): v is AccentKey {
  return v === "slate" || v === "green" || v === "purple" || v === "terracotta";
}

export function isBodyFont(v: string | undefined): v is BodyFont {
  return v === "serif" || v === "sans";
}

/** SSR 用の初期解決。system は light を初期値とし、<head> のインラインスクリプトが上書きする。 */
export function resolveThemeForSSR(pref: ThemePref): ResolvedTheme {
  return pref === "dark" ? "dark" : "light";
}
