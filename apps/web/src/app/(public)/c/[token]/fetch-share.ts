/**
 * 匿名共有ページ(4c)の唯一のデータ取得(plans/09-screens/4c §2.1、plans/03 §14.1)。
 *
 * `@yakudoku/api-client` は本エンドポイント登録前の生成物のため型が存在しない(main.py への
 * `share.router` 登録待ち。followups 参照)。生成後に置き換えるまでの間、ここでレスポンス型を
 * 手書きし fetch を直書きする(厳守ルール6の許容範囲)。
 */

/** plans/03 §14.1 `GET /api/share/collections/{token}` 200 の完全形。 */
export interface ShareCollectionResponse {
  collection: {
    name: string;
    description: string | null;
    shared_by: string;
    updated_at: string;
    deadline: string | null;
    item_count: number;
  };
  include_notes: boolean;
  items: {
    order: number;
    title: string;
    authors_short: string;
    venue_year: string | null;
    arxiv_url: string | null;
    summary_3line: string[] | null;
    shared_note: string | null;
  }[];
}

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
