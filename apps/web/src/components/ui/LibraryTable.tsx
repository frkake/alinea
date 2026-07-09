"use client";

import { useRef, useState, type CSSProperties, type KeyboardEvent } from "react";
import { STATUS_COLORS, STATUS_LABELS, type ReadingStatus } from "@alinea/tokens";
import { StatusPill } from "@/components/ui/StatusPill";
import { QualityBadge } from "@/components/ui/QualityBadge";
import { TagChip } from "@/components/ui/TagChip";
import { PriorityBadge } from "@/components/ui/PriorityBadge";
import { DeadlineBadge } from "@/components/ui/DeadlineBadge";
import { Popover } from "@/components/ui/Popover";

/** ライブラリ専用テーブル(plans/08 §5.15、plans/09 1e §2.6)。11 列固定。 */
export interface LibraryTableRow {
  id: string;
  title: string;
  titleBadge?: "pdf_import";
  authorsLine: string;
  thumbnailUrl: string | null;
  status: ReadingStatus;
  quality: "A" | "B";
  tags: string[];
  priority: "high" | "mid" | "low" | null;
  deadline: string | null;
  readingHours: number | null;
  comprehension: number | null;
  addedAt: string;
}

export type SortKey =
  | "title"
  | "status"
  | "quality"
  | "priority"
  | "deadline"
  | "reading_time"
  | "comprehension"
  | "added_at"
  | "updated_at";

export interface LibraryTableProps {
  rows: LibraryTableRow[];
  selectedIds: ReadonlySet<string>;
  onToggleSelect: (id: string) => void;
  onToggleSelectAll: () => void;
  sort: { key: SortKey; dir: "asc" | "desc" };
  onSortChange: (s: { key: SortKey; dir: "asc" | "desc" }) => void;
  onOpenRow: (id: string) => void;
  /**
   * ステータスセルを interactive にする(1e §4.7・M1 統合ポリッシュ)。未指定時は
   * `StatusPill(variant="dot-label")` の静的表示のみ(既存挙動を維持)。
   */
  onStatusChange?: (id: string, next: ReadingStatus) => void;
  onDeleteRow?: (row: Pick<LibraryTableRow, "id" | "title">) => void;
}

const GRID_COLUMNS = "34px 1fr 108px 44px 168px 64px 66px 76px 64px 64px 48px";

const STATUS_ORDER: readonly ReadingStatus[] = [
  "planned",
  "up_next",
  "reading",
  "done",
  "reread",
  "on_hold",
];

/**
 * ステータスセル(1e §4.7)。`onStatusChange` 指定時は `StatusPill(dot-label)` の見た目を
 * 保ったまま interactive にする(dot-label 自体は StatusPill 側の対象外バリアントのため、
 * ここでトリガー+メニューを自前で組む。plans/08 §5.2 の pill バリアントと同じメニュー構成)。
 */
function StatusCell({
  status,
  rowId,
  rowTitle,
  onStatusChange,
}: {
  status: ReadingStatus;
  rowId: string;
  rowTitle: string;
  onStatusChange?: (id: string, next: ReadingStatus) => void;
}) {
  const anchorRef = useRef<HTMLButtonElement>(null);
  const [open, setOpen] = useState(false);

  if (!onStatusChange) {
    return <StatusPill status={status} variant="dot-label" />;
  }

  return (
    <span onClick={(e) => e.stopPropagation()}>
      <button
        ref={anchorRef}
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={`${rowTitle} のステータスを変更`}
        onClick={() => setOpen((v) => !v)}
        style={{ border: "none", background: "transparent", padding: 0, cursor: "pointer", fontFamily: "inherit" }}
      >
        <StatusPill status={status} variant="dot-label" />
      </button>
      <Popover open={open} onClose={() => setOpen(false)} anchorRef={anchorRef} width={180} placement="bottom-start" caret={false}>
        <div role="menu" style={{ padding: 4 }}>
          {STATUS_ORDER.map((s) => (
            <button
              key={s}
              type="button"
              role="menuitemradio"
              aria-checked={s === status}
              onClick={(e) => {
                e.stopPropagation();
                setOpen(false);
                if (s !== status) onStatusChange(rowId, s);
              }}
              style={{
                display: "flex",
                alignItems: "center",
                gap: 6,
                width: "100%",
                padding: "6px 8px",
                border: "none",
                borderRadius: 6,
                background: s === status ? "var(--pr-acc-s)" : "transparent",
                color: s === status ? "var(--pr-acc)" : "var(--pr-text-mid)",
                fontSize: 11.5,
                fontWeight: s === status ? 600 : 400,
                cursor: "pointer",
                fontFamily: "inherit",
                textAlign: "left",
              }}
            >
              <span
                style={{
                  width: 7,
                  height: 7,
                  borderRadius: "50%",
                  background: STATUS_COLORS[s],
                  flex: "none",
                }}
              />
              {STATUS_LABELS[s]}
            </button>
          ))}
        </div>
      </Popover>
    </span>
  );
}

const HEADERS: Array<{ label: string; key?: SortKey }> = [
  { label: "論文", key: "title" },
  { label: "ステータス", key: "status" },
  { label: "品質", key: "quality" },
  { label: "タグ" },
  { label: "優先度", key: "priority" },
  { label: "締切", key: "deadline" },
  { label: "読書時間", key: "reading_time" },
  { label: "理解度", key: "comprehension" },
  { label: "追加日", key: "added_at" },
  { label: "操作" },
];

function Checkbox({
  checked,
  onToggle,
  ariaLabel,
}: {
  checked: boolean;
  onToggle: () => void;
  ariaLabel: string;
}) {
  const onKey = (e: KeyboardEvent<HTMLSpanElement>) => {
    if (e.key === " " || e.key === "Enter") {
      e.preventDefault();
      onToggle();
    }
  };
  return (
    <span
      role="checkbox"
      aria-checked={checked}
      aria-label={ariaLabel}
      tabIndex={0}
      onClick={(e) => {
        e.stopPropagation();
        onToggle();
      }}
      onKeyDown={onKey}
      style={{
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        width: 14,
        height: 14,
        border: checked ? "1.5px solid var(--pr-acc)" : "1.5px solid var(--pr-border-check)",
        borderRadius: 3,
        background: checked ? "var(--pr-acc)" : "transparent",
        color: "#FFFFFF",
        fontSize: 9,
        cursor: "pointer",
      }}
    >
      {checked ? "✓" : null}
    </span>
  );
}

const cellText = (mid: boolean): CSSProperties => ({
  fontSize: 11,
  color: mid ? "var(--pr-text-mid)" : "var(--pr-text-muted)",
});

export function LibraryTable({
  rows,
  selectedIds,
  onToggleSelect,
  onToggleSelectAll,
  sort,
  onSortChange,
  onOpenRow,
  onStatusChange,
  onDeleteRow,
}: LibraryTableProps) {
  const allSelected = rows.length > 0 && rows.every((r) => selectedIds.has(r.id));
  const sortGlyph = sort.dir === "asc" ? " ↑" : " ↓";

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        background: "var(--pr-bg-card)",
        border: "1px solid var(--pr-border-card)",
        borderRadius: 10,
        overflow: "hidden",
      }}
    >
      {/* ヘッダ行 */}
      <div
        role="row"
        style={{
          display: "grid",
          gridTemplateColumns: GRID_COLUMNS,
          alignItems: "center",
          gap: 8,
          padding: "8px 14px",
          borderBottom: "1px solid var(--pr-border-soft)",
          fontSize: 10.5,
          fontWeight: 600,
          color: "var(--pr-text-muted)",
        }}
      >
        <Checkbox checked={allSelected} onToggle={onToggleSelectAll} ariaLabel="すべて選択" />
        {HEADERS.map((h) => {
          const isSorted = h.key !== undefined && h.key === sort.key;
          if (h.key === undefined) {
            return <div key={h.label}>{h.label}</div>;
          }
          const key = h.key;
          return (
            <button
              key={h.label}
              type="button"
              onClick={() => {
                onSortChange({
                  key,
                  dir: isSorted && sort.dir === "asc" ? "desc" : "asc",
                });
              }}
              style={{
                border: "none",
                background: "transparent",
                padding: 0,
                textAlign: "left",
                cursor: "pointer",
                font: "inherit",
                color: "inherit",
              }}
            >
              {h.label}
              {isSorted ? sortGlyph : ""}
            </button>
          );
        })}
      </div>

      {/* 行 */}
      {rows.map((row, index) => {
        const selected = selectedIds.has(row.id);
        return (
          <div
            key={row.id}
            role="row"
            className="alinea-lib-row"
            onClick={() => {
              onOpenRow(row.id);
            }}
            style={{
              display: "grid",
              gridTemplateColumns: GRID_COLUMNS,
              alignItems: "center",
              gap: 8,
              padding: "8px 14px",
              borderBottom:
                index === rows.length - 1 ? undefined : "1px solid var(--pr-border-row)",
              background: selected ? "var(--pr-acc-s)" : undefined,
              cursor: "pointer",
            }}
          >
            <Checkbox
              checked={selected}
              onToggle={() => {
                onToggleSelect(row.id);
              }}
              ariaLabel={`${row.title} を選択`}
            />

            {/* 論文セル */}
            <div style={{ display: "flex", gap: 10, minWidth: 0, alignItems: "center" }}>
              <span
                style={{
                  width: 26,
                  height: 34,
                  flex: "none",
                  borderRadius: 2,
                  border: "1px solid var(--pr-border-thumb)",
                  background: row.thumbnailUrl
                    ? `center/cover no-repeat url(${row.thumbnailUrl})`
                    : "var(--pr-bg-thumb)",
                }}
              />
              <div style={{ minWidth: 0 }}>
                <div
                  style={{
                    fontSize: 12,
                    fontWeight: 600,
                    color: "var(--pr-text)",
                    whiteSpace: "nowrap",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    display: "flex",
                    alignItems: "center",
                    gap: 6,
                  }}
                >
                  <span
                    style={{
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                    }}
                  >
                    {row.title}
                  </span>
                  {row.titleBadge === "pdf_import" ? <TagChip size="card">PDF 取り込み</TagChip> : null}
                </div>
                <div
                  style={{
                    fontSize: 10,
                    color: "var(--pr-text-muted)",
                    whiteSpace: "nowrap",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                  }}
                >
                  {row.authorsLine}
                </div>
              </div>
            </div>

            {/* ステータス */}
            <div>
              <StatusCell
                status={row.status}
                rowId={row.id}
                rowTitle={row.title}
                onStatusChange={onStatusChange}
              />
            </div>

            {/* 品質 */}
            <div>
              <QualityBadge level={row.quality} size={17} />
            </div>

            {/* タグ */}
            <div style={{ display: "flex", gap: 4, overflow: "hidden" }}>
              {row.tags.map((tag) => (
                <TagChip key={tag}>{tag}</TagChip>
              ))}
            </div>

            {/* 優先度 */}
            <div>
              {row.priority ? (
                <PriorityBadge priority={row.priority} />
              ) : (
                <span style={cellText(false)}>—</span>
              )}
            </div>

            {/* 締切 */}
            <div>
              <DeadlineBadge date={row.deadline} variant="text" />
            </div>

            {/* 読書時間 */}
            <div style={cellText(row.readingHours !== null)}>
              {row.readingHours !== null ? `${row.readingHours}h` : "—"}
            </div>

            {/* 理解度 */}
            <div style={cellText(row.comprehension !== null)}>
              {row.comprehension !== null ? `${row.comprehension}/5` : "—"}
            </div>

            {/* 追加日 */}
            <div style={cellText(true)}>{row.addedAt}</div>

            {/* 操作 */}
            <div>
              {onDeleteRow ? (
                <button
                  type="button"
                  aria-label={row.title + " を削除"}
                  onClick={(e) => {
                    e.stopPropagation();
                    onDeleteRow({ id: row.id, title: row.title });
                  }}
                  style={{
                    height: 24,
                    padding: "0 8px",
                    border: "none",
                    borderRadius: 6,
                    background: "transparent",
                    color: "var(--pr-warn)",
                    fontSize: 11,
                    fontWeight: 600,
                    cursor: "pointer",
                    fontFamily: "inherit",
                  }}
                >
                  削除
                </button>
              ) : null}
            </div>
          </div>
        );
      })}

      {/* 最下段スペーサ */}
      <div style={{ flex: 1 }} />
    </div>
  );
}
