"use client";

import { useMemo, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  libraryItemsBulk,
  libraryItemsUpdate,
  type BulkOperationBody,
  type LibraryItemSummary,
} from "@yakudoku/api-client";
import type { ReadingStatus } from "@yakudoku/tokens";
import { LibraryTable } from "@/components/ui/LibraryTable";
import { BulkActionBar } from "@/components/library/BulkActionBar";
import { toTableRow } from "@/components/library/toTableRow";
import { useFinishReadingStore } from "@/components/library/finishReadingStore";
import { useToast } from "@/components/ui/Toast";
import { DeleteLibraryItemConfirmModal } from "@/components/library/DeleteLibraryItemConfirmModal";
import { useDeleteLibraryItem } from "@/hooks/useDeleteLibraryItem";
import type { SortState } from "@/components/library/types";

/**
 * ライブラリ テーブルビューの画面組込(1e §3〜§5)。
 * 共通 LibraryTable(plans/08 §5.15)に LibraryItemSummary → LibraryTableRow の変換を橋渡しする。
 * 11 列固定・未供給列「—」は共通コンポーネント側で担保。
 * 複数選択→`BulkActionBar`(1e §4.8・§5.5・plans/03 §5.6)。ソートは呼び出し側が保持しサーバへ反映。
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
  const [deleteTarget, setDeleteTarget] = useState<{ id: string; title: string } | null>(null);
  const qc = useQueryClient();
  const toast = useToast();
  const deleteItem = useDeleteLibraryItem({
    onSuccess: (deleted) => {
      setDeleteTarget(null);
      setSelectedIds((prev) => {
        const next = new Set(prev);
        next.delete(deleted.id);
        return next;
      });
    },
  });

  // 一括操作(§5.6・1e §5.5): 成功・失敗いずれも選択は維持する(連続操作のため)。
  const bulkMutation = useMutation({
    mutationFn: async (body: BulkOperationBody) => {
      const res = await libraryItemsBulk({ body });
      if (res.error !== undefined) {
        const problem = res.error as { title?: string; detail?: string };
        throw new Error(problem.detail ?? problem.title ?? "一括操作に失敗しました");
      }
      return res.data;
    },
    onSuccess: (data) => {
      toast({ kind: "success", message: `${data?.updated ?? 0} 件を更新しました` });
      void qc.invalidateQueries({ queryKey: ["library"] });
    },
    onError: (err: Error) => {
      toast({ kind: "error", message: err.message });
    },
  });

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

  const clearSelection = () => setSelectedIds(new Set());

  return (
    <>
      <LibraryTable
        rows={rows}
        selectedIds={selectedIds}
        onToggleSelect={toggleSelect}
        onToggleSelectAll={toggleSelectAll}
        sort={sort}
        onSortChange={onSortChange}
        onOpenRow={onOpenRow}
        onStatusChange={onStatusChange}
        onDeleteRow={setDeleteTarget}
      />
      <BulkActionBar
        selectedCount={selectedIds.size}
        busy={bulkMutation.isPending}
        onClearSelection={clearSelection}
        onSetStatus={(status) => {
          bulkMutation.mutate({ ids: Array.from(selectedIds), op: "set_status", status });
        }}
        onAddTags={(tags) => {
          bulkMutation.mutate({ ids: Array.from(selectedIds), op: "add_tags", tags });
        }}
        onAddToCollection={(collectionId) => {
          bulkMutation.mutate({
            ids: Array.from(selectedIds),
            op: "add_to_collection",
            collection_id: collectionId,
          });
        }}
      />
      <DeleteLibraryItemConfirmModal
        open={deleteTarget !== null}
        title={deleteTarget?.title ?? ""}
        pending={deleteItem.isPending}
        onCancel={() => setDeleteTarget(null)}
        onConfirm={() => {
          if (deleteTarget) deleteItem.mutate(deleteTarget);
        }}
      />
    </>
  );
}
