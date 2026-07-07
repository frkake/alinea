"use client";

import type { CSSProperties } from "react";
import type { LibraryItemSummary } from "@yakudoku/api-client";
import { Card } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { ProgressBar } from "@/components/ui/ProgressBar";
import { cardBibLine } from "@/components/library/format";

/**
 * 「続きを読む」セクション(plans/09-screens/1d-dashboard.md §4.4)。
 * `GET /api/dashboard` の `continue_reading`(status=reading、最大3件)を描画する。
 */
export interface ContinueReadingProps {
  items: LibraryItemSummary[];
  onOpen: (id: string) => void;
  /** モバイル縮退(mobile.md §5.2)。カード幅 100% の 1 カラムにする。 */
  isMobile?: boolean;
}

/** 前回読書の日単位相対表記(§3.3)。当日=今日/1日前=昨日/2〜6日前=n日前/7〜13日前=先週/14日以上=M/D。 */
export function formatRelativeDay(iso: string, now: Date = new Date()): string {
  const target = new Date(iso);
  const startOf = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const diffDays = Math.round((startOf(now) - startOf(target)) / 86_400_000);
  if (diffDays <= 0) return "今日";
  if (diffDays === 1) return "昨日";
  if (diffDays <= 6) return `${diffDays}日前`;
  if (diffDays <= 13) return "先週";
  return `${target.getMonth() + 1}/${target.getDate()}`;
}

export function ContinueReading({ items, onOpen, isMobile = false }: ContinueReadingProps) {
  return (
    <section style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <h2 style={{ fontSize: 12, fontWeight: 700, color: "var(--pr-text-sub)", margin: 0 }}>
        続きを読む
      </h2>
      {items.length === 0 ? (
        <EmptyState
          title="読みかけの論文はありません"
          description="ライブラリから論文を開くとここに表示されます"
        />
      ) : (
        <div style={{ display: "grid", gridTemplateColumns: isMobile ? "1fr" : "1fr 1fr 1fr", gap: 12 }}>
          {items.map((item) => (
            <ContinueReadingCard key={item.id} item={item} onOpen={onOpen} />
          ))}
        </div>
      )}
    </section>
  );
}

const clamp2: CSSProperties = {
  display: "-webkit-box",
  WebkitLineClamp: 2,
  WebkitBoxOrient: "vertical",
  overflow: "hidden",
};

function ContinueReadingCard({
  item,
  onOpen,
}: {
  item: LibraryItemSummary;
  onOpen: (id: string) => void;
}) {
  const lastPosition = item.last_position;
  return (
    <Card
      as="article"
      role="link"
      tabIndex={0}
      aria-label={item.paper.title}
      onClick={() => onOpen(item.id)}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onOpen(item.id);
        }
      }}
      style={{ display: "flex", gap: 12, padding: "12px 14px", cursor: "pointer" }}
    >
      {/* サムネイル(プレースホルダのみ。thumbnail_url は現状すべて null) */}
      <div
        style={{
          width: 56,
          height: 74,
          flex: "none",
          borderRadius: 5,
          background: "var(--pr-bg-thumb)",
          border: "1px solid var(--pr-border-thumb)",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          color: "var(--pr-text-thumb)",
          fontSize: 9,
          overflow: "hidden",
        }}
      >
        {item.thumbnail_url ? (
          <img
            src={item.thumbnail_url}
            alt=""
            style={{ width: "100%", height: "100%", objectFit: "cover" }}
          />
        ) : (
          "—"
        )}
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: 5, minWidth: 0, flex: 1 }}>
        <div style={{ fontSize: 12.5, fontWeight: 600, lineHeight: 1.5, ...clamp2 }}>
          {item.paper.title}
        </div>
        <div style={{ fontSize: 10.5, color: "var(--pr-text-muted)" }}>
          {cardBibLine(item.paper)}
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: 5, marginTop: "auto" }}>
          <ProgressBar value={item.progress_pct} color="accent" height={3} />
          <div style={{ display: "flex", alignItems: "center", fontSize: 10.5, color: "var(--pr-text-sub2)" }}>
            {lastPosition ? (
              <span>
                前回: {lastPosition.section_display} · {formatRelativeDay(lastPosition.saved_at)}
              </span>
            ) : null}
            {/* カード全体がクリック対象(role="link")のため、個別のハンドラは持たない */}
            <span style={{ marginLeft: "auto", color: "var(--pr-acc)", fontWeight: 700 }}>
              再開 →
            </span>
          </div>
        </div>
      </div>
    </Card>
  );
}
