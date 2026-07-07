"use client";

import { useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  libraryItemsFacets,
  libraryItemsList,
  savedFiltersCreate,
  type LibraryItemSummary,
} from "@yakudoku/api-client";
import { EmptyState } from "@/components/ui/EmptyState";
import { Popover } from "@/components/ui/Popover";
import { useToast } from "@/components/ui/Toast";
import { ViewSwitch } from "@/components/library/ViewSwitch";
import { QuickFilterBar } from "@/components/library/QuickFilterBar";
import {
  AttributeFilterBar,
  emptyAttributeFilters,
  hasAppliedAttributeFilters,
  type AppliedAttributeFilters,
} from "@/components/library/AttributeFilterBar";
import { savedFiltersQueryKey } from "@/components/library/SavedFilterList";
import { LibraryTableView } from "@/components/library/LibraryTableView";
import { LibraryCard } from "@/components/library/LibraryCard";
import { useIsMobile } from "@/hooks/useMediaQuery";
import type { LibraryView, Quick, SortState } from "@/components/library/types";

/**
 * サーバがソート可能かつ保存フィルタに保存できるキー(plans/03 §5.1・§5.14 / library_items._SORTS)。
 * ステータス/品質はソート不可(テーブル列ヘッダのクリックも無視する)。
 */
const VALID_SORT_KEYS = new Set<SortState["key"]>([
  "updated_at",
  "added_at",
  "title",
  "deadline",
  "priority",
  "reading_time",
  "comprehension",
]);
type SavableSortKey =
  | "updated_at"
  | "added_at"
  | "title"
  | "deadline"
  | "reading_time"
  | "comprehension"
  | "priority";

/** 属性フィルタの現在値 → §5.1/§5.2 のクエリ語彙(空配列/null は「絞らない」= キー省略)。 */
function attributeQuery(v: AppliedAttributeFilters) {
  return {
    status: v.status.length > 0 ? v.status : undefined,
    tag: v.tags.length > 0 ? v.tags : undefined,
    collection_id: v.collectionId ?? undefined,
    quality: v.quality ?? undefined,
    year: v.years.length > 0 ? v.years : undefined,
  };
}

/**
 * ライブラリ画面(1e テーブル + 4a カード)。
 * - クイックフィルタ 5 種+属性フィルタ 5 種(件数=facets)・ビュー切替・基本ソート(テーブル列ヘッダ)。
 * - 「この条件を保存」(保存フィルタ作成)・`?filter_id=` 適用(サイドバーからの遷移。plans/03 §5.1)。
 * - 一括操作(一括バー)は `LibraryTableView`(テーブルビュー専用。docs/06 §8.5)。
 * - 検索ドロップダウン・通知は別タスク(M1-13/M1-08)。
 */
export default function LibraryPage() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const isMobile = useIsMobile();
  const qc = useQueryClient();
  const toast = useToast();
  const [view, setView] = useState<LibraryView>("table");
  const [quick, setQuick] = useState<Quick>("all");
  const [sort, setSort] = useState<SortState>({ key: "updated_at", dir: "desc" });
  const [attributeFilters, setAttributeFilters] = useState<AppliedAttributeFilters>(
    emptyAttributeFilters(),
  );
  // モバイル縮退(mobile.md §5.1): カードビューのみ(テーブルビューは提供しない)。
  const effectiveView: LibraryView = isMobile ? "card" : view;
  const filterId = searchParams.get("filter_id");

  const facetsQuery = useQuery({
    queryKey: ["library", "facets", attributeFilters],
    queryFn: async () =>
      (
        await libraryItemsFacets({ query: attributeQuery(attributeFilters), throwOnError: true })
      ).data,
  });

  const listQuery = useQuery({
    queryKey: ["library", "list", { quick, sort, attributeFilters, filterId }],
    queryFn: async () =>
      (
        await libraryItemsList({
          query: {
            quick,
            sort: sort.key,
            order: sort.dir,
            limit: 50,
            filter_id: filterId ?? undefined,
            ...attributeQuery(attributeFilters),
          },
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

  const canSaveFilter =
    quick !== "all" ||
    hasAppliedAttributeFilters(attributeFilters) ||
    sort.key !== "updated_at" ||
    sort.dir !== "desc";

  const handleSaveFilter = async (name: string) => {
    // sort.key は VALID_SORT_KEYS(=保存フィルタが受け付ける 7 キーのみ)に限定済み(onSortChange)。
    const savableKey = sort.key as SavableSortKey;
    const res = await savedFiltersCreate({
      body: {
        name,
        conditions: {
          quick,
          status: attributeFilters.status.length > 0 ? attributeFilters.status : undefined,
          tags: attributeFilters.tags.length > 0 ? attributeFilters.tags : undefined,
          collection_id: attributeFilters.collectionId ?? undefined,
          quality: attributeFilters.quality ?? undefined,
          years: attributeFilters.years.length > 0 ? attributeFilters.years : undefined,
        },
        sort: { key: savableKey, order: sort.dir },
      },
    });
    if (res.error !== undefined) {
      const problem = res.error as { title?: string; detail?: string };
      toast({ kind: "error", message: problem.detail ?? problem.title ?? "保存に失敗しました" });
      return;
    }
    toast({ kind: "success", message: `保存フィルタ「${name}」を作成しました` });
    void qc.invalidateQueries({ queryKey: savedFiltersQueryKey });
  };

  const items = listQuery.data?.items ?? [];
  const total = facetsQuery.data?.quick.all;

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 12,
        padding: isMobile ? "16px" : "16px 22px",
        minHeight: 0,
      }}
    >
      {/* 見出し行 */}
      <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
        <h1 style={{ fontSize: 16, fontWeight: 700, margin: 0 }}>ライブラリ</h1>
        {total != null ? (
          <span style={{ fontSize: 11.5, color: "var(--pr-text-muted)" }}>{total} 本</span>
        ) : null}
        {/* ビュー切替(カード⇄テーブル)は操作系のためモバイルでは非描画(mobile.md §5.1)。 */}
        {isMobile ? null : (
          <span style={{ marginLeft: 6 }}>
            <ViewSwitch view={view} onViewChange={setView} />
          </span>
        )}
        {/* 「この条件を保存」はテーブルビューのみ(1e §4.5・docs/06 §8.1)。 */}
        {!isMobile && view === "table" ? (
          <span style={{ marginLeft: "auto" }}>
            <SaveFilterButton disabled={!canSaveFilter} onSave={handleSaveFilter} />
          </span>
        ) : null}
      </div>

      {/* クイックフィルタ+属性フィルタ */}
      <QuickFilterBar facets={facetsQuery.data?.quick} quick={quick} onQuickChange={setQuick} />
      <AttributeFilterBar
        facets={facetsQuery.data}
        value={attributeFilters}
        onChange={setAttributeFilters}
      />

      {/* 本体 */}
      <LibraryBody
        view={effectiveView}
        loading={listQuery.isPending}
        error={listQuery.isError}
        onRetry={() => void listQuery.refetch()}
        items={items}
        quick={quick}
        totalAll={total}
        onClearFilters={() => {
          setQuick("all");
          setAttributeFilters(emptyAttributeFilters());
        }}
        sort={sort}
        onSortChange={onSortChange}
        onOpen={openReader}
        gridColumns={isMobile ? "1fr" : "1fr 1fr 1fr"}
      />
    </div>
  );
}

/** 「この条件を保存」ボタン+名前入力ポップオーバー(1e §4.5・§5.4)。 */
function SaveFilterButton({
  disabled,
  onSave,
}: {
  disabled: boolean;
  onSave: (name: string) => Promise<void>;
}) {
  const anchorRef = useRef<HTMLButtonElement>(null);
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [saving, setSaving] = useState(false);
  const trimmed = name.trim();

  const submit = async () => {
    if (!trimmed || saving) return;
    setSaving(true);
    try {
      await onSave(trimmed);
      setOpen(false);
      setName("");
    } finally {
      setSaving(false);
    }
  };

  return (
    <>
      <button
        ref={anchorRef}
        type="button"
        disabled={disabled}
        onClick={() => setOpen((v) => !v)}
        style={{
          height: 26,
          padding: "0 10px",
          border: "1px solid var(--pr-border-control)",
          borderRadius: 6,
          fontSize: 11,
          color: "var(--pr-text-mid)",
          background: "var(--pr-bg-control)",
          cursor: disabled ? "default" : "pointer",
          opacity: disabled ? 0.5 : 1,
          fontFamily: "inherit",
        }}
      >
        この条件を保存
      </button>
      <Popover
        open={open}
        onClose={() => setOpen(false)}
        anchorRef={anchorRef}
        width={260}
        placement="bottom-end"
      >
        <div style={{ padding: 12 }}>
          <div
            style={{
              fontSize: 10.5,
              fontWeight: 600,
              color: "var(--pr-text-muted)",
              marginBottom: 6,
            }}
          >
            フィルタ名
          </div>
          <input
            aria-label="フィルタ名"
            value={name}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                void submit();
              }
            }}
            style={{
              width: "100%",
              height: 28,
              padding: "0 10px",
              border: "1px solid var(--pr-border-control)",
              borderRadius: 6,
              fontSize: 12,
              fontFamily: "inherit",
              marginBottom: 8,
            }}
          />
          <div style={{ display: "flex", justifyContent: "flex-end" }}>
            <button
              type="button"
              disabled={!trimmed || saving}
              onClick={() => void submit()}
              style={{
                height: 26,
                padding: "0 12px",
                border: "none",
                borderRadius: 6,
                background: "var(--pr-acc)",
                color: "#FFFFFF",
                fontSize: 11,
                fontWeight: 600,
                cursor: !trimmed || saving ? "default" : "pointer",
                opacity: !trimmed || saving ? 0.5 : 1,
                fontFamily: "inherit",
              }}
            >
              保存
            </button>
          </div>
        </div>
      </Popover>
    </>
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
  /** カードグリッドの列数(mobile.md §5.1: モバイルは 1fr の 1 カラム)。 */
  gridColumns?: string;
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
  gridColumns = "1fr 1fr 1fr",
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
        gridTemplateColumns: gridColumns,
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
