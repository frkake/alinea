"use client";

import { useRef, useState, type CSSProperties } from "react";
import Link from "next/link";
import { Popover } from "@/components/ui/Popover";
import { ProgressBar } from "@/components/ui/ProgressBar";
import { formatDeadlineBadge } from "@/components/collections/format";
import type { CollectionDetail, CollectionPatch } from "@/components/collections/types";

/** コレクションヘッダー(plans/09-screens/4b §4.3.1)。 */
export interface CollectionHeaderProps {
  collection: CollectionDetail;
  onPatch: (patch: CollectionPatch) => void;
}

export function CollectionHeader({ collection, onPatch }: CollectionHeaderProps) {
  const [editingDescription, setEditingDescription] = useState(false);
  const [description, setDescription] = useState(collection.description ?? "");
  const deadlineAnchorRef = useRef<HTMLButtonElement>(null);
  const [deadlinePopoverOpen, setDeadlinePopoverOpen] = useState(false);

  const progressPct =
    collection.progress.total === 0
      ? 0
      : Math.round((collection.progress.done / collection.progress.total) * 100);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 7, flex: 1, minWidth: 0 }}>
      <div style={{ fontSize: 10.5, color: "var(--pr-text-muted)" }}>
        <Link href="/library" style={{ color: "inherit", textDecoration: "none" }}>
          ライブラリ
        </Link>{" "}
        / コレクション
      </div>

      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <span style={{ fontSize: 19, fontWeight: 700 }}>{collection.name}</span>
        {collection.deadline != null && collection.days_left != null ? (
          <button
            ref={deadlineAnchorRef}
            type="button"
            onClick={() => setDeadlinePopoverOpen((v) => !v)}
            style={deadlineBadgeStyle}
          >
            {formatDeadlineBadge(collection.deadline, collection.days_left)}
          </button>
        ) : (
          <button
            ref={deadlineAnchorRef}
            type="button"
            onClick={() => setDeadlinePopoverOpen((v) => !v)}
            style={setDeadlineLinkStyle}
          >
            締切を設定
          </button>
        )}
        <span style={{ fontSize: 11, color: "var(--pr-text-muted)" }}>
          {collection.entries.length} 本 · 順序付き
        </span>
      </div>

      {editingDescription ? (
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <textarea
            value={description}
            autoFocus
            onChange={(e) => setDescription(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Escape") {
                setDescription(collection.description ?? "");
                setEditingDescription(false);
              } else if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
                onPatch({ description: description.trim() === "" ? null : description });
                setEditingDescription(false);
              }
            }}
            style={descriptionTextareaStyle}
          />
          <div style={{ display: "flex", gap: 8 }}>
            <button
              type="button"
              onClick={() => {
                onPatch({ description: description.trim() === "" ? null : description });
                setEditingDescription(false);
              }}
              style={saveButtonStyle}
            >
              保存
            </button>
            <button
              type="button"
              onClick={() => {
                setDescription(collection.description ?? "");
                setEditingDescription(false);
              }}
              style={cancelButtonStyle}
            >
              キャンセル
            </button>
          </div>
        </div>
      ) : (
        <div style={{ fontSize: 12, color: "var(--pr-text-sub)", lineHeight: 1.7 }}>
          {collection.description}{" "}
          <button
            type="button"
            onClick={() => setEditingDescription(true)}
            style={inlineEditLinkStyle}
          >
            {collection.description ? "説明を編集" : "説明を追加"}
          </button>
        </div>
      )}

      {collection.progress.total > 0 ? (
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginTop: 2 }}>
          <div style={{ width: 220 }}>
            <ProgressBar value={progressPct} color="green" height={4} />
          </div>
          <span style={{ fontSize: 10.5, color: "var(--pr-text-sub)" }}>
            {collection.progress.done}/{collection.progress.total} 読了
          </span>
        </div>
      ) : null}

      <DeadlinePopover
        open={deadlinePopoverOpen}
        onClose={() => setDeadlinePopoverOpen(false)}
        anchorRef={deadlineAnchorRef}
        value={collection.deadline}
        onSave={(deadline) => {
          onPatch({ deadline });
          setDeadlinePopoverOpen(false);
        }}
      />
    </div>
  );
}

function DeadlinePopover({
  open,
  onClose,
  anchorRef,
  value,
  onSave,
}: {
  open: boolean;
  onClose: () => void;
  anchorRef: React.RefObject<HTMLButtonElement | null>;
  value: string | null;
  onSave: (deadline: string | null) => void;
}) {
  const [date, setDate] = useState(value ?? "");
  if (!open) return null;
  return (
    <Popover open={open} onClose={onClose} anchorRef={anchorRef} width={220} placement="bottom-start">
      <div style={{ padding: 12, display: "flex", flexDirection: "column", gap: 8 }}>
        <input
          type="date"
          value={date}
          onChange={(e) => setDate(e.target.value)}
          style={{
            height: 28,
            fontSize: 11.5,
            border: "1px solid var(--pr-border-control)",
            borderRadius: 6,
            padding: "0 8px",
          }}
        />
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <button
            type="button"
            onClick={() => {
              if (date) onSave(date);
            }}
            style={saveButtonStyle}
          >
            保存
          </button>
          {value != null ? (
            <button
              type="button"
              onClick={() => onSave(null)}
              style={{ ...cancelButtonStyle, fontSize: 10.5 }}
            >
              締切を削除
            </button>
          ) : null}
        </div>
      </div>
    </Popover>
  );
}

const deadlineBadgeStyle: CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  height: 19,
  padding: "0 8px",
  borderRadius: 4,
  border: "none",
  background: "var(--pr-warn-bg)",
  color: "var(--pr-warn)",
  fontSize: 10.5,
  fontWeight: 700,
  cursor: "pointer",
  fontFamily: "inherit",
};

const setDeadlineLinkStyle: CSSProperties = {
  border: "none",
  background: "transparent",
  fontSize: 10.5,
  color: "var(--pr-acc)",
  fontWeight: 600,
  cursor: "pointer",
  fontFamily: "inherit",
};

const inlineEditLinkStyle: CSSProperties = {
  border: "none",
  background: "transparent",
  color: "var(--pr-acc)",
  fontWeight: 600,
  fontSize: 12,
  cursor: "pointer",
  fontFamily: "inherit",
  padding: 0,
};

const descriptionTextareaStyle: CSSProperties = {
  width: "100%",
  maxWidth: 560,
  minHeight: 56,
  fontSize: 12,
  lineHeight: 1.7,
  border: "1px solid var(--pr-border-control)",
  borderRadius: 6,
  padding: "8px 10px",
  background: "var(--pr-bg-card)",
  fontFamily: "inherit",
};

const saveButtonStyle: CSSProperties = {
  height: 24,
  padding: "0 10px",
  border: "none",
  borderRadius: 5,
  background: "var(--pr-acc)",
  color: "#FFFFFF",
  fontSize: 11,
  fontWeight: 600,
  cursor: "pointer",
  fontFamily: "inherit",
};

const cancelButtonStyle: CSSProperties = {
  height: 24,
  padding: "0 10px",
  border: "none",
  background: "transparent",
  color: "var(--pr-text-sub2)",
  fontSize: 11,
  cursor: "pointer",
  fontFamily: "inherit",
};
