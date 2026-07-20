/**
 * リソースタブ(5a)の型。
 *
 * `@alinea/api-client` の生成型を re-export し、UI 固有の補助型だけをここで定義する。
 * 生成型は OpenAPI "nullable optional" フィールドを `?: T | null` (= `T | null | undefined`) で
 * 生成するが、呼び出し元は `T | null` を期待するため、ラッパー型で `undefined` を除去する。
 */

export type {
  ResourceSuggestion,
} from "@alinea/api-client";

import type {
  ResourceLink as _ResourceLink,
  ResourceListResponse as _ResourceListResponse,
} from "@alinea/api-client";

/**
 * ResourceLink: `@alinea/api-client` の生成型から nullable optional フィールドを
 * `T | null` へ正規化し、meta を Record<string, unknown> に固定する。
 */
export type ResourceLink = Omit<_ResourceLink, "thumbnail_url" | "note" | "meta"> & {
  thumbnail_url: string | null;
  note: string | null;
  meta: Record<string, unknown>;
};

/**
 * ResourceListResponse: items を正規化済み ResourceLink の配列に固定。
 */
export type ResourceListResponse = Omit<_ResourceListResponse, "items" | "suggestion"> & {
  items: ResourceLink[];
  suggestion: import("@alinea/api-client").ResourceSuggestion | null;
};

/** リソース種別(生成型 ResourceLink.kind の union を名付けたエイリアス)。 */
export type ResKind = "github" | "youtube" | "slides" | "article";

/** kind 別 meta 形(format.ts のキャスト用。生成型は meta?: Record<string, unknown>)。 */
export interface ResourceGithubMeta {
  language: string | null;
  stars: number | null;
  updated_at: string | null;
}

export interface ResourceYoutubeMeta {
  duration_seconds: number | null;
}

export interface ResourceSlidesMeta {
  format: "pdf";
  pages: number | null;
}

export interface ResourceArticleMeta {
  reading_minutes: number | null;
}

export type ResourceMeta =
  | ResourceGithubMeta
  | ResourceYoutubeMeta
  | ResourceSlidesMeta
  | ResourceArticleMeta
  | Record<string, never>;
