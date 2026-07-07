"use client";

import { useMemo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { libraryItemsUpdate, type LibraryItemSummary } from "@yakudoku/api-client";
import type { ReadingStatus } from "@yakudoku/tokens";
import { LibraryTable } from "@/components/ui/LibraryTable";
import { toTableRow } from "@/components/library/toTableRow";
import { useFinishReadingStore } from "@/components/library/finishReadingStore";
import { useToast } from "@/components/ui/Toast";
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
  const qc = useQueryClient();
  const toast = useToast();

  // ステータスピル(テーブル行)からの変更(1g §2.3 の発火規約: done への PATCH 成功で
  // 読了ダイアログを開く。LibraryCard と同じ配線)。
  const onStatusChange = (id: string, next: ReadingStatus) => {
    const prevStatus = rows.find((r) => r.id === id)?.status;
    if (prevStatus === next) return;
    void libraryItemsUpdate({
      path: { item_id: id },
      body: { status: next },
      throwOnError: true,
    }).then(
      (res) => {
        void qc.invalidateQueries({ queryKey: ["library"] });
        void qc.invalidateQueries({ queryKey: ["dashboard"] });
        if (prevStatus !== "done" && next === "done" && res.data) {
          useFinishReadingStore.getState().open(res.data);
        }
      },
      () => {
        toast({ kind: "error", message: "ステータスを変更できませんでした" });
      },
    );
  };

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
      onStatusChange={onStatusChange}
    />
  );
}
