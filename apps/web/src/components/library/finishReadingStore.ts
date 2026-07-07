import { create } from "zustand";
import type { LibraryItemSummary } from "@yakudoku/api-client";

/**
 * 読了フロー(1g)のグローバル起動状態(plans/09-screens/1g §2.3)。
 *
 * `item` は `PATCH /api/library-items/{id}` の `{status:"done"}` 成功レスポンス
 * (= `LibraryItemSummary`)そのもの。`null` はダイアログが閉じている状態。
 * 発火規約: 「ステータスが UI 操作で done になり PATCH が成功した」時点で `open()` を呼ぶ
 * (既存ステータス変更 UI と通知ポップオーバーの両方から共通で使う。1g §2.3 の決定)。
 *
 * 決定(本タスクの縮小スコープ): plans/09-screens/1g §2.3 は
 * `apps/web/src/stores/finish-reading.ts` を正としているが、本タスクの所有範囲が
 * `components/library/` と `components/notifications/` に限られるため、store を
 * `components/library/` 配下に置く(deviations 記載)。
 */
interface FinishReadingState {
  item: LibraryItemSummary | null;
  open: (item: LibraryItemSummary) => void;
  close: () => void;
}

export const useFinishReadingStore = create<FinishReadingState>((set) => ({
  item: null,
  open: (item) => {
    set({ item });
  },
  close: () => {
    set({ item: null });
  },
}));
