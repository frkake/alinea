"use client";

import { useParams, useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { EmptyState } from "@/components/ui/EmptyState";
import { useToast } from "@/components/ui/Toast";
import { CollectionHeader } from "@/components/collections/CollectionHeader";
import { ShareLinkCard } from "@/components/collections/ShareLinkCard";
import { CollectionEntryList } from "@/components/collections/CollectionEntryList";
import {
  addEntry,
  ApiError,
  getCollection,
  issueShare,
  patchCollection,
  patchEntry,
  patchShare,
  removeEntry,
  reorderEntries,
  revokeShare,
} from "@/components/collections/api";
import type { CollectionDetail, CollectionPatch, EntryPatch } from "@/components/collections/types";

/**
 * コレクション詳細画面(4b。plans/09-screens/4b-collection-detail.md)。
 * ルート `/collections/{collectionId}`。`(app)/layout.tsx` の共通シェル(AppHeader/AppNav)配下。
 */
export default function CollectionDetailPage() {
  const params = useParams<{ collectionId: string }>();
  const collectionId = params.collectionId;
  const router = useRouter();
  const queryClient = useQueryClient();
  const toast = useToast();

  const queryKey = ["collection", collectionId] as const;

  const detailQuery = useQuery({
    queryKey,
    queryFn: () => getCollection(collectionId),
    staleTime: 30_000,
  });

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey });
  };

  const patchMutation = useMutation({
    mutationFn: (patch: CollectionPatch) => patchCollection(collectionId, patch),
    onSuccess: (data) => {
      queryClient.setQueryData(queryKey, data);
    },
    onError: () => {
      toast({ kind: "error", message: "保存できませんでした" });
    },
  });

  const addMutation = useMutation({
    mutationFn: (libraryItemId: string) => addEntry(collectionId, libraryItemId),
    onSuccess: invalidate,
    onError: (err: unknown) => {
      if (err instanceof ApiError && err.code === "duplicate") {
        toast({ kind: "error", message: "すでにこのコレクションにあります" });
      } else {
        toast({ kind: "error", message: "追加できませんでした" });
      }
    },
  });

  const patchEntryMutation = useMutation({
    mutationFn: ({ entryId, patch }: { entryId: string; patch: EntryPatch }) =>
      patchEntry(entryId, patch),
    onSuccess: invalidate,
    onError: () => {
      toast({ kind: "error", message: "保存できませんでした" });
    },
  });

  const removeMutation = useMutation({
    mutationFn: (entryId: string) => removeEntry(entryId),
    onSuccess: () => {
      invalidate();
      toast({ kind: "success", message: "コレクションから外しました(論文はライブラリに残ります)" });
    },
    onError: () => {
      toast({ kind: "error", message: "操作に失敗しました" });
    },
  });

  const reorderMutation = useMutation({
    mutationFn: (entryIds: string[]) => reorderEntries(collectionId, entryIds),
    onError: () => {
      toast({ kind: "error", message: "並べ替えを保存できませんでした" });
      invalidate();
    },
  });

  const issueShareMutation = useMutation({
    mutationFn: () => issueShare(collectionId),
    onSuccess: invalidate,
    onError: (err: unknown) => {
      if (!(err instanceof ApiError && err.code === "conflict")) {
        toast({ kind: "error", message: "共有リンクを発行できませんでした" });
      }
      invalidate();
    },
  });

  const patchShareMutation = useMutation({
    mutationFn: (includeNotes: boolean) => patchShare(collectionId, includeNotes),
    onSuccess: invalidate,
    onError: () => {
      toast({ kind: "error", message: "設定を変更できませんでした" });
      invalidate();
    },
  });

  const revokeShareMutation = useMutation({
    mutationFn: () => revokeShare(collectionId),
    onSuccess: () => {
      invalidate();
      toast({ kind: "success", message: "共有リンクを無効化しました" });
    },
    onError: () => {
      toast({ kind: "error", message: "無効化できませんでした" });
    },
  });

  if (detailQuery.isError) {
    const notFound = detailQuery.error instanceof ApiError && detailQuery.error.status === 404;
    return (
      <div style={{ padding: "18px 26px" }}>
        <EmptyState
          title={notFound ? "コレクションが見つかりません" : "読み込みに失敗しました"}
          description={
            notFound
              ? "削除されたか、URL が正しくない可能性があります。"
              : "時間をおいて再試行してください。"
          }
          action={
            notFound
              ? { label: "ライブラリへ戻る", onClick: () => router.push("/library") }
              : { label: "再試行", onClick: () => void detailQuery.refetch() }
          }
        />
      </div>
    );
  }

  if (detailQuery.isPending) {
    return (
      <div style={{ padding: "18px 26px", fontSize: 11.5, color: "var(--pr-text-muted)" }}>
        読み込み中…
      </div>
    );
  }

  const collection: CollectionDetail = detailQuery.data;

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 14,
        padding: "18px 26px",
        height: "100%",
        minHeight: 0,
      }}
    >
      <div style={{ display: "flex", alignItems: "flex-start", gap: 18 }}>
        <CollectionHeader
          collection={collection}
          onPatch={(patch) => patchMutation.mutate(patch)}
        />
        <ShareLinkCard
          share={collection.share}
          issuing={issueShareMutation.isPending}
          onIssue={() => issueShareMutation.mutate()}
          onToggleNotes={(next) => patchShareMutation.mutate(next)}
          onRevoke={() => revokeShareMutation.mutate()}
        />
      </div>

      <CollectionEntryList
        entries={collection.entries}
        onOpen={(libraryItemId) => router.push(`/papers/${libraryItemId}`)}
        onReorder={(entryIds) => {
          const byId = new Map(collection.entries.map((e) => [e.id, e]));
          const reordered = entryIds
            .map((id, idx) => {
              const entry = byId.get(id);
              return entry ? { ...entry, order: idx + 1 } : null;
            })
            .filter((e): e is NonNullable<typeof e> => e !== null);
          queryClient.setQueryData(queryKey, { ...collection, entries: reordered });
          reorderMutation.mutate(entryIds);
        }}
        onAddEntry={(libraryItemId) => addMutation.mutate(libraryItemId)}
        onPatchEntry={(entryId, patch) => patchEntryMutation.mutate({ entryId, patch })}
        onRemoveEntry={(entryId) => removeMutation.mutate(entryId)}
      />
    </div>
  );
}
