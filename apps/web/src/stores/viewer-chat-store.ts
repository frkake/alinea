"use client";

import { create } from "zustand";
import type { Anchor } from "@alinea/api-client";

/**
 * 送信前の引用チップ(1a §3.3 pendingAnchors)。
 * `anchor` は送信本文(plans/03 §10.3 の context_anchors)、`display` はチップ表示文字列。
 */
export interface PendingAnchor {
  anchor: Anchor;
  display: string;
}

/** 本文側「✦ チャットの根拠」常時強調の対象(1a §5.4)。 */
export interface ChatEvidenceTarget {
  blockId: string;
  display: string;
}

/**
 * 本文ペイン(BilingualPane / SourcePane)とサイドパネル(ChatPanel)を橋渡しする状態。
 *
 * viewer-store(viewer-shell §2.3 で完全形確定・凍結)は変更しないため、
 * チャット⇔本文の双方向リンクに必要な横断状態はこの独立ストアに置く(1c §3.3 と同方針)。
 * - pendingAnchors: 「✦ この式を説明」/ 選択メニュー「✦ AIに質問」で積む引用チップ。
 * - chatEvidenceBlockId: 本文側「✦ チャットの根拠」常時強調の対象ブロック(1a §5.4)。
 */
interface ViewerChatState {
  pendingAnchors: PendingAnchor[];
  chatEvidence: ChatEvidenceTarget | null;

  addPendingAnchor(pending: PendingAnchor): void;
  removePendingAnchor(blockId: string): void;
  clearPendingAnchors(): void;
  setChatEvidence(target: ChatEvidenceTarget | null): void;
  reset(): void;
}

export const useViewerChatStore = create<ViewerChatState>((set) => ({
  pendingAnchors: [],
  chatEvidence: null,

  addPendingAnchor(pending) {
    set((s) => {
      // 同一ブロックの重複は積まない(決定: 1 ブロック 1 チップ)。
      if (s.pendingAnchors.some((p) => p.anchor.block_id === pending.anchor.block_id)) {
        return s;
      }
      return { pendingAnchors: [...s.pendingAnchors, pending] };
    });
  },

  removePendingAnchor(blockId) {
    set((s) => ({
      pendingAnchors: s.pendingAnchors.filter((p) => p.anchor.block_id !== blockId),
    }));
  },

  clearPendingAnchors() {
    set({ pendingAnchors: [] });
  },

  setChatEvidence(target) {
    set({ chatEvidence: target });
  },

  reset() {
    set({ pendingAnchors: [], chatEvidence: null });
  },
}));
