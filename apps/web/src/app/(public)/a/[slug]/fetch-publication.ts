/**
 * 公開記事(Task 26)の Server Component 側データ取得。共有ページ(4c)の fetch-share と同方針:
 * SDK の Fetch クライアントは Next.js の `next` キャッシュ制御に非対応のため直接 fetch する。
 * 生成型 `PublicArticleOut` を返り値の型として使う。
 *
 * 契約:
 * - slug 形式不一致・API 404(未公開・予約中・不在)→ `null`(呼び出し側は `notFound()`)。
 * - それ以外の非 2xx・ネットワークエラー → throw(呼び出し側は error boundary)。
 */

import type { PublicArticleOut } from "@alinea/api-client";

// slug は英小文字・数字・ハイフン(サーバ側 _SLUG_RE と同じ規約)。
const SLUG_RE = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;

const API_INTERNAL_URL = process.env.API_INTERNAL_URL ?? "http://localhost:8000";

export async function fetchPublication(slug: string): Promise<PublicArticleOut | null> {
  if (!SLUG_RE.test(slug) || slug.length > 200) return null;

  const res = await fetch(`${API_INTERNAL_URL}/api/p/${slug}`, {
    next: { revalidate: 60 },
  });
  if (res.status === 404) return null;
  if (!res.ok) {
    throw new Error(`publication fetch failed: ${res.status}`);
  }
  return (await res.json()) as PublicArticleOut;
}
