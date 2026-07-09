import type { LibraryItemSummary } from "@alinea/api-client";

/**
 * collections API(plans/03 §13)の型。M2-09 時点では main.py にルータが未登録
 * (article レーンの担当)のため `@alinea/api-client` の生成物にまだ現れず、手書きする
 * (rule 6: 生成前の新規エンドポイントは手書き可。deviations 記載)。API 側の実装は
 * apps/api/src/alinea_api/schemas/collections.py と 1:1 対応させる。
 */

export interface CollectionListItem {
  id: string;
  name: string;
  deadline: string | null;
  days_left: number | null;
  item_count: number;
  done_count: number;
}

export interface CollectionListResponse {
  items: CollectionListItem[];
}

export interface ShareInfo {
  status: "none" | "active" | "revoked";
  token: string | null;
  url: string | null;
  include_notes: boolean;
  included_note_count: number;
}

export interface CollectionEntry {
  id: string;
  order: number;
  library_item: LibraryItemSummary;
  assignee: string | null;
  assignee_is_self: boolean;
  presentation_minutes: number | null;
  note: string | null;
}

export interface CollectionDetail {
  id: string;
  name: string;
  description: string | null;
  deadline: string | null;
  days_left: number | null;
  progress: { done: number; total: number };
  share: ShareInfo;
  entries: CollectionEntry[];
}

export interface EntryPatch {
  assignee?: string | null;
  assignee_is_self?: boolean;
  presentation_minutes?: number | null;
  note?: string | null;
}

export interface CollectionPatch {
  name?: string;
  description?: string | null;
  deadline?: string | null;
}
