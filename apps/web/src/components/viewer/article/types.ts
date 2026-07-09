import type { AnchorRefOut, ArticleBlockOut, ArticleOut, OverviewFigureRefOut } from "@alinea/api-client";

/** 記事(plans/03 §19.1)。api-client の `ArticleOut` をそのまま使う。 */
export type Article = ArticleOut;

/** 記事ブロック(plans/03 §19.1)。 */
export type ArticleBlock = ArticleBlockOut;

/**
 * 記事プリセット(plans/03 §19.1)。api-client では `Preset` という独立した型は生成されない
 * (pydantic の `Literal` 型別名は各フィールドにインライン展開される)ため、
 * `ArticleOut.preset` から導出する(決定)。
 */
export type Preset = ArticleOut["preset"];

/**
 * 全体概要図の参照(plans/03 §20.1)。`ArticleOut.overview_figure` は OpenAPI 上
 * `dict[str, Any] | null`(M2-04 が M2-05 の型を待たずに確定した経緯。schemas/articles.py
 * のコメント参照)のため、実体は API 実装(`_overview_figure_ref`)と一致する
 * `OverviewFigureRefOut` としてキャストして扱う(決定)。
 */
export type OverviewFigureRef = OverviewFigureRefOut;

export function asOverviewFigureRef(value: Article["overview_figure"]): OverviewFigureRef | null {
  if (!value) return null;
  return value as unknown as OverviewFigureRef;
}

/** アンカージャンプ(§5.6)の入力型。 */
export type AnchorRef = AnchorRefOut;

/** 進行中ジョブの識別(§3.2 決定)。 */
export type ArticleJobScope = "article" | "overview" | { blockId: string };
