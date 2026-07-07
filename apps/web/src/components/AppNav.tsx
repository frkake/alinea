"use client";

import { usePathname } from "next/navigation";
import { SidebarNav, type SidebarNavItem } from "@/components/ui/SidebarNav";

export interface AppNavProps {
  /**
   * モバイル縮退のナビドロワー(mobile.md §5.1)から使う場合、項目クリックでドロワーを
   * 閉じるためのコールバック。
   */
  onNavigate?: () => void;
}

/**
 * (app) セグメントのサイドバー容器。usePathname でアクティブ項目を解決し、
 * 汎用 SidebarNav(plans/08 §5.14)へ渡す。件数・コレクションは後続タスクで API 連携。
 */
export function AppNav({ onNavigate }: AppNavProps = {}) {
  const pathname = usePathname();

  const main: SidebarNavItem[] = [
    { id: "home", label: "ホーム", href: "/dashboard" },
    { id: "library", label: "ライブラリ", href: "/library" },
    { id: "vocab", label: "語彙帳", href: "/vocab" },
  ].map((item) => ({
    ...item,
    active: pathname === item.href || pathname.startsWith(`${item.href}/`),
  }));

  return (
    <div onClick={onNavigate}>
      <SidebarNav main={main} sections={[]} footer={<span>設定 · エクスポート</span>} />
    </div>
  );
}
