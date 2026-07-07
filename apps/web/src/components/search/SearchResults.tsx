"use client";

import { useEffect, useRef, useState, type CSSProperties } from "react";
import type { SearchFacets, SearchGroup, SearchHit } from "@yakudoku/api-client";
import { Card } from "@/components/ui/Card";
import { StatusPill } from "@/components/ui/StatusPill";
import { EmptyState } from "@/components/ui/EmptyState";
import { Popover } from "@/components/ui/Popover";
import { cardAuthors, toReadingStatus } from "@/components/library/format";
import { SourceBadge } from "@/components/search/SourceBadge";
import {
  formatArticleDate,
  hrefForSearchTarget,
  jumpLabelForTarget,
  resultsBadges,
  snippetFontVar,
  type SearchSortOption,
  type SearchSourceFilter,
} from "@/components/search/searchNav";

/**
 * 全結果画面(4e)の本体: 左ファセットレール+結果カラム(plans/09-screens/4e §3〜5)。
 * データ取得・URL 状態は呼び出し側(`app/(app)/search/page.tsx`)が持ち、本体は props 駆動。
 */

const HEADING_STYLE: CSSProperties = {
  fontSize: 10.5,
  fontWeight: 600,
  color: "var(--pr-text-muted)",
  letterSpacing: "0.4px",
  padding: "0 10px 6px",
};

function facetRowStyle(selected: boolean): CSSProperties {
  return {
    display: "flex",
    alignItems: "center",
    gap: 8,
    padding: "6px 10px",
    borderRadius: 6,
    width: "100%",
    background: selected ? "var(--pr-acc-s)" : "transparent",
    color: selected ? "var(--pr-acc)" : "inherit",
    fontWeight: selected ? 600 : 400,
    border: "none",
    cursor: "pointer",
    fontFamily: "inherit",
    fontSize: "inherit",
    textAlign: "left",
  };
}

function PulseBar({ width }: { width: number }) {
  return (
    <span
      aria-hidden="true"
      style={{
        display: "inline-block",
        width,
        height: 10,
        borderRadius: 3,
        background: "var(--pr-bg-muted)",
        animation: "yk-pulse 1.2s ease-in-out infinite",
      }}
    />
  );
}

const SOURCE_FACET_ITEMS: { value: SearchSourceFilter; label: string }[] = [
  { value: "all", label: "すべて" },
  { value: "body", label: "本文(原文・訳文)" },
  { value: "notes", label: "メモ・注釈" },
  { value: "chat", label: "チャット履歴" },
  { value: "article", label: "記事" },
];

export interface SearchFacetRailProps {
  source: SearchSourceFilter;
  paper: string | null;
  facets: SearchFacets | null;
  mode: "ready" | "loading" | "error" | "empty";
  onSourceChange: (s: SearchSourceFilter) => void;
  onPaperChange: (libraryItemId: string | null) => void;
}

/** 左ファセットレール(4e §4.3): ヒット源 5 種+論文で絞る。 */
export function SearchFacetRail({
  source,
  paper,
  facets,
  mode,
  onSourceChange,
  onPaperChange,
}: SearchFacetRailProps) {
  const countFor = (n: number | undefined) => {
    if (mode === "loading") return <PulseBar width={14} />;
    if (mode === "error" || n == null) return null;
    return n;
  };

  return (
    <div
      style={{
        width: 216,
        flex: "none",
        background: "var(--pr-bg-pane)",
        borderRight: "1px solid var(--pr-border-pane)",
        padding: "14px 10px",
        display: "flex",
        flexDirection: "column",
        gap: 2,
        fontSize: 12,
        color: "var(--pr-text-nav)",
      }}
    >
      <div style={HEADING_STYLE}>ヒット源</div>
      {mode !== "empty"
        ? SOURCE_FACET_ITEMS.map((item) => {
            const selected = source === item.value;
            const count = facets?.source[item.value];
            return (
              <button
                key={item.value}
                type="button"
                aria-pressed={selected}
                onClick={() => onSourceChange(item.value)}
                style={facetRowStyle(selected)}
              >
                <span style={{ flex: 1 }}>{item.label}</span>
                <span style={{ fontSize: 10.5, color: selected ? "inherit" : "var(--pr-text-muted)" }}>
                  {countFor(count)}
                </span>
              </button>
            );
          })
        : null}
      <div style={{ ...HEADING_STYLE, marginTop: mode !== "empty" ? 10 : 0 }}>論文で絞る</div>
      {mode === "ready" && facets
        ? facets.papers.map((p) => {
            const selected = paper === p.library_item_id;
            return (
              <button
                key={p.library_item_id}
                type="button"
                aria-pressed={selected}
                onClick={() => onPaperChange(selected ? null : p.library_item_id)}
                style={facetRowStyle(selected)}
              >
                <span
                  style={{
                    flex: 1,
                    whiteSpace: "nowrap",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                  }}
                >
                  {p.title}
                </span>
                <span style={{ fontSize: 10.5, color: selected ? "inherit" : "var(--pr-text-muted)" }}>
                  {p.count}
                </span>
              </button>
            );
          })
        : null}
      <div style={{ flex: 1 }} />
      <div style={{ fontSize: 10, color: "var(--pr-text-muted)", lineHeight: 1.7, padding: "0 10px 4px" }}>
        日本語クエリは訳文に、英語クエリは原文にヒットします(クロス検索)
      </div>
    </div>
  );
}

const SORT_LABELS: Record<SearchSortOption, string> = { relevance: "関連度", recency: "新しい順" };

export interface SearchSummaryBarProps {
  q: string;
  total: number;
  paperCount: number;
  sort: SearchSortOption;
  loading: boolean;
  onSortChange: (s: SearchSortOption) => void;
}

/** 結果サマリ行(4e §4.4): 件数+並びセレクタ。 */
export function SearchSummaryBar({
  q,
  total,
  paperCount,
  sort,
  loading,
  onSortChange,
}: SearchSummaryBarProps) {
  const anchorRef = useRef<HTMLButtonElement>(null);
  const [open, setOpen] = useState(false);

  if (loading) {
    return <PulseBar width={260} />;
  }

  return (
    <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
      <span style={{ fontSize: 13, color: "var(--pr-text-mid)" }}>
        「<b>{q}</b>」の結果 <b>{total} 件</b> · {paperCount} 論文
      </span>
      <span style={{ flex: 1 }} />
      <button
        ref={anchorRef}
        type="button"
        onClick={() => setOpen((v) => !v)}
        style={{
          display: "flex",
          alignItems: "center",
          gap: 4,
          fontSize: 11.5,
          color: "var(--pr-text-sub)",
          background: "transparent",
          border: "none",
          cursor: "pointer",
          fontFamily: "inherit",
        }}
      >
        並び: {SORT_LABELS[sort]}
        <span style={{ fontSize: 9, color: "var(--pr-text-muted)" }}>▾</span>
      </button>
      <Popover
        open={open}
        onClose={() => setOpen(false)}
        anchorRef={anchorRef}
        width={180}
        placement="bottom-end"
        caret={false}
      >
        <div role="menu" style={{ padding: 4 }}>
          {(["relevance", "recency"] as const).map((s) => (
            <button
              key={s}
              type="button"
              role="menuitemradio"
              aria-checked={s === sort}
              onClick={() => {
                onSortChange(s);
                setOpen(false);
              }}
              style={{
                display: "block",
                width: "100%",
                textAlign: "left",
                padding: "7px 12px",
                fontSize: 11.5,
                color: s === sort ? "var(--pr-acc)" : "var(--pr-text-mid)",
                fontWeight: s === sort ? 600 : 400,
                background: "transparent",
                border: "none",
                cursor: "pointer",
                fontFamily: "inherit",
              }}
            >
              {s === sort ? "✓ " : ""}
              {SORT_LABELS[s]}
            </button>
          ))}
        </div>
      </Popover>
    </div>
  );
}

export interface SearchHitRowProps {
  hit: SearchHit;
  q: string;
}

/** ヒット行(4e §4.5): ソースバッジ+スニペット+メタ行+ジャンプリンク。行全体が <a>。 */
export function SearchHitRow({ hit, q }: SearchHitRowProps) {
  const [hovered, setHovered] = useState(false);
  const badges = resultsBadges(hit);
  const href = hrefForSearchTarget(hit.target, q);

  return (
    <a
      href={href}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={{
        display: "flex",
        gap: 10,
        padding: "8px 10px",
        borderRadius: 7,
        textDecoration: "none",
        color: "inherit",
        background: hovered ? "var(--pr-bg-hover)" : "transparent",
      }}
    >
      <span style={{ display: "flex", flexDirection: "column", gap: 3, flex: "none", marginTop: 2 }}>
        {badges.map((b) => (
          <SourceBadge key={b.label} tone={b.tone} label={b.label} size="sm" />
        ))}
      </span>
      <span style={{ display: "flex", flexDirection: "column", gap: 3, minWidth: 0, flex: 1 }}>
        <span
          style={{
            fontSize: 11.5,
            lineHeight: 1.7,
            color: "var(--pr-text-en)",
            fontFamily: snippetFontVar(hit),
          }}
          // サーバーがサニタイズ済み HTML(<mark class="yk-search-hit"> のみ。plans/03 §15.1)。
          dangerouslySetInnerHTML={{ __html: hit.snippet }}
        />
        <span style={{ fontSize: 10, color: "var(--pr-text-muted)" }}>
          {hit.display}
          <span style={{ color: "var(--pr-acc)", fontWeight: 600, marginLeft: 6 }}>
            {jumpLabelForTarget(hit.target.kind)}
          </span>
        </span>
      </span>
    </a>
  );
}

export interface SearchGroupCardProps {
  group: SearchGroup;
  q: string;
}

/** 論文単位グループカード(4e §4.5)。記事のみのグループはヘッダ表記が切り替わる。 */
export function SearchGroupCard({ group, q }: SearchGroupCardProps) {
  const isArticleOnly = group.article != null && group.hits.every((h) => h.source === "article");
  const paper = group.library_item.paper;
  const authorsLine = [cardAuthors(paper.authors_short), paper.venue].filter(Boolean).join(" · ");

  return (
    <Card as="article" style={{ display: "flex", flexDirection: "column" }}>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 10,
          padding: "10px 16px",
          borderBottom: "1px solid var(--pr-border-hair)",
          background: "var(--pr-bg-feed)",
        }}
      >
        <div
          style={{
            width: 24,
            height: 32,
            borderRadius: 3,
            background: "var(--pr-bg-thumb)",
            border: "1px solid var(--pr-border-thumb)",
            flex: "none",
            overflow: "hidden",
          }}
        >
          {group.library_item.thumbnail_url ? (
            // 他コンポーネント(LibraryCard 等)と同一方針で <img> のまま使う。
            <img
              src={group.library_item.thumbnail_url}
              alt=""
              style={{ width: "100%", height: "100%", objectFit: "cover" }}
            />
          ) : null}
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 2, minWidth: 0 }}>
          <div style={{ fontSize: 12.5, fontWeight: 700 }}>
            {isArticleOnly ? `記事: ${group.article?.title}` : paper.title}
          </div>
          <div style={{ fontSize: 10.5, color: "var(--pr-text-muted)" }}>
            {isArticleOnly && group.article
              ? `記事(自動構成) · ${formatArticleDate(group.article.generated_at)}`
              : authorsLine}
          </div>
        </div>
        {!isArticleOnly ? (
          <StatusPill
            status={toReadingStatus(group.library_item.status)}
            size="sm"
            variant="pill"
            interactive={false}
          />
        ) : null}
        <span style={{ marginLeft: "auto", fontSize: 10.5, color: "var(--pr-text-muted)", flex: "none" }}>
          {group.hit_count} 件
        </span>
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 2, padding: "6px 16px 12px" }}>
        {group.hits.map((hit, i) => (
          <SearchHitRow key={`${hit.target.kind}-${hit.display}-${i}`} hit={hit} q={q} />
        ))}
      </div>
    </Card>
  );
}

function SearchResultsSkeleton({ count = 2 }: { count?: number }) {
  return (
    <>
      {Array.from({ length: count }).map((_, i) => (
        <Card key={i} as="article">
          <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "10px 16px" }}>
            <span
              aria-hidden="true"
              style={{
                width: 24,
                height: 32,
                borderRadius: 3,
                background: "var(--pr-bg-muted)",
                animation: "yk-pulse 1.2s ease-in-out infinite",
                flex: "none",
              }}
            />
            <div style={{ display: "flex", flexDirection: "column", gap: 6, flex: 1 }}>
              <PulseBar width={160} />
              <PulseBar width={80} />
            </div>
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 8, padding: "10px 16px" }}>
            {Array.from({ length: 3 }).map((_row, j) => (
              <div key={j} style={{ display: "flex", gap: 10 }}>
                <span
                  aria-hidden="true"
                  style={{
                    width: 48,
                    height: 16,
                    borderRadius: 3,
                    background: "var(--pr-bg-muted)",
                    animation: "yk-pulse 1.2s ease-in-out infinite",
                    flex: "none",
                  }}
                />
                <div style={{ display: "flex", flexDirection: "column", gap: 4, flex: 1 }}>
                  <PulseBar width={280} />
                  <PulseBar width={140} />
                </div>
              </div>
            ))}
          </div>
        </Card>
      ))}
    </>
  );
}

export interface SearchResultsProps {
  /** トリム済みの確定クエリ(空文字 = 未入力状態)。 */
  q: string;
  source: SearchSourceFilter;
  paper: string | null;
  sort: SearchSortOption;
  facets: SearchFacets | null;
  total: number;
  paperCount: number;
  groups: SearchGroup[];
  isPending: boolean;
  isError: boolean;
  isPlaceholderData: boolean;
  hasNextPage: boolean;
  isFetchingNextPage: boolean;
  onSourceChange: (s: SearchSourceFilter) => void;
  onPaperChange: (libraryItemId: string | null) => void;
  onSortChange: (s: SearchSortOption) => void;
  onRetry: () => void;
  onLoadMore: () => void;
}

/** 全結果画面の本体(4e §3〜5)。ファセットレール+結果カラムの 2 カラム構造。 */
export function SearchResults({
  q,
  source,
  paper,
  sort,
  facets,
  total,
  paperCount,
  groups,
  isPending,
  isError,
  isPlaceholderData,
  hasNextPage,
  isFetchingNextPage,
  onSourceChange,
  onPaperChange,
  onSortChange,
  onRetry,
  onLoadMore,
}: SearchResultsProps) {
  const sentinelRef = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const empty = q.length === 0;

  useEffect(() => {
    const el = sentinelRef.current;
    const root = listRef.current;
    if (!el || !root || !hasNextPage) return;
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries[0]?.isIntersecting) onLoadMore();
      },
      { root, rootMargin: "300px" },
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, [hasNextPage, onLoadMore]);

  const railMode: SearchFacetRailProps["mode"] = empty
    ? "empty"
    : isError
      ? "error"
      : isPending
        ? "loading"
        : "ready";

  return (
    <div style={{ flex: 1, height: "100%", display: "flex", minHeight: 0 }}>
      <SearchFacetRail
        source={source}
        paper={paper}
        facets={facets}
        mode={railMode}
        onSourceChange={onSourceChange}
        onPaperChange={onPaperChange}
      />
      <div
        style={{
          flex: 1,
          minWidth: 0,
          padding: "16px 26px",
          display: "flex",
          flexDirection: "column",
          gap: 12,
          overflow: "hidden",
        }}
      >
        {empty ? (
          <EmptyState
            title="検索語を入力してください"
            description="⌘K でライブラリ全体を検索できます — 本文・訳文・メモ・チャット・記事"
          />
        ) : (
          <>
            <SearchSummaryBar
              q={q}
              total={total}
              paperCount={paperCount}
              sort={sort}
              loading={isPending}
              onSortChange={onSortChange}
            />
            <div
              ref={listRef}
              style={{
                flex: 1,
                display: "flex",
                flexDirection: "column",
                gap: 14,
                overflowY: "auto",
                minHeight: 0,
                paddingBottom: 8,
                opacity: isPlaceholderData ? 0.55 : 1,
                pointerEvents: isPlaceholderData ? "none" : "auto",
              }}
            >
              {isPending ? (
                <SearchResultsSkeleton />
              ) : isError ? (
                <EmptyState
                  title="検索に失敗しました"
                  description="通信状態を確認してもう一度お試しください"
                  action={{ label: "再試行", onClick: onRetry }}
                />
              ) : groups.length === 0 ? (
                <EmptyState
                  title={`「${q}」に一致する結果はありません`}
                  description="別の言い回しを試してください。日本語クエリは訳文に、英語クエリは原文にヒットします(クロス検索)"
                />
              ) : (
                <>
                  {groups.map((g) => (
                    <SearchGroupCard key={g.library_item.id} group={g} q={q} />
                  ))}
                  {isFetchingNextPage ? <SearchResultsSkeleton count={1} /> : null}
                  {hasNextPage ? <div ref={sentinelRef} style={{ height: 1 }} /> : null}
                </>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
