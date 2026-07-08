"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useEffect, useState, type ReactNode } from "react";
import { ThemeProvider } from "@/components/ThemeProvider";
import { registerAuthRedirect } from "@/lib/auth-redirect";
import type { AccentKey, BodyFont, ThemePref } from "@/lib/theme";

export interface ProvidersProps {
  theme: ThemePref;
  accent: AccentKey;
  bodyFont: BodyFont;
  children: ReactNode;
}

/**
 * クライアント境界: QueryClientProvider(TanStack Query)+ ThemeProvider。
 * QueryClient は 1 度だけ生成し、SSR/再描画で使い回す。
 */
export function Providers({ theme, accent, bodyFont, children }: ProvidersProps): ReactNode {
  // 401 → /login の一元リダイレクト(クライアント側でのみ登録)。
  useEffect(() => {
    registerAuthRedirect();
  }, []);

  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 30_000,
            refetchOnWindowFocus: false,
            retry: 1,
          },
        },
      }),
  );

  return (
    <QueryClientProvider client={queryClient}>
      <ThemeProvider initialTheme={theme} initialAccent={accent} initialBodyFont={bodyFont}>
        {children}
      </ThemeProvider>
    </QueryClientProvider>
  );
}
