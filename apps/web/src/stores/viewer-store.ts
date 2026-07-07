"use client";

import { create } from "zustand";
import type { SidePanelTabId } from "@/components/ui/SidePanelTabs";

/** 翻訳スタイル(plans/03 §7)。オンデマンド生成対象は literal のみ。 */
export type TranslationStyle = "natural" | "literal";

/** 本文ペインへスクロールを依頼する対象(viewer-shell §2.3)。 */
export type PendingScrollTarget =
  | { kind: "block"; blockId: string }
  | { kind: "section"; sectionId: string }
  | null;

/** テキスト選択状態(選択メニュー配置用。viewer-shell §2.3 の M0 サブセット)。 */
export interface ViewerSelection {
  blockId: string;
  /** 選択元。原文(対訳ポップ内)なら 'source'、訳文段落なら 'translation'。 */
  side: "source" | "translation";
  quote: string;
  /** ブロック内文字オフセット(1b §5.5 の Anchor 構築用。取得不能時は null)。 */
  start: number | null;
  end: number | null;
  /** メニュー配置に使う選択矩形(ビューポート座標)。 */
  rect: { top: number; left: number; bottom: number; right: number };
}

interface ViewerStoreState {
  itemId: string | null;
  revisionId: string | null;

  // 目次(viewer-shell §5)
  tocOpen: boolean; // true=232px ペイン / false=44px レール
  activeSectionId: string | null; // 現在位置ハイライト(スクロール連動)

  // サイドパネル(viewer-shell §6)
  panelOpen: boolean;
  activeTab: SidePanelTabId;

  // 翻訳スタイル(viewer-shell §4.4)
  style: TranslationStyle;
  /**
   * 直訳(literal)のオンデマンド生成状態(plans/06 §10.2・1b §4.2-7)。
   * "unknown"=未確認、"generating"=生成中(job_id あり。優先セクション分の完了を待つ)、
   * "ready"=表示中セクション分は生成済み(セット全体の完了は §7.1 の progress_pct で別途確認)。
   * リビジョンが変わるたび(initViewer)に "unknown" へ戻る。
   */
  literalStatus: "unknown" | "generating" | "ready";
  literalJobId: string | null;
  literalSetId: string | null;

  // 読書位置・モード間位置引き継ぎ(viewer-shell §3.4 / §8)
  currentBlockId: string | null;
  pendingScrollTarget: PendingScrollTarget;

  // 論文内検索(viewer-shell §7。M0 はフォーカス起動のみ)
  searchOpen: boolean;
  searchQuery: string;

  // 対訳ポップ開閉シグナル(viewer-shell §10 キー `t`。0 起点で +1)
  bilingualPopToggleSignal: number;

  /**
   * 記事「✦ 指示つき再生成」の進行状態(1h §5.3)。トリガーは `ArticleRegenerateButton`
   * (ヘッダ)、表示は `ArticleRegenBanner`(本文)— ツリー上の兄弟のため、この横断状態で橋渡しする
   * (literalStatus と同方針)。
   */
  articleRegenerating: boolean;
  articleRegenProgressPct: number;

  // ブックマーク切替シグナル(viewer-shell §10 キー `b`。0 起点で +1。1b が実処理を担う)
  bookmarkToggleSignal: number;

  // テキスト選択(選択メニュー。null=非表示)
  selection: ViewerSelection | null;

  // 検索ヒット遷移(plans/11 §7)の一発消費ターゲット。対象タブが消費して null に戻す。
  pendingAnnotationId: string | null;
  pendingNoteId: string | null;
  /** `?hl=` の値。遷移先ブロック内だけをマークする一発消費クエリ。 */
  pendingHighlightQuery: string | null;
  /**
   * チャットのディープリンク(plans/11 §7 `?thread=`/`?message=`。plans/09 1e §5.3-4「チャット」行)。
   * ChatPanel が該当スレッド選択+メッセージへスクロールした時点で一発消費(null に戻す)。
   */
  pendingChatThreadId: string | null;
  pendingChatMessageId: string | null;

  // actions
  initViewer(itemId: string, revisionId: string): void;
  setTocOpen(open: boolean): void;
  setPanel(open: boolean, tab?: SidePanelTabId): void;
  setStyle(style: TranslationStyle): void;
  setLiteralGeneration(state: {
    status: "unknown" | "generating" | "ready";
    jobId?: string | null;
    setId?: string | null;
  }): void;
  setCurrentBlock(blockId: string, sectionId: string): void;
  setArticleRegenState(state: { regenerating: boolean; progressPct?: number }): void;
  requestScroll(target: PendingScrollTarget): void;
  consumeScroll(): void;
  openSearch(query?: string): void;
  closeSearch(): void;
  setSearchQuery(query: string): void;
  toggleBilingualPop(): void;
  toggleBookmark(): void;
  setSelection(selection: ViewerSelection | null): void;
  requestAnnotationFocus(annotationId: string): void;
  consumeAnnotationFocus(): void;
  requestNoteFocus(noteId: string): void;
  consumeNoteFocus(): void;
  setPendingHighlightQuery(query: string | null): void;
  requestChatFocus(target: { threadId?: string | null; messageId?: string | null }): void;
  consumeChatFocus(): void;
}

function readLocal(key: string): string | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function writeLocal(key: string, value: string): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(key, value);
  } catch {
    /* storage 不可環境では黙ってスキップ */
  }
}

function readSession(key: string): string | null {
  if (typeof window === "undefined") return null;
  try {
    return window.sessionStorage.getItem(key);
  } catch {
    return null;
  }
}

function writeSession(key: string, value: string): void {
  if (typeof window === "undefined") return;
  try {
    window.sessionStorage.setItem(key, value);
  } catch {
    /* noop */
  }
}

const TAB_IDS: readonly SidePanelTabId[] = [
  "chat",
  "notes",
  "annotations",
  "figures",
  "resources",
  "info",
];

/**
 * ビューア横断状態(viewer-shell §2.3)。論文単位に 1 つ。
 * `mode` は URL クエリ `?mode=` が正なのでストアには持たない(viewer-shell §2.3)。
 * 永続化: tocOpen/style は localStorage、panelOpen/activeTab は sessionStorage。
 */
export const useViewerStore = create<ViewerStoreState>((set, get) => ({
  itemId: null,
  revisionId: null,
  tocOpen: false,
  activeSectionId: null,
  panelOpen: true,
  activeTab: "chat",
  style: "natural",
  literalStatus: "unknown",
  literalJobId: null,
  literalSetId: null,
  currentBlockId: null,
  pendingScrollTarget: null,
  searchOpen: false,
  searchQuery: "",
  bilingualPopToggleSignal: 0,
  bookmarkToggleSignal: 0,
  articleRegenerating: false,
  articleRegenProgressPct: 0,
  selection: null,
  pendingAnnotationId: null,
  pendingNoteId: null,
  pendingHighlightQuery: null,
  pendingChatThreadId: null,
  pendingChatMessageId: null,

  initViewer(itemId, revisionId) {
    const tocRaw = readLocal(`yk-toc-open:${itemId}`);
    const styleRaw = readLocal(`yk-viewer-style:${itemId}`);
    const panelRaw = readSession(`yk-viewer-panel:${itemId}`);

    let panelOpen = true;
    let activeTab: SidePanelTabId = "chat";
    if (panelRaw === "closed") {
      panelOpen = false;
    } else if (panelRaw && (TAB_IDS as readonly string[]).includes(panelRaw)) {
      activeTab = panelRaw as SidePanelTabId;
    }

    set({
      itemId,
      revisionId,
      tocOpen: tocRaw === "1" ? true : tocRaw === "0" ? false : get().tocOpen,
      style: styleRaw === "literal" ? "literal" : styleRaw === "natural" ? "natural" : get().style,
      panelOpen,
      activeTab,
      // リビジョンが変わるので直訳生成状態は未確認に戻す(plans/06 §10.2)。
      literalStatus: "unknown",
      literalJobId: null,
      literalSetId: null,
      // 論文が変わるので前の論文の記事再生成表示は引き継がない。
      articleRegenerating: false,
      articleRegenProgressPct: 0,
    });
  },

  setTocOpen(open) {
    const { itemId } = get();
    if (itemId) writeLocal(`yk-toc-open:${itemId}`, open ? "1" : "0");
    set({ tocOpen: open });
  },

  setPanel(open, tab) {
    const { itemId, activeTab } = get();
    const nextTab = tab ?? activeTab;
    if (itemId) {
      writeSession(`yk-viewer-panel:${itemId}`, open ? nextTab : "closed");
    }
    set({ panelOpen: open, activeTab: nextTab });
  },

  setStyle(style) {
    const { itemId } = get();
    if (itemId) writeLocal(`yk-viewer-style:${itemId}`, style);
    set({ style });
  },

  setLiteralGeneration({ status, jobId, setId }) {
    set((s) => ({
      literalStatus: status,
      literalJobId: jobId === undefined ? s.literalJobId : jobId,
      literalSetId: setId === undefined ? s.literalSetId : setId,
    }));
  },

  setCurrentBlock(blockId, sectionId) {
    set({ currentBlockId: blockId, activeSectionId: sectionId });
  },

  setArticleRegenState({ regenerating, progressPct }) {
    set((s) => ({
      articleRegenerating: regenerating,
      articleRegenProgressPct: progressPct === undefined ? s.articleRegenProgressPct : progressPct,
    }));
  },

  requestScroll(target) {
    set({ pendingScrollTarget: target });
  },

  consumeScroll() {
    set({ pendingScrollTarget: null });
  },

  openSearch(query) {
    set((s) => ({ searchOpen: true, searchQuery: query ?? s.searchQuery }));
  },

  closeSearch() {
    set({ searchOpen: false });
  },

  setSearchQuery(query) {
    set({ searchQuery: query });
  },

  toggleBilingualPop() {
    set((s) => ({ bilingualPopToggleSignal: s.bilingualPopToggleSignal + 1 }));
  },

  toggleBookmark() {
    set((s) => ({ bookmarkToggleSignal: s.bookmarkToggleSignal + 1 }));
  },

  setSelection(selection) {
    set({ selection });
  },

  requestAnnotationFocus(annotationId) {
    set({ pendingAnnotationId: annotationId });
  },

  consumeAnnotationFocus() {
    set({ pendingAnnotationId: null });
  },

  requestNoteFocus(noteId) {
    set({ pendingNoteId: noteId });
  },

  consumeNoteFocus() {
    set({ pendingNoteId: null });
  },

  setPendingHighlightQuery(query) {
    set({ pendingHighlightQuery: query });
  },

  requestChatFocus(target) {
    set({
      pendingChatThreadId: target.threadId ?? null,
      pendingChatMessageId: target.messageId ?? null,
    });
  },

  consumeChatFocus() {
    set({ pendingChatThreadId: null, pendingChatMessageId: null });
  },
}));
