"use client";

import { useMemo, useState } from "react";
import type { LibraryItemSummary } from "@yakudoku/api-client";
import { LibraryTable } from "@/components/ui/LibraryTable";
import { toTableRow } from "@/components/library/toTableRow";
import type { SortState } from "@/components/library/types";

/**
 * ライブラリ テーブルビューの画面組込(1e §3〜§5)。
 * 共通 LibraryTable(plans/08 §5.15)に LibraryItemSummary → LibraryTableRow の変換を橋渡しする。
 * 10 列固定・未供給列「—」は共通コンポーネント側で担保。
 * M0 スコープ: 行選択は表示のみ(一括操作バーは非表示=M1)。ソートは呼び出し側が保持しサーバへ反映。
 */
export interface LibraryTableViewProps {
  items: LibraryItemSummary[];
  sort: SortState;
  onSortChange: (sort: SortState) => void;
  onOpenRow: (id: string) => void;
}

export function LibraryTableView({ items, sort, onSortChange, onOpenRow }: LibraryTableViewProps) {
  const rows = useMemo(() => items.map(toTableRow), [items]);
  const [selectedIds, setSelectedIds] = useState<ReadonlySet<string>>(() => new Set());

  const toggleSelect = (id: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const toggleSelectAll = () => {
    setSelectedIds((prev) =>
      prev.size === rows.length ? new Set() : new Set(rows.map((r) => r.id)),
    );
  };

  return (
    <LibraryTable
      rows={rows}
      selectedIds={selectedIds}
      onToggleSelect={toggleSelect}
      onToggleSelectAll={toggleSelectAll}
      sort={sort}
      onSortChange={onSortChange}
      onOpenRow={onOpenRow}
    />
  );
}
