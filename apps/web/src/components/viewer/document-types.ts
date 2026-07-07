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
  /** PDF ページ位置(1 起点)。品質 B は常時、品質 A は同期成功時のみ(plans/05 §4.6・2a §5.4)。 */
  page?: number | null;
  /** [x0,y0,x1,y1] pt。PyMuPDF 既定の上原点・下方向 y 増加(2a pdf/geometry.ts が変換)。 */
  bbox?: [number, number, number, number] | null;
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
