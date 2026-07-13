"use client";

import type { CSSProperties } from "react";
import type { AnchorRef, ChatMessage as ChatMessageData } from "@alinea/api-client";
import { AiMark, AIBadge } from "@/components/ui/AIBadge";
import { EvidenceChip } from "@/components/ui/EvidenceChip";
import { ChatMarkdown } from "@/components/chat/ChatMarkdown";

export interface ChatMessageProps {
  message: ChatMessageData;
  /** SSE 受信中(アクション行を出さず、本文空なら「…」を表示。1a §5.3)。 */
  streaming?: boolean;
  /** 「再生成」(1a §5.2)。旧回答は残し新回答を追記(P3)。 */
  onRegenerate?: (messageId: string) => void;
  /** 「コピー」(根拠チップは display 展開。docs/05 §6)。 */
  onCopy?: (message: ChatMessageData) => void;
  /** 根拠チップ → 本文ジャンプ+双方向強調(1a §5.2)。 */
  onEvidenceJump?: (anchor: AnchorRef) => void;
  /** `error` イベント/接続断で残る失敗回答の再試行(1a §5.3・P3)。 */
  onRetry?: (messageId: string) => void;
  /** 「↑ メモに保存」(docs/05 §8。根拠アンカーはサーバーが複写)。 */
  onSaveToNote?: (messageId: string) => void;
  /**
   * モバイル縮退のボトムシート(mobile.md §4.5)から閲覧専用で再利用する場合 true。
   * 再生成・メモ保存(作成/操作系)を非描画にする(決定)。コピー・根拠ジャンプは維持。
   */
  readOnly?: boolean;
}

const actionLink = (accent = false): CSSProperties => ({
  border: "none",
  background: "transparent",
  cursor: "pointer",
  padding: 0,
  fontFamily: "var(--pr-font-ui)",
  fontSize: 10.5,
  color: accent ? "var(--pr-acc)" : "var(--pr-text-icon)",
  fontWeight: accent ? 600 : 400,
});

/** アシスタント/ユーザーのメッセージ 1 件(1a §4.5)。 */
export function ChatMessage({
  message,
  streaming = false,
  onRegenerate,
  onCopy,
  onEvidenceJump,
  onRetry,
  onSaveToNote,
  readOnly = false,
}: ChatMessageProps) {
  if (message.role === "user") return <UserMessage message={message} />;

  const hasContent = message.blocks.some((b) => (b.text ?? "").trim().length > 0);
  const isError = message.status === "error";

  return (
    <div
      data-message-id={message.id}
      style={{ display: "flex", flexDirection: "column", gap: 7, padding: "0 2px" }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
        <span style={{ color: "var(--pr-acc)", fontSize: 11, fontWeight: 700 }}>
          <AiMark /> アシスタント
        </span>
        <AIBadge variant="generated" />
      </div>

      {isError ? (
        <div style={{ fontSize: 12.6, lineHeight: 1.7, color: "var(--pr-warn)" }}>
          {message.error?.title ?? "回答の生成に失敗しました"}
          {readOnly ? null : (
            <>
              {" · "}
              <button type="button" style={actionLink(true)} onClick={() => onRetry?.(message.id)}>
                再試行
              </button>
            </>
          )}
        </div>
      ) : streaming && !hasContent ? (
        <div
          aria-label="生成中"
          style={{
            fontSize: 12.6,
            color: "var(--pr-text-muted)",
            animation: "alinea-pulse 1.2s ease-in-out infinite",
          }}
        >
          …
        </div>
      ) : (
        message.blocks.map((block, i) => {
          if ("label" in block) {
            return <AsideBox key={i} label={block.label} text={block.text} />;
          }
          return (
            <div
              key={i}
              style={{
                fontSize: 12.6,
                lineHeight: 1.85,
                color: "var(--pr-text-body)",
                margin: 0,
                minWidth: 0,
              }}
            >
              <ChatMarkdown
                text={block.text}
                evidence={block.evidence ?? []}
                onEvidenceJump={onEvidenceJump}
              />
            </div>
          );
        })
      )}

      {!streaming && !isError ? (
        <div style={{ display: "flex", gap: 12, paddingTop: 2 }}>
          {readOnly ? null : (
            <button type="button" style={actionLink()} onClick={() => onRegenerate?.(message.id)}>
              再生成
            </button>
          )}
          <button type="button" style={actionLink()} onClick={() => onCopy?.(message)}>
            コピー
          </button>
          {readOnly ? null : (
            <button type="button" style={actionLink()} onClick={() => onSaveToNote?.(message.id)}>
              ↑ メモに保存
            </button>
          )}
        </div>
      ) : null}
    </div>
  );
}

/** 論文外知識/推測ボックス(1a §4.5)。 */
function AsideBox({ label, text }: { label: "outside_knowledge" | "speculation"; text: string }) {
  return (
    <div
      style={{
        fontSize: 12.3,
        lineHeight: 1.8,
        color: "var(--pr-text-sub)",
        background: "var(--pr-bg-knowledge)",
        borderRadius: 6,
        padding: "8px 10px",
      }}
    >
      <span style={{ marginRight: 5, verticalAlign: 1 }}>
        <AIBadge variant={label === "speculation" ? "guess" : "external"} />
      </span>
      <ChatMarkdown text={text} evidence={[]} />
    </div>
  );
}

/** ユーザー質問カード(1a §4.5)。根拠チップ+引用+本文。 */
function UserMessage({ message }: { message: ChatMessageData }) {
  const anchors = message.context_anchors ?? [];
  const quoteRaw = anchors[0]?.quote ?? null;
  const quote = quoteRaw && quoteRaw.length > 80 ? `${quoteRaw.slice(0, 80)} …` : quoteRaw;
  const body = message.blocks.map((b) => b.text ?? "").join("");

  return (
    <div
      data-message-id={message.id}
      style={{
        background: "var(--pr-bg-card)",
        border: "1px solid var(--pr-border-card)",
        borderRadius: 8,
        padding: "10px 12px",
        display: "flex",
        flexDirection: "column",
        gap: 6,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
        {anchors.map((a, i) => (
          <EvidenceChip
            key={i}
            anchor={{ type: "section", sectionNumber: a.display }}
            label={a.display}
            size="header"
            onJump={() => undefined}
          />
        ))}
        <span style={{ marginLeft: "auto", fontSize: 10, color: "var(--pr-text-muted)" }}>
          あなた
        </span>
      </div>
      {quote ? (
        <div
          style={{
            fontFamily: "var(--pr-font-en)",
            fontStyle: "italic",
            fontSize: 10.5,
            color: "var(--pr-text-sub2)",
            borderLeft: "2px solid var(--pr-border-quote)",
            paddingLeft: 8,
          }}
        >
          {quote}
        </div>
      ) : null}
      {body ? (
        <div style={{ fontSize: 12.6, lineHeight: 1.7, color: "var(--pr-text-body)" }}>{body}</div>
      ) : null}
    </div>
  );
}
