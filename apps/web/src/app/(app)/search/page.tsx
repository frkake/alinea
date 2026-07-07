"use client";

import { useEffect } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { keepPreviousData, useInfiniteQuery } from "@tanstack/react-query";
import { searchAll } from "@yakudoku/api-client";
import { SearchResults } from "@/components/search/SearchResults";
import type { SearchSortOption, SearchSourceFilter } from "@/components/search/searchNav";

/**
 * 横断検索 全結果画面(4e)。`apps/web/src/app/(app)/search/page.tsx`。
 * 状態は URL クエリのみで表現する(plans/09-screens/4e §1)。
 */

const VALID_SOURCES: readonly SearchSourceFilter[] = ["all", "body", "notes", "chat", "article"];
const VALID_SORTS: readonly SearchSortOption[] = ["relevance", "recency"];

function normalizeSource(raw: string | null): SearchSourceFilter {
  return raw && (VALID_SOURCES as readonly string[]).includes(raw) ? (raw as SearchSourceFilter) : "all";
}

function normalizeSort(raw: string | null): SearchSortOption {
  return raw && (VALID_SORTS as readonly string[]).includes(raw) ? (raw as SearchSortOption) : "relevance";
}

export default function SearchPage() {
  const router = useRouter();
  const searchParams = useSearchParams();

  const q = (searchParams.get("q") ?? "").trim();
  const source = normalizeSource(searchParams.get("source"));
  const paper = searchParams.get("paper");
  const sort = normalizeSort(searchParams.get("sort"));

  // §5.8: document.title「「{q}」の検索結果 — 訳読」/ 未入力は「検索 — 訳読」。
  useEffect(() => {
    document.title = q ? `「${q}」の検索結果 — 訳読` : "検索 — 訳読";
  }, [q]);

  const resultsQuery = useInfiniteQuery({
    queryKey: ["search", "results", { q, source, paper, sort }],
    queryFn: async ({ pageParam }: { pageParam: string | undefined }) =>
      (
        await searchAll({
          query: {
            q,
            source: source === "all" ? undefined : source,
            library_item_id: paper ?? undefined,
            sort,
            cursor: pageParam,
            limit: 10,
          },
          throwOnError: true,
        })
      ).data,
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    enabled: q.length > 0,
    staleTime: 30_000,
    refetchOnWindowFocus: false,
    placeholderData: keepPreviousData,
  });

  const pages = resultsQuery.data?.pages ?? [];
  const lastPage = pages.at(-1);
  const groups = pages.flatMap((p) => p.groups);

  const setParams = (updates: Record<string, string | null>, mode: "push" | "replace" = "replace") => {
    const params = new URLSearchParams(searchParams.toString());
    for (const [key, value] of Object.entries(updates)) {
      if (value == null || value === "") params.delete(key);
      else params.set(key, value);
    }
    const url = params.toString() ? `/search?${params.toString()}` : "/search";
    if (mode === "push") router.push(url);
    else router.replace(url);
  };

  return (
    <SearchResults
      q={q}
      source={source}
      paper={paper}
      sort={sort}
      facets={lastPage?.facets ?? null}
      total={lastPage?.total ?? 0}
      paperCount={lastPage?.paper_count ?? 0}
      groups={groups}
      isPending={resultsQuery.isPending}
      isError={resultsQuery.isError}
      isPlaceholderData={resultsQuery.isPlaceholderData}
      hasNextPage={resultsQuery.hasNextPage}
      isFetchingNextPage={resultsQuery.isFetchingNextPage}
      onSourceChange={(s) => setParams({ source: s === "all" ? null : s })}
      onPaperChange={(id) => setParams({ paper: id })}
      onSortChange={(s) => setParams({ sort: s === "relevance" ? null : s })}
      onRetry={() => void resultsQuery.refetch()}
      onLoadMore={() => {
        if (!resultsQuery.isFetchingNextPage) void resultsQuery.fetchNextPage();
      }}
    />
  );
}
