"use client";

import { useState, type CSSProperties, type KeyboardEvent } from "react";
import type { PendingAnchor } from "@/stores/viewer-chat-store";

/** 入力エリアの免責文(1a §4.5・固定逐語)。 */
export const CHAT_DISCLAIMER =
  "回答は原文を根拠にします。本文にない内容は「論文外の知識」「推測」と表示されます。";

export interface ChatComposerProps {
  onSend: (content: string) => void;
  /** ストリーミング中は入力・送信を非活性(1a §5.3)。 */
  disabled?: boolean;
  /** 送信前の引用チップ(「✦ この式を説明」/「✦ AIに質問」で積む)。 */
  pendingAnchors?: PendingAnchor[];
  onRemovePendingAnchor?: (blockId: string) => void;
}

const sendButtonStyle = (active: boolean): CSSProperties => ({
  width: 24,
  height: 24,
  flex: "none",
  borderRadius: 6,
  border: "none",
  background: "var(--pr-acc)",
  color: "var(--pr-bg-app)",
  fontSize: 12,
  cursor: active ? "pointer" : "default",
  opacity: active ? 1 : 0.45,
});

/** 入力エリア(1a §4.5)。自動伸長 textarea + 送信「↑」+ 免責文(固定)。 */
export function ChatComposer({
  onSend,
  disabled = false,
  pendingAnchors = [],
  onRemovePendingAnchor,
}: ChatComposerProps) {
  const [value, setValue] = useState("");
  const trimmed = value.trim();
  const canSend = !disabled && trimmed.length > 0;

  const submit = () => {
    if (!canSend) return;
    onSend(trimmed);
    setValue("");
  };

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    // Shift+Enter は改行、IME 変換確定中(isComposing)の Enter は送信しない(1a §5.2)。
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      submit();
    }
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      {pendingAnchors.length > 0 ? (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 5 }}>
          {pendingAnchors.map((p) => (
            <span
              key={p.anchor.block_id}
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 4,
                height: 18,
                padding: "0 6px",
                border: "1px solid var(--pr-acc-m)",
                color: "var(--pr-acc)",
                background: "var(--pr-acc-s)",
                borderRadius: 4,
                fontSize: 10,
                fontWeight: 600,
              }}
            >
              {p.display}
              <button
                type="button"
                aria-label={`${p.display} を外す`}
                onClick={() => onRemovePendingAnchor?.(p.anchor.block_id)}
                style={{
                  border: "none",
                  background: "transparent",
                  color: "var(--pr-acc)",
                  cursor: "pointer",
                  padding: 0,
                  fontSize: 11,
                  lineHeight: 1,
                }}
              >
                ×
              </button>
            </span>
          ))}
        </div>
      ) : null}

      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          border: "1px solid var(--pr-border-control)",
          borderRadius: 8,
          padding: "8px 10px",
        }}
      >
        <textarea
          rows={1}
          value={value}
          disabled={disabled}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder="この論文について質問…"
          aria-label="この論文について質問"
          style={{
            flex: 1,
            resize: "none",
            border: "none",
            outline: "none",
            background: "transparent",
            fontFamily: "inherit",
            fontSize: 12,
            color: "var(--pr-text-body)",
            maxHeight: 120,
          }}
        />
        <button
          type="button"
          aria-label="送信"
          disabled={!canSend}
          onClick={submit}
          style={sendButtonStyle(canSend)}
        >
          ↑
        </button>
      </div>

      <p
        style={{
          margin: 0,
          fontSize: 10,
          color: "var(--pr-text-muted)",
          lineHeight: 1.5,
        }}
      >
        {CHAT_DISCLAIMER}
      </p>
    </div>
  );
}
