"use client";

import type { CSSProperties } from "react";
import type { DeadlineCollectionEntry, DeadlineItemEntry } from "@yakudoku/api-client";
import { Card } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { ProgressBar } from "@/components/ui/ProgressBar";
import { formatShortDate } from "@/components/library/format";

/** 締切ステータス表示マップ(1d §3.3 の決定)。 */
const STATUS_LABELS: Record<string, string> = {
  planned: "未着手",
  up_next: "未着手",
  reading: "読書中",
  on_hold: "読書中",
  done: "読了",
  reread: "再読予定",
};

/**
 * 「締切が近い」セクション(plans/09-screens/1d-dashboard.md §4.7・docs/06 §6.3。M2-09 で有効化)。
 * `GET /api/dashboard` の `deadlines`(collections 最大2・items 最大3。実データ化は
 * apps/api/src/yakudoku_api/services/deadlines.py)を消費するだけの表示コンポーネント。
 */
export interface DashboardDeadlinesProps {
  collections: DeadlineCollectionEntry[];
  items: DeadlineItemEntry[];
  onOpenCollection: (id: string) => void;
  onOpenItem: (libraryItemId: string) => void;
}

export function DashboardDeadlines({
  collections,
  items,
  onOpenCollection,
  onOpenItem,
}: DashboardDeadlinesProps) {
  const empty = collections.length === 0 && items.length === 0;

  return (
    <section style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <h2 style={{ fontSize: 12, fontWeight: 700, color: "var(--pr-text-sub)", margin: 0 }}>
        締切が近い
      </h2>
      {empty ? (
        <EmptyState title="締切はありません" />
      ) : (
        <Card style={{ padding: "12px 14px", display: "flex", flexDirection: "column", gap: 11 }}>
          {collections.map((c, i) => (
            <div key={c.id}>
              {i > 0 ? <Divider /> : null}
              <CollectionBlock collection={c} onOpen={() => onOpenCollection(c.id)} />
            </div>
          ))}
          {items.map((it, i) => (
            <div key={it.library_item_id}>
              {i > 0 || collections.length > 0 ? <Divider /> : null}
              <ItemBlock item={it} onOpen={() => onOpenItem(it.library_item_id)} />
            </div>
          ))}
        </Card>
      )}
    </section>
  );
}

function Divider() {
  return <div style={{ height: 1, background: "var(--pr-border-soft)", margin: "11px 0" }} />;
}

function CollectionBlock({
  collection,
  onOpen,
}: {
  collection: DeadlineCollectionEntry;
  onOpen: () => void;
}) {
  const pct =
    collection.total_count === 0
      ? 0
      : Math.round((collection.done_count / collection.total_count) * 100);
  const daysLeftLabel =
    collection.days_left === 0
      ? "今日が締切"
      : collection.days_left < 0
        ? "期限超過"
        : `残り ${collection.days_left} 日`;

  return (
    <button type="button" onClick={onOpen} style={blockButtonStyle}>
      <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
        <span style={{ fontSize: 12.5, fontWeight: 600 }}>{collection.name}</span>
        <span style={daysLeftBadgeStyle}>{daysLeftLabel}</span>
      </div>
      <div style={{ fontSize: 10.5, color: "var(--pr-text-sub2)" }}>
        コレクション · {collection.total_count} 本中 {collection.done_count} 本読了
      </div>
      <ProgressBar value={pct} color="green" height={3} />
    </button>
  );
}

function ItemBlock({ item, onOpen }: { item: DeadlineItemEntry; onOpen: () => void }) {
  const statusLabel = STATUS_LABELS[item.status] ?? item.status;
  const isUnstartedStatus = item.status === "planned" || item.status === "up_next";
  const deadlineShort = formatShortDate(item.deadline);
  const metaParts = [item.assignee_self ? "担当発表" : null, `締切 ${deadlineShort}`].filter(
    Boolean,
  );

  return (
    <button type="button" onClick={onOpen} style={blockButtonStyle}>
      <div style={{ fontSize: 12, fontWeight: 600, lineHeight: 1.5 }}>{item.title}</div>
      <div style={{ fontSize: 10.5, color: "var(--pr-text-sub2)" }}>
        {metaParts.join(" · ")} ·{" "}
        <span style={isUnstartedStatus ? unstartedLabelStyle : undefined}>{statusLabel}</span>
      </div>
    </button>
  );
}

const blockButtonStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 6,
  width: "100%",
  border: "none",
  background: "transparent",
  padding: 0,
  textAlign: "left",
  cursor: "pointer",
  fontFamily: "inherit",
};

const daysLeftBadgeStyle: CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  height: 17,
  padding: "0 6px",
  borderRadius: 3,
  background: "var(--pr-warn-bg)",
  color: "var(--pr-warn)",
  fontSize: 9.5,
  fontWeight: 700,
};

const unstartedLabelStyle: CSSProperties = {
  color: "var(--pr-warn)",
  fontWeight: 600,
};
