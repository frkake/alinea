/**
 * 構造化ドキュメント(GET /api/revisions/{id}/document)のクライアント型。
 *
 * OpenAPI 生成物では document レスポンスが `unknown`(生 JSON)なので、
 * py-core の DocumentContent / Block / Inline(docs/01 §4.1・§4.2)と同型を
 * ここでミラーする。翻訳ユニットの型は @yakudoku/api-client の TranslationUnitItem を使う。
 */

export type InlineType =
  | "text"
  | "math_inline"
  | "citation"
  | "ref"
  | "footnote_ref"
  | "url"
  | "emphasis"
  | "code_inline";

export interface Inline {
  t: InlineType;
  v?: string;
  ref?: string | null;
  kind?: string | null;
  href?: string | null;
}

export type BlockType =
  | "paragraph"
  | "heading"
  | "figure"
  | "table"
  | "equation"
  | "code"
  | "list"
  | "quote"
  | "theorem"
  | "algorithm"
  | "footnote"
  | "reference_entry";

export interface DocBlock {
  id: string;
  type: BlockType;
  inlines?: Inline[];
  level?: number | null;
  number?: string | null;
  title?: string | null;
  label?: string | null;
  latex?: string | null;
  language?: string | null;
  code?: string | null;
  caption?: Inline[];
  items?: Inline[][];
  ordered?: boolean | null;
}

export interface DocSectionHeading {
  number?: string;
  title?: string;
}

export interface DocSection {
  id: string;
  heading?: DocSectionHeading;
  blocks?: DocBlock[];
  sections?: DocSection[];
}

export interface DocumentResponse {
  revision_id: string;
  quality_level: string;
  sections: DocSection[];
}
