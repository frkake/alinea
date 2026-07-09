"use client";

import type { CSSProperties, KeyboardEvent } from "react";
import { create } from "zustand";
import { persist } from "zustand/middleware";
import type { LibraryItemSummary } from "@alinea/api-client";
import { Card } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { PriorityBadge } from "@/components/ui/PriorityBadge";
import { DeadlineBadge } from "@/components/ui/DeadlineBadge";
import { cardBibLine, formatShortDate, toPriority } from "@/components/library/format";

/** キュー本数がこれ以上で「積みすぎかも?」警告(docs/06 §6.2)。 */
const WARN_THRESHOLD = 6;

/**
 * ダッシュボード画面のクライアント側 UI 状態(plans/09-screens/1d-dashboard.md §5.4)。
 * localStorage キー `alinea-dashboard-ui`(persist ミドルウェアの実キー名)。
 * 「× を押した時点の本数」を保存し、本数が変わると再表示される。
 */
interface DashboardUiState {
  queueWarnDismissedCount: number | null;
  dismissQueueWarn: (count: number) => void;
}

export const useDashboardUiStore = create<DashboardUiState>()(
  persist(
    (set) => ({
      queueWarnDismissedCount: null,
      dismissQueueWarn: (count: number) => {
        set({ queueWarnDismissedCount: count });
      },
    }),
    { name: "alinea-dashboard-ui" },
  ),
);

/**
 * 「すぐ読むキュー」セクション(§4.5)。並べ替えは dnd-kit を追加せず(依存追加禁止)、
 * 上下ボタン(HTML5 DnD の代替。決定・deviations 記載)で実現する。
 */
export interface UpNextQueueProps {
  items: LibraryItemSummary[];
  onOpen: (id: string) => void;
  /** 新しい並び順(全件)を確定する。呼び出し側が楽観更新+PUT queue-order を担う。 */
  onReorder: (items: LibraryItemSummary[]) => void;
  /** 「整理する」クリック(`/library?status=up_next` への遷移は呼び出し側)。 */
  onOrganize: () => void;
  /** モバイル縮退(mobile.md §1.2)。並べ替え(上下ボタン)を非描画にする。閲覧・遷移は維持。 */
  hideReorder?: boolean;
}

export function UpNextQueue({ items, onOpen, onReorder, onOrganize, hideReorder = false }: UpNextQueueProps) {
  const dismissedCount = useDashboardUiStore((s) => s.queueWarnDismissedCount);
  const dismissQueueWarn = useDashboardUiStore((s) => s.dismissQueueWarn);

  const count = items.length;
  const showWarning = count >= WARN_THRESHOLD && count !== dismissedCount;

  const move = (index: number, dir: -1 | 1) => {
    const target = index + dir;
    if (target < 0 || target >= items.length) return;
    const next = items.slice();
    const moved = next.splice(index, 1)[0];
    if (!moved) return;
    next.splice(target, 0, moved);
    onReorder(next);
  };

  return (
    <section style={{ display: "flex", flexDirection: "column", gap: 10, minHeight: 0 }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
        <h2 style={{ fontSize: 12, fontWeight: 700, color: "var(--pr-text-sub)", margin: 0 }}>
          すぐ読むキュー
        </h2>
        {count > 0 ? (
          <span style={{ fontSize: 10.5, color: "var(--pr-text-muted)" }}>
            {count} 本{hideReorder ? "" : " · 並べ替え可"}
          </span>
        ) : null}
      </div>

      {count === 0 ? (
        <EmptyState
          title="すぐ読むキューは空です"
          description="ライブラリで論文を「すぐ読む」にするとここに並びます"
        />
      ) : (
        <Card style={{ maxHeight: 196, overflowY: "auto" }}>
          {items.map((item, index) => (
            <QueueRow
              key={item.id}
              item={item}
              index={index}
              isLast={index === items.length - 1}
              onOpen={onOpen}
              hideReorder={hideReorder}
              onMoveUp={() => {
                move(index, -1);
              }}
              onMoveDown={() => {
                move(index, 1);
              }}
              disableUp={index === 0}
              disableDown={index === items.length - 1}
            />
          ))}
        </Card>
      )}

      {showWarning ? (
        <div role="status" style={warningStyle}>
          <span>{`キューが ${count} 本になっています — 積みすぎかも?`}</span>
          <button type="button" onClick={onOrganize} style={organizeButtonStyle}>
            整理する
          </button>
          <button
            type="button"
            aria-label="警告を閉じる"
            onClick={() => {
              dismissQueueWarn(count);
            }}
            style={closeButtonStyle}
          >
            ×
          </button>
        </div>
      ) : null}
    </section>
  );
}

interface QueueRowProps {
  item: LibraryItemSummary;
  index: number;
  isLast: boolean;
  onOpen: (id: string) => void;
  hideReorder?: boolean;
  onMoveUp: () => void;
  onMoveDown: () => void;
  disableUp: boolean;
  disableDown: boolean;
}

/**
 * 行全体ではなく上下ボタンをそれぞれ独立した操作対象にする(ネストした対話要素を避ける。
 * ドラッグハンドル以外はクリックで開く仕様=タイトルを開閉の主対象としてフォーカス可能にする)。
 */
function QueueRow({
  item,
  index,
  isLast,
  onOpen,
  hideReorder = false,
  onMoveUp,
  onMoveDown,
  disableUp,
  disableDown,
}: QueueRowProps) {
  const priority = toPriority(item.priority);
  const deadline = formatShortDate(item.deadline);

  const open = () => {
    onOpen(item.id);
  };
  const onKey = (e: KeyboardEvent<HTMLSpanElement>) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      open();
    }
  };

  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 10,
        padding: "9px 14px",
        borderBottom: isLast ? "none" : "1px solid var(--pr-border-hair)",
      }}
    >
      {/* 並べ替え(上下ボタン。deviations: dnd-kit の代替)。モバイルでは操作系のため非描画。 */}
      {hideReorder ? null : (
        <div style={{ display: "flex", flexDirection: "column", gap: 1, flex: "none" }}>
          <button
            type="button"
            aria-label="上へ移動"
            disabled={disableUp}
            onClick={onMoveUp}
            style={moveButtonStyle}
          >
            ▲
          </button>
          <button
            type="button"
            aria-label="下へ移動"
            disabled={disableDown}
            onClick={onMoveDown}
            style={moveButtonStyle}
          >
            ▼
          </button>
        </div>
      )}
      <span style={{ fontSize: 11, color: "var(--pr-text-muted)", width: 12, flex: "none" }}>
        {index + 1}
      </span>
      <span
        role="link"
        tabIndex={0}
        onClick={open}
        onKeyDown={onKey}
        style={{
          fontSize: 12.5,
          fontWeight: 600,
          flex: 1,
          minWidth: 0,
          whiteSpace: "nowrap",
          overflow: "hidden",
          textOverflow: "ellipsis",
          cursor: "pointer",
        }}
      >
        {item.paper.title}
      </span>
      <span style={{ fontSize: 10.5, color: "var(--pr-text-muted)", flex: "none" }}>
        {cardBibLine(item.paper)}
      </span>
      {priority ? <PriorityBadge priority={priority} variant="chip" /> : null}
      {deadline ? <DeadlineBadge date={deadline} variant="chip" withLabel /> : null}
    </div>
  );
}

const moveButtonStyle: CSSProperties = {
  border: "none",
  background: "transparent",
  color: "var(--pr-text-muted)",
  fontSize: 9,
  lineHeight: 1,
  padding: 0,
  cursor: "pointer",
  fontFamily: "inherit",
};

/* 警告バナー配色は plans/09-screens/1d-dashboard.md §4.5 の実測値(トークン未定義のため直接指定)。 */
const warningStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 7,
  fontSize: 10.5,
  color: "#8A6A24",
  background: "#FFF9F0",
  border: "1px solid #EEDDB8",
  borderRadius: 7,
  padding: "6px 11px",
};

const organizeButtonStyle: CSSProperties = {
  marginLeft: "auto",
  border: "none",
  background: "transparent",
  padding: 0,
  fontWeight: 600,
  color: "#8A6A24",
  cursor: "pointer",
  fontSize: 10.5,
  fontFamily: "inherit",
};

const closeButtonStyle: CSSProperties = {
  border: "none",
  background: "transparent",
  padding: 0,
  color: "#B8A26E",
  cursor: "pointer",
  fontSize: 12,
  fontFamily: "inherit",
  lineHeight: 1,
};
