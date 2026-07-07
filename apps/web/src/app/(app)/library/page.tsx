"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { libraryItemsFacets, libraryItemsList, type LibraryItemSummary } from "@yakudoku/api-client";
import { EmptyState } from "@/components/ui/EmptyState";
import { ViewSwitch } from "@/components/library/ViewSwitch";
import { QuickFilterBar } from "@/components/library/QuickFilterBar";
import { LibraryTableView } from "@/components/library/LibraryTableView";
import { LibraryCard } from "@/components/library/LibraryCard";
import type { LibraryView, Quick, SortState } from "@/components/library/types";

/** サーバがソート可能なキー(plans/03 §5.1 / library_items._SORTS)。ステータス/品質はソート不可。 */
const VALID_SORT_KEYS = new Set<SortState["key"]>([
  "updated_at",
  "added_at",
  "title",
  "deadline",
  "priority",
  "reading_time",
  "comprehension",
]);

/**
 * ライブラリ画面(1e テーブル + 4a カード、M0 スコープ)。
 * - クイックフィルタ 5 種(件数=facets)・ビュー切替・基本ソート(テーブル列ヘッダ)。
 * - 属性フィルタ / 保存フィルタ / 一括操作 / 検索ドロップダウン / 通知は非表示(M1/M2)。
 */
export default function LibraryPage() {
  const router = useRouter();
  const [view, setView] = useState<LibraryView>("table");
  const [quick, setQuick] = useState<Quick>("all");
  const [sort, setSort] = useState<SortState>({ key: "updated_at", dir: "desc" });

  const facetsQuery = useQuery({
    queryKey: ["library", "facets"],
    queryFn: async () => (await libraryItemsFacets({ throwOnError: true })).data,
  });

  const listQuery = useQuery({
    queryKey: ["library", "list", { quick, sort }],
    queryFn: async () =>
      (
        await libraryItemsList({
          query: { quick, sort: sort.key, order: sort.dir, limit: 50 },
          throwOnError: true,
        })
      ).data,
  });

  const openReader = (id: string) => {
    router.push(`/papers/${id}`);
  };

  const onSortChange = (next: SortState) => {
    if (!VALID_SORT_KEYS.has(next.key)) return; // ステータス/品質列はソート不可
    setSort(next);
  };

  const items = listQuery.data?.items ?? [];
  const total = facetsQuery.data?.quick.all;

  return (
    <div
      style={{ display: "flex", flexDirection: "column", gap: 12, padding: "16px 22px", minHeight: 0 }}
    >
      {/* 見出し行 */}
      <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
        <h1 style={{ fontSize: 16, fontWeight: 700, margin: 0 }}>ライブラリ</h1>
        {total != null ? (
          <span style={{ fontSize: 11.5, color: "var(--pr-text-muted)" }}>{total} 本</span>
        ) : null}
        <span style={{ marginLeft: 6 }}>
          <ViewSwitch view={view} onViewChange={setView} />
        </span>
      </div>

      {/* クイックフィルタ */}
      <QuickFilterBar facets={facetsQuery.data?.quick} quick={quick} onQuickChange={setQuick} />

      {/* 本体 */}
      <LibraryBody
        view={view}
        loading={listQuery.isPending}
        error={listQuery.isError}
        onRetry={() => void listQuery.refetch()}
        items={items}
        quick={quick}
        totalAll={total}
        onClearFilters={() => {
          setQuick("all");
        }}
        sort={sort}
        onSortChange={onSortChange}
        onOpen={openReader}
      />
    </div>
  );
}

interface LibraryBodyProps {
  view: LibraryView;
  loading: boolean;
  error: boolean;
  onRetry: () => void;
  items: LibraryItemSummary[];
  quick: Quick;
  totalAll: number | undefined;
  onClearFilters: () => void;
  sort: SortState;
  onSortChange: (sort: SortState) => void;
  onOpen: (id: string) => void;
}

function LibraryBody({
  view,
  loading,
  error,
  onRetry,
  items,
  quick,
  totalAll,
  onClearFilters,
  sort,
  onSortChange,
  onOpen,
}: LibraryBodyProps) {
  if (error) {
    return (
      <EmptyState
        title="読み込みに失敗しました"
        description="通信に失敗しました"
        action={{ label: "再試行", onClick: onRetry }}
      />
    );
  }
  if (loading) {
    return (
      <div style={{ fontSize: 11.5, color: "var(--pr-text-muted)", padding: "24px 0" }}>
        読み込み中…
      </div>
    );
  }
  if (items.length === 0) {
    const libraryEmpty = quick === "all" && totalAll === 0;
    return libraryEmpty ? (
      <EmptyState
        title="まだ論文がありません"
        description="ブラウザ拡張で arXiv ページを開き「保存」すると、ここに並びます"
      />
    ) : (
      <EmptyState
        title="条件に一致する論文がありません"
        description="フィルタを解除するか、検索語を変えてください"
        action={{ label: "フィルタをすべて解除", onClick: onClearFilters }}
      />
    );
  }

  if (view === "table") {
    return (
      <LibraryTableView items={items} sort={sort} onSortChange={onSortChange} onOpenRow={onOpen} />
    );
  }
  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "1fr 1fr 1fr",
        gap: 14,
        overflowY: "auto",
        minHeight: 0,
        alignContent: "start",
        padding: "2px 2px 8px",
      }}
    >
      {items.map((item) => (
        <LibraryCard key={item.id} item={item} onOpen={onOpen} />
      ))}
    </div>
  );
}
