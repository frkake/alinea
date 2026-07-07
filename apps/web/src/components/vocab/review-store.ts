"use client";

import { create } from "zustand";
import type { VocabEntryDetail } from "@yakudoku/api-client";
import type { ReviewResult } from "@/components/vocab/types";

export interface VocabReviewResultEntry {
  id: string;
  result: ReviewResult;
}

interface VocabReviewState {
  /** 出題残(先頭 = 現在のカード)。`[]` かつ `open:false` が初期状態。 */
  queue: VocabEntryDetail[];
  /** 起動時のキュー長(`again` の再エンキューで増やさない。4d §5.9 決定)。 */
  total: number;
  open: boolean;
  /** false=表(語義伏せ) / true=裏。 */
  flipped: boolean;
  /** 完了画面の集計用(評価順。同一 id が複数回入り得る)。 */
  results: VocabReviewResultEntry[];
  start: (items: VocabEntryDetail[]) => void;
  flip: () => void;
  /** 先頭を除去。'again' は末尾へ再エンキュー(docs/11 §7.2)。 */
  answer: (result: ReviewResult) => void;
  /** 途中終了可。評価済みのみ確定(P3)。 */
  close: () => void;
  /** 評価 API 失敗時、当該カードをキュー末尾へ戻す(4d §5.9 決定)。 */
  requeueAfterFailure: (entry: VocabEntryDetail) => void;
}

/** 復習セッション状態(4d §2.3・§5.9)。URL には持たない(セッション限定の一時状態)。 */
export const useVocabReviewStore = create<VocabReviewState>((set, get) => ({
  queue: [],
  total: 0,
  open: false,
  flipped: false,
  results: [],

  start: (items) => {
    set({ queue: items, total: items.length, open: true, flipped: false, results: [] });
  },

  flip: () => {
    set({ flipped: true });
  },

  answer: (result) => {
    const { queue, results } = get();
    const [current, ...rest] = queue;
    if (!current) return;
    const nextQueue = result === "again" ? [...rest, current] : rest;
    set({ queue: nextQueue, flipped: false, results: [...results, { id: current.id, result }] });
  },

  close: () => {
    set({ queue: [], open: false, flipped: false });
  },

  requeueAfterFailure: (entry) => {
    set((s) => ({ queue: [...s.queue, entry] }));
  },
}));

/** 評価済み(good で消化した)カード数(4d §5.9: 進捗の分子)。 */
export function countResolved(results: VocabReviewResultEntry[]): number {
  return results.filter((r) => r.result === "good").length;
}

/** 各カードの初回評価が good だった件数(4d §5.9: 完了画面の集計)。 */
export function countFirstAttemptGood(results: VocabReviewResultEntry[]): number {
  const seen = new Set<string>();
  let count = 0;
  for (const r of results) {
    if (seen.has(r.id)) continue;
    seen.add(r.id);
    if (r.result === "good") count += 1;
  }
  return count;
}
