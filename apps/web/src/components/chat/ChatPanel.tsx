"use client";

import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  chatListMessages,
  chatListThreads,
  notesCreate,
  notesSummarizeToNote,
  type AnchorRef,
  type AsideBlock,
  type ChatMessage as ChatMessageData,
  type EvidenceRef,
  type MarkdownBlock,
} from "@alinea/api-client";
import { useToast } from "@/components/ui/Toast";
import { EmptyState } from "@/components/ui/EmptyState";
import { Popover } from "@/components/ui/Popover";
import { useViewerStore } from "@/stores/viewer-store";
import { useViewerChatStore } from "@/stores/viewer-chat-store";
import { ChatMessage } from "@/components/chat/ChatMessage";
import { ChatComposer } from "@/components/chat/ChatComposer";
import { QuickActionChips, type QuickActionId } from "@/components/chat/QuickActionChips";
import { streamChat } from "@/components/chat/chat-stream";

export interface ChatPanelProps {
  itemId: string;
  /**
   * モバイル縮退のボトムシート(mobile.md §4.5)から閲覧専用で再利用する場合 true。
   * 入力欄・スレッドメニュー(まとめてメモ化)・再生成/メモ保存を非描画にする(決定)。
   * スレッド履歴の閲覧・根拠ジャンプ・コピーは維持。
   */
  readOnly?: boolean;
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

/** Combines fetched history with optimistic messages without duplicating server-assigned IDs. */
export function mergeDisplayMessages(
  history: readonly ChatMessageData[],
  localUser: ChatMessageData | null,
  localAssistant: ChatMessageData | null,
): ChatMessageData[] {
  const messagesById = new Map<string, ChatMessageData>();
  for (const message of history) messagesById.set(message.id, message);
  for (const message of [localUser, localAssistant]) {
    if (message) messagesById.set(message.id, message);
  }
  return [...messagesById.values()];
}

/**
 * 読解チャットタブ(1a §4.5・docs/05)。スレッド・履歴取得+ SSE 送信/再生成を担う。
 * SSE は POST fetch ストリーム(plans/03 §10.3 の start/delta/evidence/done/error を逐次パース)。
 */
export function ChatPanel({ itemId, readOnly = false }: ChatPanelProps) {
  const toast = useToast();
  const qc = useQueryClient();
  const requestScroll = useViewerStore((s) => s.requestScroll);
  const setPanel = useViewerStore((s) => s.setPanel);
  const pendingChatThreadId = useViewerStore((s) => s.pendingChatThreadId);
  const pendingChatMessageId = useViewerStore((s) => s.pendingChatMessageId);
  const consumeChatFocus = useViewerStore((s) => s.consumeChatFocus);
  const pendingAnchors = useViewerChatStore((s) => s.pendingAnchors);
  const removePendingAnchor = useViewerChatStore((s) => s.removePendingAnchor);
  const clearPendingAnchors = useViewerChatStore((s) => s.clearPendingAnchors);
  const setChatEvidence = useViewerChatStore((s) => s.setChatEvidence);

  const [activeThreadId, setActiveThreadId] = useState<string | null>(null);
  // ローカル(楽観/ストリーミング)メッセージ。done 後は履歴 refetch で置換する。
  const [localUser, setLocalUser] = useState<ChatMessageData | null>(null);
  const [localAssistant, setLocalAssistant] = useState<ChatMessageData | null>(null);
  const [streaming, setStreaming] = useState(false);
  const [threadMenuOpen, setThreadMenuOpen] = useState(false);
  const [summarizing, setSummarizing] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const threadMenuAnchor = useRef<HTMLButtonElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  const threadsQuery = useQuery({
    queryKey: ["chat-threads", itemId],
    queryFn: async () =>
      (await chatListThreads({ path: { item_id: itemId }, throwOnError: true })).data,
    staleTime: 0,
  });

  // アクティブスレッド = 明示選択 or ディープリンク(?thread=)or メイン(is_main)。
  // plans/09 1e §5.3-4「チャット」行: `target.kind === 'chat'` の遷移先。
  useEffect(() => {
    if (activeThreadId) return;
    const threads = threadsQuery.data?.items ?? [];
    if (threads.length === 0) return;
    const deepLinked = pendingChatThreadId ? threads.find((t) => t.id === pendingChatThreadId) : undefined;
    const main = deepLinked ?? threads.find((t) => t.is_main) ?? threads[0];
    if (main) setActiveThreadId(main.id);
  }, [threadsQuery.data, activeThreadId, pendingChatThreadId]);

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

  // ディープリンクのメッセージへスクロール+一発消費(plans/11 §7 `?message=`)。
  // スレッド切替が先に済んでから(activeThreadId が確定してから)実行する。
  useEffect(() => {
    if (!pendingChatThreadId && !pendingChatMessageId) return;
    if (!activeThreadId || !threadsQuery.data) return;
    if (pendingChatMessageId) {
      if (messagesQuery.isLoading) return;
      const el = listRef.current?.querySelector<HTMLElement>(
        `[data-message-id="${pendingChatMessageId}"]`,
      );
      if (el) {
        el.scrollIntoView({ block: "center" });
        el.classList.add("alinea-block-flash");
        window.setTimeout(() => el.classList.remove("alinea-block-flash"), 2000);
      }
    }
    consumeChatFocus();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pendingChatThreadId, pendingChatMessageId, activeThreadId, threadsQuery.data, messagesQuery.isLoading, history]);

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

  // 「↑ メモに保存」(docs/05 §8)。根拠アンカーは source_message_id 指定でサーバーが複写する。
  const saveToNote = useCallback(
    (message: ChatMessageData) => {
      void notesCreate({
        path: { item_id: itemId },
        body: { content_md: messageToMarkdown(message), source_message_id: message.id },
      }).then(
        () => {
          void qc.invalidateQueries({ queryKey: ["notes", itemId] });
          toast({ kind: "success", message: "✓ メモに保存しました" });
        },
        () => toast({ kind: "error", message: "メモに保存できませんでした" }),
      );
    },
    [itemId, qc, toast],
  );

  // 「まとめてメモ化」(docs/05 §8。同期実行 — plans/03 §10.5)。
  const summarizeToNote = useCallback(() => {
    if (!activeThreadId || summarizing) return;
    setThreadMenuOpen(false);
    setSummarizing(true);
    void notesSummarizeToNote({ path: { thread_id: activeThreadId } }).then(
      () => {
        setSummarizing(false);
        void qc.invalidateQueries({ queryKey: ["notes", itemId] });
        toast({ kind: "success", message: "✓ メモに保存しました" });
        setPanel(true, "notes");
      },
      () => {
        setSummarizing(false);
        toast({ kind: "error", message: "まとめてメモ化に失敗しました" });
      },
    );
  }, [activeThreadId, summarizing, itemId, qc, toast, setPanel]);

  const activeThread = (threadsQuery.data?.items ?? []).find((t) => t.id === activeThreadId);
  const displayMessages = mergeDisplayMessages(history, localUser, localAssistant);
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
        {readOnly ? null : (
          <>
            <button
              ref={threadMenuAnchor}
              type="button"
              aria-label="スレッドメニュー"
              aria-haspopup="menu"
              aria-expanded={threadMenuOpen}
              onClick={() => setThreadMenuOpen((v) => !v)}
              style={{
                border: "none",
                background: "transparent",
                cursor: "pointer",
                color: "var(--pr-text-sub)",
                fontSize: 13,
                letterSpacing: 1,
                padding: "0 2px",
              }}
            >
              ⋯
            </button>
            <Popover
              open={threadMenuOpen}
              onClose={() => setThreadMenuOpen(false)}
              anchorRef={threadMenuAnchor}
              width={180}
              placement="bottom-end"
              caret={false}
            >
              <button
                type="button"
                role="menuitem"
                disabled={summarizing || !activeThreadId}
                onClick={summarizeToNote}
                style={{
                  display: "block",
                  width: "100%",
                  textAlign: "left",
                  border: "none",
                  background: "transparent",
                  cursor: "pointer",
                  fontFamily: "inherit",
                  fontSize: 11.5,
                  padding: "0 12px",
                  height: 30,
                  color: "var(--pr-text-mid)",
                  opacity: summarizing ? 0.5 : 1,
                }}
              >
                {summarizing ? "まとめてメモ化 中…" : "まとめてメモ化"}
              </button>
            </Popover>
          </>
        )}
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
              description={
                readOnly
                  ? "この論文についての会話はまだありません。"
                  : "下の定型チップか入力欄から、この論文について質問できます。"
              }
            />
          </div>
        ) : (
          displayMessages.map((m) => (
            <ChatMessage
              key={m.id}
              message={m}
              streaming={streaming && m.id === localAssistant?.id}
              readOnly={readOnly}
              onRegenerate={regenerate}
              onCopy={copy}
              onEvidenceJump={onEvidenceJump}
              onRetry={retry}
              onSaveToNote={() => saveToNote(m)}
            />
          ))
        )}
      </div>

      {/* 入力エリア(ChatInputArea)。モバイル閲覧専用では非描画(mobile.md §1.2-3)。 */}
      {readOnly ? null : (
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
      )}
    </div>
  );
}
