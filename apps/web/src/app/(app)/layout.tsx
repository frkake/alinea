import type { ReactNode } from "react";
import { AppHeader } from "@/components/AppHeader";
import { AppNav } from "@/components/AppNav";
import { ToastViewport } from "@/components/ui/Toast";

/** 認証必須領域(dashboard/library/collections/vocab/search/settings/papers)の共通シェル。 */
export default function AppLayout({ children }: { children: ReactNode }): ReactNode {
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        height: "100vh",
        background: "var(--pr-bg-app-alt)",
        color: "var(--pr-text)",
        fontFamily: "var(--pr-font-ui)",
      }}
    >
      <AppHeader />
      <div style={{ flex: 1, display: "flex", minHeight: 0 }}>
        <AppNav />
        <main style={{ flex: 1, minWidth: 0, overflow: "auto" }}>{children}</main>
      </div>
      <ToastViewport />
    </div>
  );
}
