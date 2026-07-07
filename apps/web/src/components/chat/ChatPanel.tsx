"use client";

import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  chatListMessages,
  chatListThreads,
  type AnchorRef,
  type AsideBlock,
  type ChatMessage as ChatMessageData,
  type EvidenceRef,
  type MarkdownBlock,
} from "@yakudoku/api-client";
import { useToast } from "@/components/ui/Toast";
import { EmptyState } from "@/components/ui/EmptyState";
import { useViewerStore } from "@/stores/viewer-store";
import { useViewerChatStore } from "@/stores/viewer-chat-store";
import { ChatMessage } from "@/components/chat/ChatMessage";
import { ChatComposer } from "@/components/chat/ChatComposer";
import { QuickActionChips, type QuickActionId } from "@/components/chat/QuickActionChips";
import { streamChat } from "@/components/chat/chat-stream";

export interface ChatPanelProps {
  itemId: string;
}

type StreamBlock = MarkdownBlock | AsideBlock;

function newLocalMessage(role: "user" | "assistant", partial: Partial<ChatMessageData>): ChatMessageData {
  return {
    id: partial.id ?? `local-${role}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    role,
    blocks: partial.blocks ?? [],
    context_anchors: partial.context_anchors ?? [],
    quick_action: partial.quick_action ?? null,
    status: partial.status ?? "complete",
    error: partial.error ?? null,
    created_at: partial.created_at ?? new Date().toISOString(),
  };
}

/** delta を該当 block_index に反映(markdown は連結、aside はラベル保持で連結)。 */
function applyDelta(
  blocks: StreamBlock[],
  d: { block_index: number; block_type: "markdown" | "aside"; text: string; label?: AsideBlock["label"] },
  evidence: EvidenceRef[],
): StreamBlock[] {
  const next = [...blocks];
  while (next.length <= d.block_index) {
    next.push({ type: "markdown", text: "", evidence });
  }
  const existing = next[d.block_index];
  if (d.block_type === "aside") {
    const prevText = existing && "label" in existing ? existing.text : "";
    const prevLabel = existing && "label" in existing ? existing.label : undefined;
    next[d.block_index] = {
      type: "aside",
      label: d.label ?? prevLabel ?? "outside_knowledge",
      text: prevText + d.text,
    };
  } else {
    const prevText = existing && !("label" in existing) ? existing.text : "";
    next[d.block_index] = { type: "markdown", text: prevText + d.text, evidence };
  }
  return next;
}

/** コピー用にメッセージ Markdown を組み立てる(根拠チップは display 展開。docs/05 §6)。 */
function messageToMarkdown(message: ChatMessageData): string {
  return message.blocks
    .map((block) => {
      if ("label" in block) return block.text;
      let text = block.text;
      for (const ev of block.evidence ?? []) {
        text = text.replaceAll(`[[ev:${ev.ref}]]`, ev.display);
      }
      return text;
    })
    .join("\n\n");
}

/**
 * 読解チャットタブ(1a §4.5・docs/05)。スレッド・履歴取得+ SSE 送信/再生成を担う。
 * SSE は POST fetch ストリーム(plans/03 §10.3 の start/delta/evidence/done/error を逐次パース)。
 */
export function ChatPanel({ itemId }: ChatPanelProps) {
  const toast = useToast();
  const qc = useQueryClient();
  const requestScroll = useViewerStore((s) => s.requestScroll);
  const setPanel = useViewerStore((s) => s.setPanel);
  const pendingAnchors = useViewerChatStore((s) => s.pendingAnchors);
  const removePendingAnchor = useViewerChatStore((s) => s.removePendingAnchor);
  const clearPendingAnchors = useViewerChatStore((s) => s.clearPendingAnchors);
  const setChatEvidence = useViewerChatStore((s) => s.setChatEvidence);

  const [activeThreadId, setActiveThreadId] = useState<string | null>(null);
  // ローカル(楽観/ストリーミング)メッセージ。done 後は履歴 refetch で置換する。
  const [localUser, setLocalUser] = useState<ChatMessageData | null>(null);
  const [localAssistant, setLocalAssistant] = useState<ChatMessageData | null>(null);
  const [streaming, setStreaming] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const listRef = useRef<HTMLDivElement>(null);

  const threadsQuery = useQuery({
    queryKey: ["chat-threads", itemId],
    queryFn: async () =>
      (await chatListThreads({ path: { item_id: itemId }, throwOnError: true })).data,
    staleTime: 0,
  });

  // アクティブスレッド = 明示選択 or メイン(is_main)。
  useEffect(() => {
    if (activeThreadId) return;
    const threads = threadsQuery.data?.items ?? [];
    const main = threads.find((t) => t.is_main) ?? threads[0];
    if (main) setActiveThreadId(main.id);
  }, [threadsQuery.data, activeThreadId]);

  const messagesQuery = useQuery({
    queryKey: ["chat-messages", activeThreadId],
    queryFn: async () =>
      (
        await chatListMessages({
          path: { thread_id: activeThreadId as string },
          throwOnError: true,
        })
      ).data,
    enabled: Boolean(activeThreadId),
    staleTime: 0,
  });

  // items は新しい順(遡り)なので、表示は古い順に反転する(plans/03 §10.2)。
  const history = useMemo<ChatMessageData[]>(
    () => [...(messagesQuery.data?.items ?? [])].reverse(),
    [messagesQuery.data],
  );

  // 履歴が確定メッセージを含んだらローカルの重複を破棄(done 後の置換)。
  useEffect(() => {
    if (streaming) return;
    const ids = new Set(history.map((m) => m.id));
    if (localAssistant && ids.has(localAssistant.id)) setLocalAssistant(null);
    if (localUser && ids.has(localUser.id)) setLocalUser(null);
  }, [history, streaming, localAssistant, localUser]);

  // ストリーミング中は最下部追随。
  useEffect(() => {
    const el = listRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [history, localAssistant, localUser]);

  useEffect(() => () => abortRef.current?.abort(), []);

  const onEvidenceJump = useCallback(
    (anchor: AnchorRef) => {
      requestScroll({ kind: "block", blockId: anchor.block_id });
      setChatEvidence({ blockId: anchor.block_id, display: anchor.display });
    },
    [requestScroll, setChatEvidence],
  );

  const runStream = useCallback(
    (url: string, body: unknown, seedAssistant: ChatMessageData) => {
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;
      setStreaming(true);
      setLocalAssistant(seedAssistant);
      const evidence: EvidenceRef[] = [];

      void streamChat(
        url,
        body,
        {
          onStart(e) {
            setLocalAssistant((m) => (m ? { ...m, id: e.message_id } : m));
            setLocalUser((m) => (m ? { ...m, id: e.user_message_id } : m));
          },
          onDelta(e) {
            setLocalAssistant((m) =>
              m ? { ...m, blocks: applyDelta(m.blocks, e, evidence) } : m,
            );
          },
          onEvidence(e) {
            evidence.push(e);
            setLocalAssistant((m) =>
              m
                ? {
                    ...m,
                    blocks: m.blocks.map((b) =>
                      "label" in b ? b : { ...b, evidence: [...evidence] },
                    ),
                  }
                : m,
            );
          },
          onDone() {
            setStreaming(false);
            void qc.invalidateQueries({ queryKey: ["chat-messages", activeThreadId] });
          },
          onError(problem) {
            setStreaming(false);
            setLocalAssistant((m) => (m ? { ...m, status: "error", error: problem } : m));
          },
        },
        controller.signal,
      );
    },
    [qc, activeThreadId],
  );

  const send = useCallback(
    (content: string, quickAction?: QuickActionId) => {
      if (!activeThreadId || streaming) return;
      const anchors = pendingAnchors.map((p) => p.anchor);
      const contextAnchorRefs: AnchorRef[] = pendingAnchors.map((p) => ({
        revision_id: p.anchor.revision_id,
        block_id: p.anchor.block_id,
        start: p.anchor.start ?? null,
        end: p.anchor.end ?? null,
        quote: p.anchor.quote ?? null,
        side: p.anchor.side,
        display: p.display,
      }));
      setLocalUser(
        newLocalMessage("user", {
          blocks: content ? [{ type: "markdown", text: content, evidence: [] }] : [],
          context_anchors: contextAnchorRefs,
          quick_action: quickAction ?? null,
        }),
      );
      clearPendingAnchors();
      runStream(
        `/api/chat/threads/${activeThreadId}/messages`,
        { content, context_anchors: anchors, quick_action: quickAction ?? null },
        newLocalMessage("assistant", {}),
      );
    },
    [activeThreadId, streaming, pendingAnchors, clearPendingAnchors, runStream],
  );

  const regenerate = useCallback(
    (messageId: string) => {
      if (streaming) return;
      runStream(
        `/api/chat/messages/${messageId}/regenerate`,
        {},
        newLocalMessage("assistant", {}),
      );
    },
    [streaming, runStream],
  );

  const copy = useCallback(
    (message: ChatMessageData) => {
      void navigator.clipboard?.writeText(messageToMarkdown(message)).then(
        () => toast({ kind: "success", message: "コピーしました" }),
        () => toast({ kind: "error", message: "コピーできませんでした" }),
      );
    },
    [toast],
  );

  const retry = useCallback(
    (messageId: string) => {
      // 失敗回答は履歴に残り message_id を持つため、その再生成で復旧(1a §5.3)。
      regenerate(messageId);
    },
    [regenerate],
  );

  const activeThread = (threadsQuery.data?.items ?? []).find((t) => t.id === activeThreadId);
  const displayMessages = [...history];
  if (localUser) displayMessages.push(localUser);
  if (localAssistant) displayMessages.push(localAssistant);
  const isEmpty = displayMessages.length === 0;

  const chipStyle: CSSProperties = {
    display: "inline-flex",
    alignItems: "center",
    gap: 4,
    height: 18,
    padding: "0 7px",
    background: "var(--pr-bg-inset)",
    borderRadius: 4,
    fontSize: 10,
    color: "var(--pr-text-sub2)",
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", minHeight: 0 }}>
      {/* スレッド/コンテキスト行(ThreadBar) */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 6,
          padding: "7px 12px",
          borderBottom: "1px solid var(--pr-border-hair)",
          fontSize: 11,
          color: "var(--pr-text-sub2)",
          flex: "none",
        }}
      >
        <span>
          スレッド:{" "}
          <span style={{ color: "var(--pr-text-mid)", fontWeight: 600 }}>
            {activeThread?.title ?? "メイン"}
          </span>
        </span>
        <span style={{ flex: 1 }} />
        <span style={chipStyle}>コンテキスト: この論文</span>
      </div>

      {/* メッセージ領域(ChatMessageList) */}
      <div
        ref={listRef}
        style={{
          flex: 1,
          minHeight: 0,
          overflowY: "auto",
          padding: 12,
          display: "flex",
          flexDirection: "column",
          gap: 12,
          background: "var(--pr-bg-feed)",
        }}
      >
        {isEmpty ? (
          <div style={{ margin: "auto 0" }}>
            <EmptyState
              title="まだ会話がありません"
              description="下の定型チップか入力欄から、この論文について質問できます。"
            />
          </div>
        ) : (
          displayMessages.map((m) => (
            <ChatMessage
              key={m.id}
              message={m}
              streaming={streaming && m.id === localAssistant?.id}
              onRegenerate={regenerate}
              onCopy={copy}
              onEvidenceJump={onEvidenceJump}
              onRetry={retry}
            />
          ))
        )}
      </div>

      {/* 入力エリア(ChatInputArea) */}
      <div
        style={{
          padding: "10px 12px",
          borderTop: "1px solid var(--pr-border-soft)",
          display: "flex",
          flexDirection: "column",
          gap: 8,
          background: "var(--pr-bg-card)",
          flex: "none",
        }}
      >
        <QuickActionChips
          disabled={streaming}
          onPick={(qa) => {
            setPanel(true, "chat");
            send("", qa);
          }}
        />
        <ChatComposer
          onSend={(content) => send(content)}
          disabled={streaming}
          pendingAnchors={pendingAnchors}
          onRemovePendingAnchor={removePendingAnchor}
        />
      </div>
    </div>
  );
}
