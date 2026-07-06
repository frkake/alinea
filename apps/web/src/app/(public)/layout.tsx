import type { Metadata } from "next";
import type { ReactNode } from "react";

/** 公開領域(共有ページ 4c 等)。アプリの認証チローム無し・noindex。 */
export const metadata: Metadata = {
  robots: { index: false, follow: false },
};

export default function PublicLayout({ children }: { children: ReactNode }): ReactNode {
  return (
    <div
      style={{
        minHeight: "100vh",
        background: "var(--pr-bg-canvas)",
        color: "var(--pr-text)",
        fontFamily: "var(--pr-font-ui)",
      }}
    >
      {children}
    </div>
  );
}
