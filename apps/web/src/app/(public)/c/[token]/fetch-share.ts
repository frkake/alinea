/**
 * 匿名共有ページ(4c)の唯一のデータ取得(plans/09-screens/4c §2.1、plans/03 §14.1)。
 *
 * `@alinea/api-client` の生成型を使用する。fetch は Next.js Server Component 専用で
 * `next: { revalidate: 60 }` キャッシュが必要なため、SDK の Fetch クライアントは使わず
 * 直接 fetch する(@hey-api/client-fetch は Next.js `next` オプション非対応)。
 *
 * 生成型は OpenAPI "nullable optional" フィールドを `?: T | null` で生成するが、
 * 呼び出し元は `T | null` を期待するため、ラッパー型で `undefined` を除去する。
 */

import type {
  ShareCollectionResponse as _ShareCollectionResponse,
  ShareCollectionInfo as _ShareCollectionInfo,
  ShareCollectionItem as _ShareCollectionItem,
} from "@alinea/api-client";

/** ShareCollectionInfo: nullable optional フィールドを `T | null` へ正規化。 */
export type ShareCollectionInfo = Omit<_ShareCollectionInfo, "description" | "deadline"> & {
  description: string | null;
  deadline: string | null;
};

/** ShareCollectionItem: nullable optional フィールドを `T | null` へ正規化。 */
export type ShareCollectionItem = Omit<
  _ShareCollectionItem,
  "venue_year" | "arxiv_url" | "summary_3line" | "shared_note"
> & {
  venue_year: string | null;
  arxiv_url: string | null;
  summary_3line: string[] | null;
  shared_note: string | null;
};

/** ShareCollectionResponse: 正規化済み collection + items を持つ。 */
export type ShareCollectionResponse = Omit<_ShareCollectionResponse, "collection" | "items"> & {
  collection: ShareCollectionInfo;
  items: ShareCollectionItem[];
};

/** token は 8 文字の英数(plans/03 §13.3)。 */
const TOKEN_RE = /^[A-Za-z0-9]{8}$/;

const API_INTERNAL_URL = process.env.API_INTERNAL_URL ?? "http://localhost:8000";

/**
 * 共有コレクションを取得する。契約(plans/09-screens/4c §2.1 決定):
 * - token 形式不一致・API 404 → `null`(呼び出し側は `notFound()`)。
 * - それ以外の非 2xx・ネットワークエラー → throw(呼び出し側は `error.tsx`)。
 */
export async function fetchShareCollection(token: string): Promise<ShareCollectionResponse | null> {
  if (!TOKEN_RE.test(token)) return null;

  const res = await fetch(`${API_INTERNAL_URL}/api/share/collections/${token}`, {
    next: { revalidate: 60 },
  });
  if (res.status === 404) return null;
  if (!res.ok) {
    throw new Error(`share fetch failed: ${res.status}`);
  }
  return (await res.json()) as ShareCollectionResponse;
}
