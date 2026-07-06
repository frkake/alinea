import type { ReactNode } from "react";

/** 認証画面(ログイン)の最小レイアウト。アプリのヘッダ・サイドバーは出さない。 */
export default function AuthLayout({ children }: { children: ReactNode }): ReactNode {
  return (
    <div
      style={{
        minHeight: "100vh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background: "var(--pr-bg-canvas)",
        color: "var(--pr-text)",
        fontFamily: "var(--pr-font-ui)",
        padding: 24,
      }}
    >
      {children}
    </div>
  );
}
