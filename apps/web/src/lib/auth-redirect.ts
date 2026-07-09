/**
 * 401 → /login リダイレクトの一元処理。
 *
 * ダッシュボード/ライブラリ等の認証必須ページで、セッション切れ・未ログイン時に
 * 「読み込みに失敗しました」を出したまま止まるのではなく、ログイン画面へ誘導する
 * (docs/06 §1 の想定フロー)。api-client のレスポンスインターセプタとして登録する。
 *
 * - ここは apps/web 専用: 拡張は同じ @alinea/api-client を使うが、ポップアップは
 *   401 で独自の未ログイン UI を出すため、この副作用は web 側でのみ登録する。
 * - /login 自身と /api/auth/*(me はログイン状態確認に 401 を正常系として使う)は除外し、
 *   リダイレクトループを防ぐ。
 */

import { client } from "@alinea/api-client";

let registered = false;

export function registerAuthRedirect(): void {
  if (registered || typeof window === "undefined") return;
  registered = true;

  client.interceptors.response.use((response, request) => {
    if (response.status !== 401) return response;

    const url = new URL(request.url, window.location.origin);
    if (url.pathname.startsWith("/api/auth/")) return response;

    const { pathname, search } = window.location;
    if (pathname === "/login" || pathname.startsWith("/c/")) return response;

    const next = encodeURIComponent(pathname + search);
    window.location.assign(`/login?next=${next}`);
    return response;
  });
}
