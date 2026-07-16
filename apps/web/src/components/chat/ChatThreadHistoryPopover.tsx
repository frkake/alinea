"use client";

import { type RefObject } from "react";
import type { ChatThread } from "@alinea/api-client";
import { Popover } from "@/components/ui/Popover";
import { CountBadge } from "@/components/ui/CountBadge";

/** 会話履歴一覧(FigureVersionPopover と同じ実装パターン)。行クリックで切替、× で削除要求。 */
export function ChatThreadHistoryPopover({
  open,
  onClose,
  anchorRef,
  threads,
  activeThreadId,
  onSelect,
  onRequestDelete,
}: {
  open: boolean;
  onClose: () => void;
  anchorRef: RefObject<HTMLElement | null>;
  threads: ChatThread[];
  activeThreadId: string | null;
  onSelect: (threadId: string) => void;
  onRequestDelete: (thread: ChatThread) => void;
}) {
  return (
    <Popover open={open} onClose={onClose} anchorRef={anchorRef} width={260} placement="bottom-end">
      <div role="menu" aria-label="会話履歴" style={{ padding: "6px 0", maxHeight: 320, overflowY: "auto" }}>
        {threads.length === 0 ? (
          <div style={{ padding: "8px 12px", fontSize: 11, color: "var(--pr-text-muted)" }}>
            会話がありません
          </div>
        ) : (
          threads.map((t) => (
            <div
              key={t.id}
              style={{
                display: "flex",
                alignItems: "center",
                gap: 6,
                padding: "6px 10px 6px 12px",
                background: t.id === activeThreadId ? "var(--pr-bg-inset)" : "transparent",
              }}
            >
              <button
                type="button"
                role="menuitem"
                onClick={() => {
                  onSelect(t.id);
                  onClose();
                }}
                style={{
                  flex: 1,
                  minWidth: 0,
                  textAlign: "left",
                  border: "none",
                  background: "transparent",
                  cursor: "pointer",
                  fontFamily: "inherit",
                  padding: 0,
                }}
              >
                <div
                  style={{
                    fontSize: 11.5,
                    fontWeight: t.id === activeThreadId ? 600 : 400,
                    color: "var(--pr-text-mid)",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                  }}
                >
                  {t.title}
                  {t.is_main ? "(メイン)" : ""}
                </div>
                <div style={{ fontSize: 10, color: "var(--pr-text-muted)", display: "flex", gap: 6, alignItems: "center" }}>
                  <CountBadge count={t.message_count} variant="tab" />
                  {t.last_message_at ? <span>{t.last_message_at.slice(0, 10)}</span> : null}
                </div>
              </button>
              {t.is_main ? null : (
                <button
                  type="button"
                  aria-label={`「${t.title}」を削除`}
                  onClick={() => onRequestDelete(t)}
                  style={{
                    border: "none",
                    background: "transparent",
                    cursor: "pointer",
                    color: "var(--pr-text-sub)",
                    fontSize: 13,
                    padding: "0 2px",
                  }}
                >
                  ×
                </button>
              )}
            </div>
          ))
        )}
      </div>
    </Popover>
  );
}
