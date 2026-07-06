"use client";

import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import {
  ACCENT_COOKIE,
  DEFAULT_ACCENT,
  DEFAULT_BODY_FONT,
  DEFAULT_THEME,
  FONT_COOKIE,
  THEME_COOKIE,
  type AccentKey,
  type BodyFont,
  type ThemePref,
} from "@/lib/theme";

interface ThemeState {
  theme: ThemePref;
  accent: AccentKey;
  bodyFont: BodyFont;
  setTheme: (theme: ThemePref) => void;
  setAccent: (accent: AccentKey) => void;
  setBodyFont: (font: BodyFont) => void;
}

const ThemeContext = createContext<ThemeState | null>(null);

function writeCookie(name: string, value: string): void {
  if (typeof document === "undefined") return;
  const oneYear = 60 * 60 * 24 * 365;
  document.cookie = `${name}=${value}; path=/; max-age=${oneYear}; SameSite=Lax`;
}

function applyThemeAttr(theme: ThemePref): void {
  if (typeof document === "undefined") return;
  const root = document.documentElement;
  const resolved =
    theme === "system"
      ? window.matchMedia("(prefers-color-scheme: dark)").matches
        ? "dark"
        : "light"
      : theme;
  root.setAttribute("data-theme", resolved);
}

export interface ThemeProviderProps {
  initialTheme?: ThemePref;
  initialAccent?: AccentKey;
  initialBodyFont?: BodyFont;
  children: ReactNode;
}

export function ThemeProvider({
  initialTheme = DEFAULT_THEME,
  initialAccent = DEFAULT_ACCENT,
  initialBodyFont = DEFAULT_BODY_FONT,
  children,
}: ThemeProviderProps): ReactNode {
  const [theme, setThemeState] = useState<ThemePref>(initialTheme);
  const [accent, setAccentState] = useState<AccentKey>(initialAccent);
  const [bodyFont, setBodyFontState] = useState<BodyFont>(initialBodyFont);

  const setTheme = useCallback((next: ThemePref) => {
    setThemeState(next);
    writeCookie(THEME_COOKIE, next);
    applyThemeAttr(next);
  }, []);

  const setAccent = useCallback((next: AccentKey) => {
    setAccentState(next);
    writeCookie(ACCENT_COOKIE, next);
    if (typeof document !== "undefined") {
      document.documentElement.setAttribute("data-accent", next);
    }
  }, []);

  const setBodyFont = useCallback((next: BodyFont) => {
    setBodyFontState(next);
    writeCookie(FONT_COOKIE, next);
    if (typeof document !== "undefined") {
      document.documentElement.setAttribute("data-body-font", next);
    }
  }, []);

  const value = useMemo<ThemeState>(
    () => ({ theme, accent, bodyFont, setTheme, setAccent, setBodyFont }),
    [theme, accent, bodyFont, setTheme, setAccent, setBodyFont],
  );

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme(): ThemeState {
  const ctx = useContext(ThemeContext);
  if (!ctx) {
    throw new Error("useTheme は ThemeProvider の内側で使ってください");
  }
  return ctx;
}
