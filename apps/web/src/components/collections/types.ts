/**
 * collections API(plans/03 §13)の型。
 *
 * `@alinea/api-client` の生成型を re-export し、後方互換のエイリアスだけをここで定義する。
 * 生成型は OpenAPI "nullable optional" フィールドを `?: T | null` (= `T | null | undefined`) で
 * 生成するが、呼び出し元は `T | null` を期待するため、ラッパー型で `undefined` を除去する。
 */

export type {
  CollectionListResponse,
  CollectionListItem,
  ShareInfo,
  CollectionPatchBody as CollectionPatch,
  EntryPatchBody as EntryPatch,
} from "@alinea/api-client";

import type {
  CollectionDetailResponse,
  CollectionEntryOut,
} from "@alinea/api-client";

/**
 * CollectionDetail: `CollectionDetailResponse` の nullable optional フィールドを
 * `T | null` へ正規化(undefined を除去)。
 */
export type CollectionDetail = Omit<CollectionDetailResponse, "description" | "deadline" | "days_left"> & {
  description: string | null;
  deadline: string | null;
  days_left: number | null;
  entries: CollectionEntry[];
};

/**
 * CollectionEntry: `CollectionEntryOut` の nullable optional フィールドを
 * `T | null` へ正規化(undefined を除去)。
 */
export type CollectionEntry = Omit<CollectionEntryOut, "assignee" | "presentation_minutes" | "note"> & {
  assignee: string | null;
  presentation_minutes: number | null;
  note: string | null;
};
