"use client";

import { create } from "zustand";
import { clampZoom } from "@/components/viewer/pdf/geometry";

/** 幅に合わせる / ページ全体 / 実寸(2a §3.2)。 */
export type PdfFitMode = "fit-width" | "fit-page" | "actual";
export type PdfSpreadFirstPageSide = "left" | "right";

export interface PdfViewState {
  /** リセット対象キー(libraryItemId)。異なる論文に切り替わったら state を初期化する。 */
  itemId: string | null;
  page: number; // 1 起点
  zoom: number; // pdf.js scale
  fitMode: PdfFitMode | null; // null=手動ズーム中
  spread: boolean;
  spreadFirstPageSide: PdfSpreadFirstPageSide;
  selectedBlockId: string | null;
  sidebarTab: "toc" | "pages"; // 既定 'pages'

  resetForItem(itemId: string, initialPage?: number): void;
  setPage(page: number): void;
  zoomIn(): void;
  zoomOut(): void;
  setFitMode(mode: PdfFitMode): void;
  toggleSpread(): void;
  setSpreadFirstPageSide(side: PdfSpreadFirstPageSide): void;
  selectBlock(id: string | null): void;
  setSidebarTab(tab: "toc" | "pages"): void;
}

/**
 * PDF モード専用のビュー状態(2a §3.2)。論文単位にリセットし、永続化しない
 * (URL の `?mode=pdf&page=` が正 — 2a §5.1)。
 */
export const usePdfViewStore = create<PdfViewState>((set, get) => ({
  itemId: null,
  page: 1,
  zoom: 1,
  fitMode: "fit-width",
  spread: false,
  spreadFirstPageSide: "right",
  selectedBlockId: null,
  sidebarTab: "pages",

  resetForItem(itemId, initialPage = 1) {
    if (get().itemId === itemId) return;
    set({
      itemId,
      page: initialPage,
      zoom: 1,
      fitMode: "fit-width",
      spread: false,
      spreadFirstPageSide: "right",
      selectedBlockId: null,
      sidebarTab: "pages",
    });
  },

  setPage(page) {
    set({ page, selectedBlockId: null });
  },

  zoomIn() {
    set((s) => ({ zoom: clampZoom(s.zoom + 0.1), fitMode: null }));
  },

  zoomOut() {
    set((s) => ({ zoom: clampZoom(s.zoom - 0.1), fitMode: null }));
  },

  setFitMode(mode) {
    set({ fitMode: mode });
  },

  toggleSpread() {
    set((s) => ({ spread: !s.spread }));
  },

  setSpreadFirstPageSide(side) {
    set({ spreadFirstPageSide: side });
  },

  selectBlock(id) {
    set({ selectedBlockId: id });
  },

  setSidebarTab(tab) {
    set({ sidebarTab: tab });
  },
}));
