"use client";

import { useQuery } from "@tanstack/react-query";
import { savedFiltersList } from "@alinea/api-client";
import { SidebarNav, type SidebarNavItem } from "@/components/ui/SidebarNav";

/** サイドバー「保存フィルタ」節のクエリキー(AppNav・本コンポーネント間で共有)。 */
export const savedFiltersQueryKey = ["saved-filters"] as const;

/**
 * サイドバー「保存フィルタ」節(1e §4.4、plans/03 §5.14)。
 * `SidebarNav`(plans/08 §5.14)の `sections` 形状で返す — `AppNav` はこれを
 * `sections` 配列に追加するだけでよい(所有範囲: AppNav.tsx への追加のみ)。
 * 0 件時は節自体を出さない(見出しだけの空表示を避ける。§4.4 の意図に沿う)。
 */
export function useSavedFilterSection(): { heading: string; items: SidebarNavItem[] } | null {
  const query = useQuery({
    queryKey: savedFiltersQueryKey,
    queryFn: async () => (await savedFiltersList({ throwOnError: true })).data,
    staleTime: 60_000,
  });
  const items = query.data?.items ?? [];
  if (items.length === 0) return null;
  return {
    heading: "保存フィルタ",
    items: items.map((sf) => ({
      id: sf.id,
      label: sf.name,
      href: `/library?filter_id=${sf.id}`,
      count: sf.count,
    })),
  };
}

/**
 * 単体描画版(Storybook・テスト用。実運用では `useSavedFilterSection()` を `AppNav` の
 * `sections` に足すだけで並び順どおりに表示される)。`SidebarNav` をそのまま使い、
 * 見た目(見出し・行スタイル)を重複実装しない。
 */
export function SavedFilterList() {
  const section = useSavedFilterSection();
  return <SidebarNav main={[]} sections={section ? [section] : []} />;
}
