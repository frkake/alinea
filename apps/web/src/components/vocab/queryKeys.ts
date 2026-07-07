/**
 * 語彙帳(4d)関連の TanStack Query キー(plans/09-screens/4d §2.2)。
 * `AppNav`(サイドバーバッジ)と `/vocab` 画面本体で共有するため、文字列直書きを避けて
 * ここへ集約する(`@/components/notifications/queryKeys` と同方針)。
 */
export type VocabListParams = {
  kind?: "word" | "collocation" | "idiom" | null;
  due?: boolean;
  q?: string;
  sort: "added_at" | "term";
};

/** サイドバーバッジ用の最小フェッチ(4d §2.1 決定: `GET /api/vocab?limit=1` 相当)。 */
export const vocabCountsQueryKey = ["vocab", "counts"] as const;

export function vocabListQueryKey(params: VocabListParams) {
  return ["vocab", "list", params] as const;
}

export function vocabEntryQueryKey(id: string) {
  return ["vocab", "entry", id] as const;
}

export const vocabReviewQueueQueryKey = ["vocab", "review-queue"] as const;
