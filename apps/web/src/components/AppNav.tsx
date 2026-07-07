"use client";

import { usePathname } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { vocabList } from "@yakudoku/api-client";
import { SidebarNav, type SidebarNavItem } from "@/components/ui/SidebarNav";
import { vocabCountsQueryKey } from "@/components/vocab/queryKeys";
import { useSavedFilterSection } from "@/components/library/SavedFilterList";

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
 * 「語彙帳」バッジ = 総語数(`counts.all`。plans/09-screens/4d §2.1・docs/11 受け入れ基準)。
 * `/vocab` 画面本体が同一キーへ `setQueryData` するため(4d §2.1 決定)、他画面滞在時のみ
 * このクエリが実際に発火する。
 */
export function AppNav({ onNavigate }: AppNavProps = {}) {
  const pathname = usePathname();

  const vocabCountsQuery = useQuery({
    queryKey: vocabCountsQueryKey,
    queryFn: async () => (await vocabList({ query: { limit: 1 }, throwOnError: true })).data,
    staleTime: 60_000,
  });

  // 保存フィルタ節(1e §4.4・plans/03 §5.14)。件数付き・0 件時は非表示(useSavedFilterSection)。
  const savedFilterSection = useSavedFilterSection();

  const main: SidebarNavItem[] = [
    { id: "home", label: "ホーム", href: "/dashboard" },
    { id: "library", label: "ライブラリ", href: "/library" },
    { id: "vocab", label: "語彙帳", href: "/vocab", count: vocabCountsQuery.data?.counts.all },
  ].map((item) => ({
    ...item,
    active: pathname === item.href || pathname.startsWith(`${item.href}/`),
  }));

  return (
    <div onClick={onNavigate}>
      <SidebarNav
        main={main}
        sections={savedFilterSection ? [savedFilterSection] : []}
        footer={<span>設定 · エクスポート</span>}
      />
    </div>
  );
}
