/**
 * リソースタブ(5a)の型(plans/03 §12.1 の完全形をそのまま使用)。
 *
 * `@alinea/api-client` は本エンドポイント群を未生成のため(main.py 未登録。deviations 参照)、
 * ここでローカル定義する。生成後はこのファイルを `@alinea/api-client` の re-export に差し替える。
 */

export type ResKind = "github" | "youtube" | "slides" | "article";

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

export interface ResourceLink {
  id: string;
  kind: ResKind;
  url: string;
  official: boolean;
  title: string;
  source_label: string;
  thumbnail_url: string | null;
  meta: ResourceMeta;
  meta_fetched: boolean;
  note: string | null;
  created_at: string;
}

export interface ResourceSuggestion {
  url: string;
  detected_from: "arxiv_page";
}

export interface ResourceListResponse {
  items: ResourceLink[];
  suggestion: ResourceSuggestion | null;
  count: number;
}
