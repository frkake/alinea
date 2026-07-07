"use client";

import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { notesCreate, notesDelete, notesList, notesUpdate, type Note } from "@yakudoku/api-client";
import { EmptyState } from "@/components/ui/EmptyState";
import { EvidenceChip } from "@/components/ui/EvidenceChip";
import { useToast } from "@/components/ui/Toast";
import { useViewerStore } from "@/stores/viewer-store";

/** 相対日時(1b §5.11 と同一規則)。 */
function formatRelativeDay(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const now = new Date();
  const startOfDay = (dt: Date) => new Date(dt.getFullYear(), dt.getMonth(), dt.getDate()).getTime();
  const diffDays = Math.round((startOfDay(now) - startOfDay(d)) / 86_400_000);
  const hm = `${d.getHours()}:${String(d.getMinutes()).padStart(2, "0")}`;
  if (diffDays === 0) return `今日 ${hm}`;
  if (diffDays === 1) return `昨日 ${hm}`;
  if (d.getFullYear() === now.getFullYear()) return `${d.getMonth() + 1}/${d.getDate()} ${hm}`;
  return `${d.getFullYear()}/${d.getMonth() + 1}/${d.getDate()}`;
}

/**
 * メモタブ本体(docs/04 §10・docs/05 §8。viewer-shell §6.5: props なし)。
 * 論文単位の自由メモの一覧・作成・編集・削除。チャット「↑メモに保存」/「まとめてメモ化」
 * で作成されたメモは根拠アンカー(source_message_id 経由の複写)をチップで表示する。
 */
export function NotesPanel() {
  const itemId = useViewerStore((s) => s.itemId);
  const requestScroll = useViewerStore((s) => s.requestScroll);
  const pendingNoteId = useViewerStore((s) => s.pendingNoteId);
  const consumeNoteFocus = useViewerStore((s) => s.consumeNoteFocus);
  const toast = useToast();
  const qc = useQueryClient();
  const [draft, setDraft] = useState("");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editingText, setEditingText] = useState("");
  const listRef = useRef<HTMLDivElement>(null);

  const queryKey = ["notes", itemId];
  const query = useQuery({
    queryKey,
    queryFn: async () =>
      (await notesList({ path: { item_id: itemId as string }, throwOnError: true })).data,
    enabled: Boolean(itemId),
    staleTime: 0,
  });

  const items = query.data?.items ?? [];

  const invalidate = () => void qc.invalidateQueries({ queryKey });

  // 検索ヒット遷移「メモ」(plans/11 §7 `?note=`)。該当メモへスクロール+2000ms 強調。
  useEffect(() => {
    if (!pendingNoteId) return;
    if (query.isLoading) return;
    const el = listRef.current?.querySelector<HTMLElement>(`[data-note-id="${pendingNoteId}"]`);
    if (el) {
      el.scrollIntoView({ block: "center" });
      el.classList.add("yk-block-flash");
      window.setTimeout(() => el.classList.remove("yk-block-flash"), 2000);
    }
    consumeNoteFocus();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pendingNoteId, query.isLoading, items]);

  const onCreate = () => {
    const content = draft.trim();
    if (!content || !itemId) return;
    setDraft("");
    void notesCreate({ path: { item_id: itemId }, body: { content_md: content } }).then(
      invalidate,
      () => toast({ kind: "error", message: "メモを保存できませんでした" }),
    );
  };

  const onSaveEdit = (note: Note) => {
    const content = editingText.trim();
    setEditingId(null);
    if (!content || content === note.content_md) return;
    void notesUpdate({ path: { note_id: note.id }, body: { content_md: content } }).then(
      invalidate,
      () => toast({ kind: "error", message: "メモを更新できませんでした" }),
    );
  };

  const onDelete = (note: Note) => {
    qc.setQueryData(queryKey, (prev: typeof query.data) =>
      prev ? { items: prev.items.filter((n) => n.id !== note.id) } : prev,
    );
    void notesDelete({ path: { note_id: note.id } }).then(
      () => {
        invalidate();
        toast({ kind: "success", message: "メモを削除しました" });
      },
      () => {
        invalidate();
        toast({ kind: "error", message: "削除できませんでした" });
      },
    );
  };

  if (!itemId) return null;

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", minHeight: 0 }}>
      <div
        ref={listRef}
        style={{
          flex: 1,
          minHeight: 0,
          overflowY: "auto",
          padding: 12,
          display: "flex",
          flexDirection: "column",
          gap: 8,
        }}
      >
        {query.isLoading ? null : items.length === 0 ? (
          <EmptyState title="メモはまだありません" description="下の入力欄からメモを作成できます。" />
        ) : (
          items.map((note) => (
            <div
              key={note.id}
              data-note-id={note.id}
              style={{
                background: "var(--pr-bg-card)",
                border: "1px solid var(--pr-border-card)",
                borderRadius: 8,
                padding: "9px 11px",
                display: "flex",
                flexDirection: "column",
                gap: 6,
              }}
            >
              {note.source ? (
                <span
                  style={{
                    alignSelf: "flex-start",
                    fontSize: 9,
                    fontWeight: 600,
                    color: "var(--pr-text-icon)",
                    border: "1px solid var(--pr-border-control)",
                    borderRadius: 3,
                    padding: "0 5px",
                    height: 15,
                    display: "inline-flex",
                    alignItems: "center",
                  }}
                >
                  チャットより
                </span>
              ) : null}
              {editingId === note.id ? (
                <textarea
                  autoFocus
                  aria-label="メモを編集"
                  value={editingText}
                  onChange={(e) => setEditingText(e.target.value)}
                  onBlur={() => onSaveEdit(note)}
                  rows={4}
                  style={{
                    fontSize: 12,
                    fontFamily: "inherit",
                    color: "var(--pr-text-body)",
                    border: "1px solid var(--pr-border-control)",
                    borderRadius: 6,
                    padding: 6,
                    resize: "vertical",
                  }}
                />
              ) : (
                <div
                  style={{
                    fontSize: 12,
                    lineHeight: 1.7,
                    color: "var(--pr-text-body)",
                    whiteSpace: "pre-wrap",
                  }}
                >
                  {note.content_md}
                </div>
              )}
              {(note.anchors ?? []).length > 0 ? (
                <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
                  {(note.anchors ?? []).map((a, i) => (
                    <EvidenceChip
                      key={i}
                      anchor={{ type: "section", sectionNumber: a.display }}
                      label={a.display}
                      onJump={() => requestScroll({ kind: "block", blockId: a.block_id })}
                    />
                  ))}
                </div>
              ) : null}
              <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                <span style={{ fontSize: 10, color: "var(--pr-text-muted)" }}>
                  {formatRelativeDay(note.updated_at)}
                </span>
                <span style={{ flex: 1 }} />
                {editingId === note.id ? null : (
                  <button
                    type="button"
                    onClick={() => {
                      setEditingId(note.id);
                      setEditingText(note.content_md);
                    }}
                    style={linkButtonStyle}
                  >
                    編集
                  </button>
                )}
                <button type="button" onClick={() => onDelete(note)} style={linkButtonStyle}>
                  削除
                </button>
              </div>
            </div>
          ))
        )}
      </div>

      <div
        style={{
          padding: "10px 12px",
          borderTop: "1px solid var(--pr-border-soft)",
          display: "flex",
          flexDirection: "column",
          gap: 8,
        }}
      >
        <textarea
          aria-label="新しいメモ"
          placeholder="この論文についてのメモを書く…"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          rows={3}
          style={{
            fontSize: 12,
            fontFamily: "inherit",
            color: "var(--pr-text-body)",
            border: "1px solid var(--pr-border-control)",
            borderRadius: 6,
            padding: 8,
            resize: "none",
          }}
        />
        <button
          type="button"
          onClick={onCreate}
          disabled={!draft.trim()}
          style={{
            alignSelf: "flex-end",
            height: 26,
            padding: "0 14px",
            border: "none",
            borderRadius: 6,
            background: "var(--pr-acc)",
            color: "#FFFFFF",
            fontSize: 11.5,
            fontWeight: 600,
            cursor: draft.trim() ? "pointer" : "default",
            opacity: draft.trim() ? 1 : 0.5,
            fontFamily: "inherit",
          }}
        >
          保存
        </button>
      </div>
    </div>
  );
}

const linkButtonStyle = {
  border: "none",
  background: "transparent",
  cursor: "pointer",
  padding: 0,
  fontFamily: "inherit",
  fontSize: 10.5,
  color: "var(--pr-text-icon)",
} as const;
