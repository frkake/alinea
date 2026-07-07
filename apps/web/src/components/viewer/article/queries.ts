import { articlesGet, type Problem } from "@yakudoku/api-client";
import type { Article } from "@/components/viewer/article/types";

/**
 * TanStack Query キー(1h §2.2 確定)。`['viewer', liId]` / `['in-paper-search', …]` は
 * viewer-shell 所有のためここでは再定義しない(呼び出し側が直接同一キーを使う)。
 */
export const articleKeys = {
  article: (liId: string) => ["article", liId] as const,
  articleVersions: (articleId: string) => ["article-versions", articleId] as const,
  overviewFigure: (articleId: string) => ["overview-figure", articleId] as const,
  blockPreview: (revId: string, blockId: string) => ["block-preview", revId, blockId] as const,
};

/**
 * 記事取得(§2.2 決定): 404 は例外として扱わず「未生成」として呼び出し側が判定する
 * (`isArticleNotFound`)。`retry: false` と組み合わせて使う。
 */
export async function fetchArticle(itemId: string): Promise<Article> {
  const res = await articlesGet({ path: { item_id: itemId }, throwOnError: true });
  return res.data;
}

export function isArticleNotFound(error: unknown): boolean {
  const problem = error as Partial<Problem> | undefined;
  return problem?.status === 404;
}
