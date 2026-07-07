"use client";

import { useRef, useState, type CSSProperties } from "react";
import { STATUS_COLORS, STATUS_LABELS, type ReadingStatus } from "@yakudoku/tokens";
import { Card } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { Popover } from "@/components/ui/Popover";
import { ProgressBar } from "@/components/ui/ProgressBar";
import { toReadingStatus } from "@/components/library/format";
import { formatSubLine, isUnstarted } from "@/components/collections/format";
import { AddPaperPopover } from "@/components/collections/AddPaperPopover";
import type { CollectionEntry, EntryPatch } from "@/components/collections/types";

/**
 * コレクションの論文リスト(plans/09-screens/4b §4.3.2)。
 * 並べ替えは dnd-kit を追加せず(依存追加禁止。UpNextQueue.tsx と同方針)、上下ボタンで実現する
 * (決定・deviations 記載)。オーバーフローメニュー「⋯」は簡略化し、「編集」「外す」ボタンを
 * 行内に直接出す(機能は §5.7 と同等。「開く」は行クリックで代替。決定・deviations 記載)。
 * 「+ 論文を追加」ポップオーバー(AddPaperPopover)はヘッダーボタンをアンカーに本コンポーネントが
 * 内包する(決定: ページ側では追加済み判定用の existingLibraryItemIds を渡すのみ)。
 */
export interface CollectionEntryListProps {
  entries: CollectionEntry[];
  onOpen: (libraryItemId: string) => void;
  onReorder: (entryIds: string[]) => void;
  onAddEntry: (libraryItemId: string) => void;
  onPatchEntry: (entryId: string, patch: EntryPatch) => void;
  onRemoveEntry: (entryId: string) => void;
}

export function CollectionEntryList({
  entries,
  onOpen,
  onReorder,
  onAddEntry,
  onPatchEntry,
  onRemoveEntry,
}: CollectionEntryListProps) {
  const [addOpen, setAddOpen] = useState(false);
  const addAnchorRef = useRef<HTMLButtonElement>(null);
  const existingIds = new Set(entries.map((e) => e.library_item.id));

  const move = (index: number, dir: -1 | 1) => {
    const target = index + dir;
    if (target < 0 || target >= entries.length) return;
    const next = entries.slice();
    const moved = next.splice(index, 1)[0];
    if (!moved) return;
    next.splice(target, 0, moved);
    onReorder(next.map((e) => e.id));
  };

  const openAdd = () => setAddOpen(true);

  return (
    <Card
      as="section"
      style={{ display: "flex", flexDirection: "column", flex: 1, minHeight: 0 }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          padding: "9px 16px",
          borderBottom: "1px solid var(--pr-border-soft)",
          fontSize: 10.5,
          fontWeight: 600,
          color: "var(--pr-text-muted)",
        }}
      >
        <span>発表順 — 上下ボタンで並べ替え</span>
        <button ref={addAnchorRef} type="button" onClick={openAdd} style={headerButtonStyle}>
          + 論文を追加
        </button>
      </div>

      <AddPaperPopover
        open={addOpen}
        onClose={() => setAddOpen(false)}
        anchorRef={addAnchorRef}
        existingLibraryItemIds={existingIds}
        onSelect={onAddEntry}
      />

      {entries.length === 0 ? (
        <EmptyState
          title="まだ論文がありません"
          description="「+ 論文を追加」でライブラリの論文をこのコレクションに追加できます。"
          action={{ label: "+ 論文を追加", onClick: openAdd }}
        />
      ) : (
        <div style={{ overflowY: "auto", flex: 1 }}>
          {entries.map((entry, index) => (
            <EntryRow
              key={entry.id}
              entry={entry}
              isLast={index === entries.length - 1}
              onOpen={onOpen}
              onMoveUp={() => {
                move(index, -1);
              }}
              onMoveDown={() => {
                move(index, 1);
              }}
              disableUp={index === 0}
              disableDown={index === entries.length - 1}
              onPatch={(patch) => {
                onPatchEntry(entry.id, patch);
              }}
              onRemove={() => {
                onRemoveEntry(entry.id);
              }}
            />
          ))}
        </div>
      )}

      <div
        style={{
          padding: "9px 16px",
          borderTop: "1px solid var(--pr-border-soft)",
          fontSize: 10.5,
          color: "var(--pr-text-muted)",
        }}
      >
        1 論文は複数のコレクションに入れられます · 並び順は共有ページにも反映
      </div>
    </Card>
  );
}

interface EntryRowProps {
  entry: CollectionEntry;
  isLast: boolean;
  onOpen: (libraryItemId: string) => void;
  onMoveUp: () => void;
  onMoveDown: () => void;
  disableUp: boolean;
  disableDown: boolean;
  onPatch: (patch: EntryPatch) => void;
  onRemove: () => void;
}

function EntryRow({
  entry,
  isLast,
  onOpen,
  onMoveUp,
  onMoveDown,
  disableUp,
  disableDown,
  onPatch,
  onRemove,
}: EntryRowProps) {
  const [editOpen, setEditOpen] = useState(false);
  const editAnchorRef = useRef<HTMLButtonElement>(null);
  const item = entry.library_item;
  const status = toReadingStatus(item.status);
  const unstarted = isUnstarted(item.status, item.progress_pct);
  const highlighted = entry.assignee_is_self && unstarted && item.status !== "reading";

  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 12,
        padding: "11px 16px",
        borderBottom: isLast ? "none" : "1px solid var(--pr-border-row)",
        background: highlighted ? "var(--pr-as)" : undefined,
      }}
    >
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

      <span
        style={{
          display: "inline-flex",
          alignItems: "center",
          justifyContent: "center",
          width: 20,
          height: 20,
          borderRadius: "50%",
          flex: "none",
          fontSize: 10.5,
          fontWeight: 700,
          background: highlighted ? "var(--pr-elev-bg)" : "var(--pr-bg-muted)",
          color: highlighted ? "#FFFFFF" : "var(--pr-text-sub)",
        }}
      >
        {entry.order}
      </span>

      <button
        type="button"
        onClick={() => {
          onOpen(item.id);
        }}
        style={{
          flex: 1,
          minWidth: 0,
          textAlign: "left",
          border: "none",
          background: "transparent",
          padding: 0,
          cursor: "pointer",
          fontFamily: "inherit",
        }}
      >
        <span
          style={{
            display: "block",
            fontSize: 12.5,
            fontWeight: 600,
            whiteSpace: "nowrap",
            overflow: "hidden",
            textOverflow: "ellipsis",
          }}
        >
          {item.paper.title}
        </span>
        <span style={{ display: "block", fontSize: 10, color: "var(--pr-text-muted)" }}>
          {formatSubLine(entry)}
        </span>
      </button>

      {entry.assignee_is_self ? (
        <span
          style={{
            display: "inline-flex",
            alignItems: "center",
            height: 18,
            padding: "0 8px",
            borderRadius: 999,
            background: "var(--pr-acc)",
            color: "#FFFFFF",
            fontSize: 9.5,
            fontWeight: 700,
            flex: "none",
          }}
        >
          担当: 自分
        </span>
      ) : null}

      <StatusDotLabel status={status} />
      <VariableSlot item={item} unstarted={unstarted} />

      <div style={{ display: "flex", gap: 6, flex: "none" }}>
        <button ref={editAnchorRef} type="button" onClick={() => setEditOpen(true)} style={smallActionStyle}>
          編集
        </button>
        <button type="button" onClick={onRemove} style={smallActionStyle}>
          外す
        </button>
      </div>

      {highlighted ? (
        <button
          type="button"
          onClick={() => {
            onOpen(item.id);
          }}
          style={ctaButtonStyle}
        >
          読み始める
        </button>
      ) : null}

      {editOpen ? (
        <EntryMetaPopover
          open={editOpen}
          onClose={() => setEditOpen(false)}
          anchorRef={editAnchorRef}
          initial={{
            assignee: entry.assignee,
            assigneeIsSelf: entry.assignee_is_self,
            presentationMinutes: entry.presentation_minutes,
            note: entry.note,
          }}
          onSave={(v) => {
            onPatch(
              v.assigneeIsSelf
                ? {
                    assignee: null,
                    assignee_is_self: true,
                    presentation_minutes: v.presentationMinutes,
                    note: v.note,
                  }
                : {
                    assignee: v.assignee,
                    assignee_is_self: false,
                    presentation_minutes: v.presentationMinutes,
                    note: v.note,
                  },
            );
            setEditOpen(false);
          }}
        />
      ) : null}
    </div>
  );
}

function StatusDotLabel({ status }: { status: ReadingStatus }) {
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 5, fontSize: 11, flex: "none" }}>
      <span
        style={{
          width: 7,
          height: 7,
          borderRadius: "50%",
          background: STATUS_COLORS[status],
        }}
      />
      <span>{STATUS_LABELS[status]}</span>
    </span>
  );
}

function VariableSlot({
  item,
  unstarted,
}: {
  item: CollectionEntry["library_item"];
  unstarted: boolean;
}) {
  if (unstarted && item.status !== "reading") {
    return (
      <span style={{ fontSize: 10.5, color: "var(--pr-warn)", fontWeight: 600, flex: "none" }}>
        未着手
      </span>
    );
  }
  if (item.status === "reading") {
    const section = item.last_position?.section_display?.split(" ")[0];
    return (
      <div style={{ width: 90, display: "flex", flexDirection: "column", gap: 3, flex: "none" }}>
        <ProgressBar value={item.progress_pct} color="accent" height={3} />
        <span style={{ fontSize: 9, color: "var(--pr-text-muted)" }}>
          {section ? `${section} · ${item.progress_pct}%` : `${item.progress_pct}%`}
        </span>
      </div>
    );
  }
  if (item.status === "done" && item.comprehension != null) {
    return (
      <span style={{ fontSize: 10.5, color: "var(--pr-text-sub)", flex: "none" }}>
        理解 {item.comprehension}/5
      </span>
    );
  }
  return <span style={{ fontSize: 10.5, color: "var(--pr-text-muted)", flex: "none" }}>—</span>;
}

interface EntryMetaPopoverProps {
  open: boolean;
  onClose: () => void;
  anchorRef: React.RefObject<HTMLButtonElement | null>;
  initial: {
    assignee: string | null;
    assigneeIsSelf: boolean;
    presentationMinutes: number | null;
    note: string | null;
  };
  onSave: (v: EntryMetaPopoverProps["initial"]) => void;
}

function EntryMetaPopover({ open, onClose, anchorRef, initial, onSave }: EntryMetaPopoverProps) {
  const [assignee, setAssignee] = useState(initial.assignee ?? "");
  const [assigneeIsSelf, setAssigneeIsSelf] = useState(initial.assigneeIsSelf);
  const [minutes, setMinutes] = useState(initial.presentationMinutes?.toString() ?? "");
  const [note, setNote] = useState(initial.note ?? "");

  if (!open) return null;

  return (
    <Popover open={open} onClose={onClose} anchorRef={anchorRef} width={260} placement="bottom-end">
      <div style={{ padding: 12, display: "flex", flexDirection: "column", gap: 8 }}>
        <label style={fieldLabelStyle}>
          担当
          <input
            type="text"
            value={assignee}
            disabled={assigneeIsSelf}
            placeholder={assigneeIsSelf ? "自分" : ""}
            onChange={(e) => setAssignee(e.target.value)}
            style={fieldInputStyle}
          />
        </label>
        <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 11 }}>
          <input
            type="checkbox"
            checked={assigneeIsSelf}
            onChange={(e) => setAssigneeIsSelf(e.target.checked)}
          />
          自分が担当
        </label>
        <label style={fieldLabelStyle}>
          発表時間(分)
          <input
            type="number"
            min={1}
            max={999}
            value={minutes}
            onChange={(e) => setMinutes(e.target.value)}
            style={fieldInputStyle}
          />
        </label>
        <label style={fieldLabelStyle}>
          注記
          <input
            type="text"
            value={note}
            placeholder="予備(時間があれば) など"
            onChange={(e) => setNote(e.target.value)}
            style={fieldInputStyle}
          />
        </label>
        <div style={{ display: "flex", gap: 8, justifyContent: "flex-end", marginTop: 4 }}>
          <button type="button" onClick={onClose} style={cancelButtonStyle}>
            キャンセル
          </button>
          <button
            type="button"
            onClick={() => {
              const parsedMinutes = minutes.trim() === "" ? null : Number(minutes);
              onSave({
                assignee: assignee.trim() === "" ? null : assignee.trim(),
                assigneeIsSelf,
                presentationMinutes:
                  parsedMinutes != null && Number.isFinite(parsedMinutes) ? parsedMinutes : null,
                note: note.trim() === "" ? null : note.trim(),
              });
            }}
            style={saveButtonStyle}
          >
            保存
          </button>
        </div>
      </div>
    </Popover>
  );
}

const headerButtonStyle: CSSProperties = {
  marginLeft: "auto",
  border: "none",
  background: "transparent",
  padding: 0,
  fontSize: 10.5,
  fontWeight: 600,
  color: "var(--pr-text-muted)",
  cursor: "pointer",
  fontFamily: "inherit",
};

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

const ctaButtonStyle: CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  height: 24,
  padding: "0 12px",
  borderRadius: 6,
  border: "none",
  background: "var(--pr-acc)",
  color: "#FFFFFF",
  fontSize: 11,
  fontWeight: 600,
  cursor: "pointer",
  fontFamily: "inherit",
  flex: "none",
};

const smallActionStyle: CSSProperties = {
  border: "1px solid var(--pr-border-control)",
  background: "var(--pr-bg-control)",
  color: "var(--pr-text-mid)",
  fontSize: 10.5,
  padding: "3px 8px",
  borderRadius: 5,
  cursor: "pointer",
  fontFamily: "inherit",
};

const fieldLabelStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 3,
  fontSize: 10,
  color: "var(--pr-text-muted)",
};

const fieldInputStyle: CSSProperties = {
  height: 28,
  fontSize: 11.5,
  border: "1px solid var(--pr-border-control)",
  borderRadius: 6,
  padding: "0 8px",
  fontFamily: "inherit",
};

const cancelButtonStyle: CSSProperties = {
  height: 24,
  padding: "0 10px",
  fontSize: 11,
  color: "var(--pr-text-sub2)",
  border: "none",
  background: "transparent",
  cursor: "pointer",
  fontFamily: "inherit",
};

const saveButtonStyle: CSSProperties = {
  height: 24,
  padding: "0 10px",
  fontSize: 11,
  fontWeight: 600,
  color: "#FFFFFF",
  border: "none",
  borderRadius: 5,
  background: "var(--pr-acc)",
  cursor: "pointer",
  fontFamily: "inherit",
};
