import type { SortKey } from "@/components/ui/LibraryTable";

/** ライブラリ画面のビュー(1e/4a のビュー切替)。 */
export type LibraryView = "card" | "table";

/** クイックフィルタ値(plans/03 §1.6 Quick。1e §4.6 / 4a §4.6)。 */
export type Quick = "all" | "unread" | "in_progress" | "done" | "recheck";

export interface SortState {
  key: SortKey;
  dir: "asc" | "desc";
}

/** ソートキー → 日本語ラベル(1e §5.6 / 4a §3.2 の対応表)。 */
export const SORT_LABELS: Record<SortKey, string> = {
  updated_at: "更新日",
  added_at: "追加日",
  title: "タイトル",
  deadline: "締切",
  reading_time: "読書時間",
  comprehension: "理解度",
  priority: "優先度",
  status: "ステータス",
  quality: "品質",
};

/** クイックフィルタの表示ラベル(1e §4.6 / 4a §4.6 の逐語)。 */
export const QUICK_LABELS: Record<Quick, string> = {
  all: "すべて",
  unread: "未読",
  in_progress: "途中",
  done: "読了",
  recheck: "要再確認",
};

export const QUICK_ORDER: readonly Quick[] = ["all", "unread", "in_progress", "done", "recheck"];
