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

  // 読書位置・モード間位置引き継ぎ(viewer-shell §3.4 / §8)
  currentBlockId: string | null;
  pendingScrollTarget: PendingScrollTarget;

  // 論文内検索(viewer-shell §7。M0 はフォーカス起動のみ)
  searchOpen: boolean;
  searchQuery: string;

  // 対訳ポップ開閉シグナル(viewer-shell §10 キー `t`。0 起点で +1)
  bilingualPopToggleSignal: number;

  // テキスト選択(選択メニュー。null=非表示)
  selection: ViewerSelection | null;

  // actions
  initViewer(itemId: string, revisionId: string): void;
  setTocOpen(open: boolean): void;
  setPanel(open: boolean, tab?: SidePanelTabId): void;
  setStyle(style: TranslationStyle): void;
  setCurrentBlock(blockId: string, sectionId: string): void;
  requestScroll(target: PendingScrollTarget): void;
  consumeScroll(): void;
  openSearch(query?: string): void;
  closeSearch(): void;
  setSearchQuery(query: string): void;
  toggleBilingualPop(): void;
  setSelection(selection: ViewerSelection | null): void;
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
  currentBlockId: null,
  pendingScrollTarget: null,
  searchOpen: false,
  searchQuery: "",
  bilingualPopToggleSignal: 0,
  selection: null,

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

  setCurrentBlock(blockId, sectionId) {
    set({ currentBlockId: blockId, activeSectionId: sectionId });
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

  setSelection(selection) {
    set({ selection });
  },
}));
