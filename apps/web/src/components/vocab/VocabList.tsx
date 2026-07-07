"use client";

import { useRef, type CSSProperties, type KeyboardEvent, type UIEvent } from "react";
import type { VocabEntrySummary } from "@yakudoku/api-client";
import { Card } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { VocabKindBadge } from "@/components/vocab/VocabKindBadge";
import { formatAddedRelative } from "@/components/vocab/format";

const GRID_COLUMNS = "1.25fr 1.35fr 170px 56px";

export type VocabListSort = "added_at" | "term";

export interface VocabListProps {
  entries: VocabEntrySummary[];
  selectedId: string | null;
  sort: VocabListSort;
  onSortChange: (sort: VocabListSort) => void;
  onSelect: (id: string) => void;
  onReachEnd: () => void;
  isFetchingNextPage: boolean;
  /** `null` = 0 件表示なし(通常時)。 */
  emptyVariant: "no-entries" | "no-match" | null;
  onClearFilters: () => void;
  isLoading?: boolean;
  isError?: boolean;
  onRetry?: () => void;
}

/** 語彙リスト(4d §4.2.5)。マスター/ディテールの左カード。 */
export function VocabList({
  entries,
  selectedId,
  sort,
  onSortChange,
  onSelect,
  onReachEnd,
  isFetchingNextPage,
  emptyVariant,
  onClearFilters,
  isLoading = false,
  isError = false,
  onRetry,
}: VocabListProps) {
  const reachedRef = useRef(false);

  const onScroll = (e: UIEvent<HTMLDivElement>) => {
    const el = e.currentTarget;
    const distanceToEnd = el.scrollHeight - el.scrollTop - el.clientHeight;
    if (distanceToEnd <= 200) {
      if (!reachedRef.current) {
        reachedRef.current = true;
        onReachEnd();
      }
    } else {
      reachedRef.current = false;
    }
  };

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
    if (entries.length === 0) return;
    e.preventDefault();
    const idx = entries.findIndex((it) => it.id === selectedId);
    const nextIdx = e.key === "ArrowDown" ? idx + 1 : idx - 1;
    if (nextIdx < 0 || nextIdx >= entries.length) return; // ラップしない
    const next = entries[nextIdx];
    if (next) onSelect(next.id);
  };

  return (
    <Card
      style={{ flex: 1, display: "flex", flexDirection: "column", minWidth: 0 }}
    >
      <ListHeader sort={sort} onSortChange={onSortChange} />

      {isError ? (
        <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center" }}>
          <EmptyState
            title="語彙帳を読み込めませんでした"
            action={onRetry ? { label: "再読み込み", onClick: onRetry } : undefined}
          />
        </div>
      ) : isLoading ? (
        <div style={{ flex: 1, overflow: "hidden" }}>
          {Array.from({ length: 8 }, (_, i) => (
            <SkeletonRow key={i} />
          ))}
        </div>
      ) : emptyVariant === "no-entries" ? (
        <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center" }}>
          <EmptyState
            title="まだ語彙がありません"
            description="ビューアで本文(英語原文)を選択し、「語彙に追加」を選ぶと、文脈センテンスごとここに保存されます。"
          />
        </div>
      ) : emptyVariant === "no-match" ? (
        <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center" }}>
          <EmptyState
            title="該当する語彙がありません"
            description="フィルタか検索語を変えてみてください。"
            action={{ label: "絞り込みを解除", onClick: onClearFilters }}
          />
        </div>
      ) : (
        <div
          role="listbox"
          aria-label="語彙リスト"
          tabIndex={0}
          onKeyDown={onKeyDown}
          onScroll={onScroll}
          style={{ flex: 1, overflowY: "auto", outline: "none" }}
        >
          {entries.map((entry) => (
            <VocabListRow
              key={entry.id}
              entry={entry}
              selected={entry.id === selectedId}
              onClick={() => onSelect(entry.id)}
            />
          ))}
          <div style={{ flex: 1 }} />
          {isFetchingNextPage ? (
            <div style={{ padding: "10px 16px", fontSize: 10.5, color: "var(--pr-text-muted)" }}>
              読み込み中…
            </div>
          ) : null}
        </div>
      )}

      <div
        style={{
          padding: "9px 16px",
          borderTop: "1px solid var(--pr-border-soft)",
          fontSize: 10.5,
          color: "var(--pr-text-muted)",
          flex: "none",
        }}
      >
        語義・語源・コツは保存時に文脈から自動生成されます(編集可)· 復習は忘却曲線に沿って出題
      </div>
    </Card>
  );
}

function ListHeader({
  sort,
  onSortChange,
}: {
  sort: VocabListSort;
  onSortChange: (sort: VocabListSort) => void;
}) {
  const cellStyle: CSSProperties = { fontSize: 10.5, fontWeight: 600, color: "var(--pr-text-muted)" };
  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: GRID_COLUMNS,
        alignItems: "center",
        gap: 8,
        padding: "8px 16px",
        borderBottom: "1px solid var(--pr-border-soft)",
        flex: "none",
      }}
    >
      <button
        type="button"
        onClick={() => onSortChange("term")}
        style={{ ...cellStyle, background: "none", border: "none", textAlign: "left", cursor: "pointer", padding: 0, fontFamily: "inherit" }}
      >
        語彙{sort === "term" ? " ↑" : ""}
      </button>
      <span style={{ ...cellStyle, cursor: "default" }}>文脈での語義</span>
      <span style={{ ...cellStyle, cursor: "default" }}>出典</span>
      <button
        type="button"
        onClick={() => onSortChange("added_at")}
        style={{ ...cellStyle, background: "none", border: "none", textAlign: "left", cursor: "pointer", padding: 0, fontFamily: "inherit" }}
      >
        追加{sort === "added_at" ? " ↓" : ""}
      </button>
    </div>
  );
}

function meaningCellContent(entry: VocabEntrySummary): { text: string; color: string } {
  if (entry.generation === "pending") {
    return { text: "生成中…", color: "var(--pr-text-muted)" };
  }
  if (entry.generation === "failed") {
    return { text: "生成に失敗 — 再試行できます", color: "var(--pr-warn)" };
  }
  return { text: entry.meaning_short ?? "", color: "#3C4046" };
}

function VocabListRow({
  entry,
  selected,
  onClick,
}: {
  entry: VocabEntrySummary;
  selected: boolean;
  onClick: () => void;
}) {
  const meaning = meaningCellContent(entry);
  return (
    <div
      role="option"
      aria-selected={selected}
      onClick={onClick}
      style={{
        display: "grid",
        gridTemplateColumns: GRID_COLUMNS,
        alignItems: "center",
        gap: 8,
        padding: "10px 16px",
        borderBottom: "1px solid var(--pr-border-row)",
        background: selected ? "var(--pr-acc-s)" : "transparent",
        cursor: "pointer",
      }}
    >
      <span style={{ display: "flex", alignItems: "center", gap: 7, minWidth: 0 }}>
        <span
          style={{
            fontSize: 12.5,
            fontWeight: 600,
            fontFamily: "var(--pr-font-en)",
            whiteSpace: "nowrap",
            overflow: "hidden",
            textOverflow: "ellipsis",
          }}
        >
          {entry.term}
        </span>
        <VocabKindBadge kind={entry.kind} size="list" />
      </span>
      <span
        style={{
          fontSize: 11.5,
          color: meaning.color,
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
        }}
      >
        {meaning.text}
      </span>
      <span
        style={{
          fontSize: 10,
          color: "var(--pr-text-muted)",
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
        }}
      >
        {entry.source.display}
      </span>
      <span style={{ fontSize: 10, color: "var(--pr-text-muted)" }}>
        {formatAddedRelative(entry.added_at)}
      </span>
    </div>
  );
}

function SkeletonRow() {
  const block = (w: number, h: number): CSSProperties => ({
    width: w,
    height: h,
    borderRadius: 3,
    background: "var(--pr-bg-muted)",
    animation: "yk-pulse 1.2s ease-in-out infinite",
  });
  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: GRID_COLUMNS,
        alignItems: "center",
        gap: 8,
        padding: "10px 16px",
        borderBottom: "1px solid var(--pr-border-row)",
      }}
    >
      <span style={block(120, 12)} />
      <span style={block(220, 11)} />
      <span style={block(110, 10)} />
      <span style={block(32, 10)} />
    </div>
  );
}
