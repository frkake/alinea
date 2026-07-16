"use client";

import { useEffect, useRef, useState } from "react";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { useInfiniteQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { vocabDelete, vocabList, vocabReviewQueue } from "@alinea/api-client";
import { useToast } from "@/components/ui/Toast";
import { triggerDownload } from "@/components/settings/download";
import { VocabHeader } from "@/components/vocab/VocabHeader";
import { VocabFilterRow } from "@/components/vocab/VocabFilterRow";
import { VocabList, type VocabListSort } from "@/components/vocab/VocabList";
import { VocabDetail } from "@/components/vocab/VocabDetail";
import { VocabReviewModal } from "@/components/vocab/VocabReviewModal";
import { useVocabReviewStore } from "@/components/vocab/review-store";
import { vocabCountsQueryKey, vocabListQueryKey } from "@/components/vocab/queryKeys";
import type { VocabKind } from "@/components/vocab/types";

const EMPTY_COUNTS = { all: 0, word: 0, collocation: 0, idiom: 0, due: 0 };

function normalizeKind(raw: string | null): VocabKind | null {
  return raw === "word" || raw === "collocation" || raw === "idiom" ? raw : null;
}

/**
 * 語彙帳画面(4d)。`/vocab` 及び `/vocab/{vocab_id}`(オプショナルキャッチオール)。
 * 選択・フィルタ・検索・ソートはすべて URL(パス+クエリ)が正(plans/09-screens/4d §1)。
 */
export default function VocabPage() {
  const router = useRouter();
  const params = useParams<{ vocabId?: string[] }>();
  const searchParams = useSearchParams();
  const queryClient = useQueryClient();
  const toast = useToast();

  const vocabIdParam = params.vocabId?.[0] ?? null;
  const kind = normalizeKind(searchParams.get("kind"));
  const dueOnly = searchParams.get("due") === "true";
  const q = searchParams.get("q") ?? "";
  const sort: VocabListSort = searchParams.get("sort") === "term" ? "term" : "added_at";

  const [pendingDeleteIds, setPendingDeleteIds] = useState<Set<string>>(new Set());
  const deleteTimers = useRef(new Map<string, { timer: number; term: string }>());

  function buildHref(
    nextVocabId: string | null,
    overrides: { kind?: VocabKind | null; due?: boolean; q?: string; sort?: VocabListSort } = {},
  ): string {
    const sp = new URLSearchParams(searchParams.toString());
    if ("kind" in overrides) {
      if (overrides.kind) sp.set("kind", overrides.kind);
      else sp.delete("kind");
    }
    if ("due" in overrides) {
      if (overrides.due) sp.set("due", "true");
      else sp.delete("due");
    }
    if ("q" in overrides) {
      if (overrides.q) sp.set("q", overrides.q);
      else sp.delete("q");
    }
    if ("sort" in overrides) {
      if (overrides.sort && overrides.sort !== "added_at") sp.set("sort", overrides.sort);
      else sp.delete("sort");
    }
    const qs = sp.toString();
    const path = nextVocabId ? `/vocab/${nextVocabId}` : "/vocab";
    return qs ? `${path}?${qs}` : path;
  }

  const replaceTo = (
    nextVocabId: string | null,
    overrides: { kind?: VocabKind | null; due?: boolean; q?: string; sort?: VocabListSort } = {},
  ) => {
    router.replace(buildHref(nextVocabId, overrides), { scroll: false });
  };

  const listQuery = useInfiniteQuery({
    queryKey: vocabListQueryKey({ kind, due: dueOnly || undefined, q: q || undefined, sort }),
    queryFn: async ({ pageParam }: { pageParam?: string }) => {
      const res = await vocabList({
        query: {
          kind: kind ? [kind] : undefined,
          due: dueOnly ? true : undefined,
          q: q || undefined,
          sort,
          cursor: pageParam,
          limit: 50,
        },
        throwOnError: true,
      });
      return res.data;
    },
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    staleTime: 30_000,
  });

  const pages = listQuery.data?.pages ?? [];
  const rawEntries = pages.flatMap((p) => p.items);
  const entries = rawEntries.filter((e) => !pendingDeleteIds.has(e.id));
  const counts = pages[0]?.counts ?? EMPTY_COUNTS;
  const effectiveSelectedId = vocabIdParam ?? entries[0]?.id ?? null;

  // サイドバーバッジ(AppNav)用キャッシュを充足し、追加リクエストを避ける(4d §2.1 決定)。
  const firstPage = pages[0];
  useEffect(() => {
    if (firstPage) {
      queryClient.setQueryData(vocabCountsQueryKey, firstPage);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [firstPage]);

  // フィルタ/検索/ソート変更後、現在選択中のエントリが結果に含まれない場合は先頭へ補正する(4d §5.2)。
  useEffect(() => {
    if (listQuery.isLoading || listQuery.hasNextPage) return;
    if (vocabIdParam === null) return;
    if (entries.some((e) => e.id === vocabIdParam)) return;
    const first = entries[0];
    if (first) {
      replaceTo(first.id);
    } else {
      router.replace(buildHref(null), { scroll: false });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [entries, vocabIdParam, listQuery.isLoading, listQuery.hasNextPage]);

  const startReviewMutation = useMutation({
    mutationFn: async () => (await vocabReviewQueue({ throwOnError: true })).data,
    onSuccess: (data) => {
      if (data.items.length === 0) {
        toast({ kind: "info", message: "復習期の語彙はありません" });
        void queryClient.invalidateQueries({ queryKey: ["vocab"] });
        return;
      }
      useVocabReviewStore.getState().start(data.items);
    },
    onError: () => {
      toast({ kind: "error", message: "復習キューを取得できませんでした" });
    },
  });

  const finalizeDelete = (id: string) => {
    void vocabDelete({ path: { vocab_id: id }, throwOnError: true })
      .catch(() => {
        toast({ kind: "error", message: "削除できませんでした" });
      })
      .finally(() => {
        deleteTimers.current.delete(id);
        void queryClient.invalidateQueries({ queryKey: ["vocab"] });
      });
  };

  const handleDeleteRequested = (id: string, term: string) => {
    if (effectiveSelectedId === id) {
      const idx = entries.findIndex((e) => e.id === id);
      const next = entries[idx + 1] ?? entries[idx - 1] ?? null;
      replaceTo(next ? next.id : null);
    }
    setPendingDeleteIds((prev) => new Set(prev).add(id));
    const timer = window.setTimeout(() => finalizeDelete(id), 6000);
    deleteTimers.current.set(id, { timer, term });
    toast({
      kind: "info",
      message: `「${term}」を削除しました`,
      action: {
        label: "元に戻す",
        onClick: () => {
          const rec = deleteTimers.current.get(id);
          if (rec) {
            window.clearTimeout(rec.timer);
            deleteTimers.current.delete(id);
          }
          setPendingDeleteIds((prev) => {
            const next = new Set(prev);
            next.delete(id);
            return next;
          });
        },
      },
    });
  };

  // アンマウント時、遅延削除が残っていれば即時確定させる(plans/02 §1 決定)。
  useEffect(() => {
    const timers = deleteTimers.current;
    return () => {
      for (const [id, rec] of timers) {
        window.clearTimeout(rec.timer);
        void vocabDelete({ path: { vocab_id: id }, throwOnError: true }).catch(() => undefined);
      }
      timers.clear();
    };
  }, []);

  const handleNotFound = (id: string) => {
    toast({ kind: "error", message: "この語彙は見つかりませんでした" });
    if (vocabIdParam === id) {
      router.replace(buildHref(null), { scroll: false });
    }
  };

  const handleOpenSource = (libraryItemId: string, blockId: string) => {
    router.push(`/papers/${libraryItemId}?mode=source&block=${blockId}`);
  };

  function handleAnkiExport(): void {
    const sp = new URLSearchParams();
    if (kind) sp.set("kind", kind);
    if (dueOnly) sp.set("due", "true");
    if (q) sp.set("q", q);
    if (sort !== "added_at") sp.set("sort", sort);
    const qs = sp.toString();
    triggerDownload(`/api/vocab/export/anki${qs ? `?${qs}` : ""}`);
  }

  const filtersActive = kind !== null || dueOnly || q.length > 0;
  const emptyVariant =
    listQuery.isLoading || listQuery.isError || entries.length > 0
      ? null
      : filtersActive
        ? "no-match"
        : "no-entries";

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        minHeight: 0,
        padding: "16px 22px",
        gap: 12,
        background: "var(--pr-bg-app-alt)",
      }}
    >
      <VocabHeader
        total={counts.all}
        dueCount={counts.due}
        searchValue={q}
        searchFetching={listQuery.isFetching && !listQuery.isFetchingNextPage}
        onSearchChange={(v) => replaceTo(vocabIdParam, { q: v })}
        onStartReview={() => startReviewMutation.mutate()}
        reviewLoading={startReviewMutation.isPending}
        onExportMarkdown={() => {
          const sp = new URLSearchParams();
          if (kind) sp.set("kind", kind);
          if (dueOnly) sp.set("due", "true");
          if (q) sp.set("q", q);
          if (sort !== "added_at") sp.set("sort", sort);
          triggerDownload(
            `/api/vocab/export/markdown${sp.size ? `?${sp.toString()}` : ""}`,
          );
        }}
        onAnkiExport={handleAnkiExport}
      />
      <VocabFilterRow
        counts={counts}
        kind={kind}
        dueOnly={dueOnly}
        onKindChange={(k) => replaceTo(vocabIdParam, { kind: k })}
        onDueToggle={() => replaceTo(vocabIdParam, { due: !dueOnly })}
      />
      <div style={{ display: "flex", gap: 14, flex: 1, minHeight: 0 }}>
        <VocabList
          entries={entries}
          selectedId={effectiveSelectedId}
          sort={sort}
          onSortChange={(s) => replaceTo(vocabIdParam, { sort: s })}
          onSelect={(id) => replaceTo(id)}
          onReachEnd={() => {
            if (listQuery.hasNextPage && !listQuery.isFetchingNextPage) {
              void listQuery.fetchNextPage();
            }
          }}
          isFetchingNextPage={listQuery.isFetchingNextPage}
          emptyVariant={emptyVariant}
          onClearFilters={() => router.replace("/vocab", { scroll: false })}
          isLoading={listQuery.isLoading}
          isError={listQuery.isError}
          onRetry={() => void listQuery.refetch()}
        />
        <VocabDetail
          vocabId={effectiveSelectedId}
          onOpenSource={handleOpenSource}
          onDeleteRequested={handleDeleteRequested}
          onNotFound={handleNotFound}
        />
      </div>
      <VocabReviewModal />
    </div>
  );
}
