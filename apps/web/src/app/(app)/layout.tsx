"use client";

import { useState, type ReactNode } from "react";
import { AppHeader } from "@/components/AppHeader";
import { AppNav } from "@/components/AppNav";
import { ToastViewport } from "@/components/ui/Toast";
import { Drawer } from "@/components/ui/Drawer";
import { useIsMobile } from "@/hooks/useMediaQuery";

/**
 * 認証必須領域(dashboard/library/collections/vocab/search/settings/papers)の共通シェル。
 * モバイル縮退(mobile.md §5.1): サイドバー(AppNav)を非描画にし、トップバーのハンバーガーから
 * 開く左ドロワーへ差し替える(閲覧に伴うナビゲーションのため許可)。
 */
export default function AppLayout({ children }: { children: ReactNode }): ReactNode {
  const isMobile = useIsMobile();
  const [navOpen, setNavOpen] = useState(false);

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
      <AppHeader onMenuClick={isMobile ? () => setNavOpen(true) : undefined} />
      <div style={{ flex: 1, display: "flex", minHeight: 0 }}>
        {isMobile ? (
          <Drawer open={navOpen} onClose={() => setNavOpen(false)} width={280} ariaLabel="ナビゲーション">
            <AppNav onNavigate={() => setNavOpen(false)} />
          </Drawer>
        ) : (
          <AppNav />
        )}
        <main style={{ flex: 1, minWidth: 0, overflow: "auto" }}>{children}</main>
      </div>
      <ToastViewport />
    </div>
  );
}
