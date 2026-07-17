/*
 * Alinea Service Worker — v1(S13 / M3、spec 2026-07-16-pwa-offline-design §B)。
 *
 * v1 スコープ: アプリシェル/静的アセットのランタイムキャッシュのみ。
 *
 * 認証安全性の不変条件(最重要):
 *   HTML ナビゲーション・`/api/*`・auth は一切キャッシュせず、常にネットワークへ素通しする。
 *   → 認証済み HTML の陳腐化も、401→/login リダイレクト(lib/auth-redirect.ts)への
 *      干渉も起きない。SSR にも影響しない。
 *
 * キャッシュ対象(いずれも安全に cache 可能なもの):
 *   - 同一オリジンの /_next/static/*(内容ハッシュ付き=不変)      → cache-first
 *   - Google Fonts(fonts.googleapis.com / fonts.gstatic.com)      → stale-while-revalidate
 *   - アプリアイコン(/icons/*)とマニフェスト(/manifest.webmanifest) → cache-first
 *   - それ以外はすべて素通し。
 *
 * v2(オフライン読書=直近 N 本の viewer データ+図)は spec でゲート中。ここには含めない。
 */

const VERSION = "v1";
const STATIC_CACHE = `alinea-static-${VERSION}`;
const FONT_CACHE = `alinea-fonts-${VERSION}`;
const CURRENT_CACHES = [STATIC_CACHE, FONT_CACHE];

// install 時に確実に持っておきたい最小のシェル資産(小さく保つ。Next のハッシュ付き
// アセットは runtime キャッシュに任せる)。取得失敗は無視して install を止めない。
const PRECACHE_URLS = [
  "/manifest.webmanifest",
  "/icons/icon-192.png",
  "/icons/icon-512.png",
];

const FONT_HOSTS = new Set(["fonts.googleapis.com", "fonts.gstatic.com"]);

self.addEventListener("install", (event) => {
  self.skipWaiting();
  event.waitUntil(
    caches.open(STATIC_CACHE).then((cache) =>
      // 個別に addAll せず、失敗を握りつぶして install を確実に完了させる。
      Promise.allSettled(PRECACHE_URLS.map((url) => cache.add(url))),
    ),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(
          keys
            .filter((key) => key.startsWith("alinea-") && !CURRENT_CACHES.includes(key))
            .map((key) => caches.delete(key)),
        ),
      )
      .then(() => self.clients.claim()),
  );
});

function cacheFirst(request, cacheName) {
  return caches.open(cacheName).then((cache) =>
    cache.match(request).then((cached) => {
      if (cached) return cached;
      return fetch(request).then((response) => {
        if (response && response.ok) cache.put(request, response.clone());
        return response;
      });
    }),
  );
}

function staleWhileRevalidate(request, cacheName) {
  return caches.open(cacheName).then((cache) =>
    cache.match(request).then((cached) => {
      const network = fetch(request)
        .then((response) => {
          if (response && response.ok) cache.put(request, response.clone());
          return response;
        })
        .catch(() => cached);
      return cached || network;
    }),
  );
}

self.addEventListener("fetch", (event) => {
  const { request } = event;

  // GET 以外は一切触らない。
  if (request.method !== "GET") return;

  const url = new URL(request.url);
  const isSameOrigin = url.origin === self.location.origin;

  // Next の内容ハッシュ付き静的アセット(不変)→ cache-first。
  if (isSameOrigin && url.pathname.startsWith("/_next/static/")) {
    event.respondWith(cacheFirst(request, STATIC_CACHE));
    return;
  }

  // アプリアイコン / マニフェスト → cache-first。
  if (
    isSameOrigin &&
    (url.pathname.startsWith("/icons/") || url.pathname === "/manifest.webmanifest")
  ) {
    event.respondWith(cacheFirst(request, STATIC_CACHE));
    return;
  }

  // Google Fonts(CSS + フォントファイル)→ stale-while-revalidate。
  if (FONT_HOSTS.has(url.hostname)) {
    event.respondWith(staleWhileRevalidate(request, FONT_CACHE));
    return;
  }

  // それ以外(HTML ナビゲーション・/api/*・auth・その他)は素通し。
  // respondWith を呼ばない = ブラウザ既定のネットワーク処理に委ねる。
});
