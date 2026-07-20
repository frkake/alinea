/*
 * Alinea Service Worker — v2(S13 / M3、spec 2026-07-16-pwa-offline-design §B)。
 *
 * v1 スコープ: アプリシェル/静的アセットのランタイムキャッシュ(下記 STATIC/FONT)。
 * v2 スコープ(Task 23): 直近 10 論文の viewer データ+関連 asset のオフライン閲覧。
 *
 * 認証安全性の不変条件(最重要 / plan §4「401 を SW のキャッシュで成功応答へ置換しない」):
 *   - HTML ナビゲーション・auth・非対象 API は従来どおり素通しする。
 *   - 対象 viewer API は network-first。ネットワークが「応答を返した」場合(200 でも
 *     401/403/404/500 でも)はその応答を無改変で返す。fetch() が throw したとき
 *     (=ネットワーク到達不能=オフライン)だけ cache フォールバックする。
 *     → 期限切れセッションでオンラインなら 401 が素通しし、通常の /login 遷移が働く。
 *
 * v2 の追加キャッシュ対象(いずれも viewer 閲覧に必要な GET のみ):
 *   - GET /api/library-items/{id}/viewer            (viewer init)
 *   - GET /api/revisions/{id}/document               (document)
 *   - GET /api/revisions/{id}/translations/{s}/units (translation units)
 *   - GET /api/revisions/{id}/figures / references
 *   - GET /api/assets/{id}                            (assets)
 *
 * per-user 分離: CACHE_PAPER 時に userId を manifest へ記録し、SET_ACTIVE_USER で
 *   アクティブユーザーが変わったら旧ユーザーの応答をフォールバック候補から外す。
 *   PURGE_USER(ログアウト/アカウント削除)は当該ユーザーの応答を cache ごと消す。
 *
 * ★ 本ファイルは src/lib/offline-viewer.ts と「同一の振る舞い」を表す(あちらが契約の正)。
 *   ロジックを変えるときは両方を揃えること(offline-viewer.test.ts が契約を守る)。
 *
 * rollback: VERSION を上げると旧 alinea-* キャッシュは activate で全消去され、v2 機能を
 *   含めて無効化できる(app-shell の静的キャッシュ挙動はそのまま残る)。
 */

const VERSION = "v1";
const STATIC_CACHE = `alinea-static-${VERSION}`;
const FONT_CACHE = `alinea-fonts-${VERSION}`;
// v2: 直近論文の viewer データ+manifest 用キャッシュ。
const VIEWER_CACHE = `alinea-viewer-${VERSION}`;
const CURRENT_CACHES = [STATIC_CACHE, FONT_CACHE, VIEWER_CACHE];

// install 時に確実に持っておきたい最小のシェル資産(小さく保つ。Next のハッシュ付き
// アセットは runtime キャッシュに任せる)。取得失敗は無視して install を止めない。
// v2: /offline(ナビゲーション失敗時のオフラインシェル)も precache する。
const PRECACHE_URLS = [
  "/manifest.webmanifest",
  "/icons/icon-192.png",
  "/icons/icon-512.png",
  "/offline",
];

const FONT_HOSTS = new Set(["fonts.googleapis.com", "fonts.gstatic.com"]);

// ── v2 オフライン閲覧の定数(src/lib/offline-viewer.ts と一致させる) ──────────────
const MANIFEST_URL = "https://alinea.offline/__viewer_manifest__";
const ACTIVE_USER_URL = "https://alinea.offline/__active_user__";
const MAX_PAPERS = 10;
const MAX_PAPER_BYTES = 50 * 1024 * 1024; // 50 MiB
const MAX_TOTAL_BYTES = 200 * 1024 * 1024; // 200 MiB

// 対象 GET の allow-list(offline-viewer.ts の CACHEABLE_PATTERNS と一致)。
const CACHEABLE_PATTERNS = [
  /^\/api\/library-items\/[^/]+\/viewer$/,
  /^\/api\/revisions\/[^/]+\/document$/,
  /^\/api\/revisions\/[^/]+\/translations\/[^/]+\/units$/,
  /^\/api\/revisions\/[^/]+\/figures$/,
  /^\/api\/revisions\/[^/]+\/references$/,
  /^\/api\/assets\/[^/]+$/,
];

function isCacheableViewerRequest(rawUrl) {
  let pathname;
  try {
    pathname = new URL(rawUrl).pathname;
  } catch {
    pathname = rawUrl.split("?")[0] || rawUrl;
  }
  return CACHEABLE_PATTERNS.some((re) => re.test(pathname));
}

function estimateResponseBytes(response) {
  const raw = response.headers.get("content-length");
  if (!raw) return 0;
  const n = Number.parseInt(raw, 10);
  return Number.isFinite(n) && n >= 0 ? n : 0;
}

// URL 比較キー(pathname + search)。相対 URL(manifest)と絶対 URL(request.url)を
// 同一土俵で照合するため、オリジンを落として path/query だけを見る。
function urlKey(rawUrl) {
  try {
    const u = new URL(rawUrl, "https://alinea.local");
    return u.pathname + u.search;
  } catch {
    return rawUrl;
  }
}

// LRU + サイズ eviction(offline-viewer.ts の evictManifests と一致)。
function evictManifests(manifests, limits) {
  const evicted = [];
  const survivors = [];
  for (const m of manifests) {
    if (m.bytes > limits.maxPaperBytes) evicted.push(m);
    else survivors.push(m);
  }
  survivors.sort((a, b) => a.lastAccessedAt - b.lastAccessedAt);
  while (survivors.length > limits.maxPapers) {
    const victim = survivors.shift();
    if (victim) evicted.push(victim);
  }
  let total = survivors.reduce((s, m) => s + m.bytes, 0);
  while (total > limits.maxTotalBytes && survivors.length > 0) {
    const victim = survivors.shift();
    if (victim) {
      evicted.push(victim);
      total -= victim.bytes;
    }
  }
  return { kept: survivors, evicted };
}

const LIMITS = {
  maxPapers: MAX_PAPERS,
  maxPaperBytes: MAX_PAPER_BYTES,
  maxTotalBytes: MAX_TOTAL_BYTES,
};

async function openViewerCache() {
  return caches.open(VIEWER_CACHE);
}

async function readJson(cache, url, fallback) {
  const res = await cache.match(url);
  if (!res) return fallback;
  try {
    return await res.json();
  } catch {
    return fallback;
  }
}

async function writeJson(cache, url, value) {
  await cache.put(
    url,
    new Response(JSON.stringify(value), { headers: { "content-type": "application/json" } }),
  );
}

async function readManifests(cache) {
  return readJson(cache, MANIFEST_URL, []);
}

async function writeManifests(cache, manifests) {
  await writeJson(cache, MANIFEST_URL, manifests);
}

async function readActiveUser(cache) {
  return readJson(cache, ACTIVE_USER_URL, null);
}

async function deleteEntries(cache, manifests) {
  for (const m of manifests) {
    for (const u of m.urls) {
      await cache.delete(u);
    }
  }
}

async function setActiveUser(userId) {
  const cache = await openViewerCache();
  await writeJson(cache, ACTIVE_USER_URL, userId ?? null);
}

async function cachePaper(input) {
  const cache = await openViewerCache();
  const manifests = await readManifests(cache);

  let bytes = 0;
  for (const u of input.urls) {
    const res = await cache.match(u);
    if (res) bytes += estimateResponseBytes(res);
  }

  const next = {
    userId: input.userId,
    itemId: input.itemId,
    revisionId: input.revisionId,
    urls: [...input.urls],
    bytes,
    lastAccessedAt: Date.now(),
  };

  const withoutSelf = manifests.filter((m) => m.itemId !== input.itemId);
  const combined = [...withoutSelf, next];
  const { kept, evicted } = evictManifests(combined, LIMITS);
  await deleteEntries(cache, evicted);
  await writeManifests(cache, kept);
}

async function purgeUser(userId) {
  const cache = await openViewerCache();
  const manifests = await readManifests(cache);
  const target = manifests.filter((m) => m.userId === userId);
  const remaining = manifests.filter((m) => m.userId !== userId);
  await deleteEntries(cache, target);
  await writeManifests(cache, remaining);
}

// アクティブユーザーが所有する論文の cache のみをフォールバック候補にする。
async function matchActiveUserCache(request) {
  const cache = await openViewerCache();
  const activeUser = await readActiveUser(cache);
  if (!activeUser) return undefined;
  // manifest は相対 URL を保持しうる一方 request.url は絶対 URL。pathname+search で照合する。
  const target = urlKey(request.url);
  const manifests = await readManifests(cache);
  const owned = manifests.some(
    (m) => m.userId === activeUser && m.urls.some((u) => urlKey(u) === target),
  );
  if (!owned) return undefined;
  return cache.match(request);
}

// network-first + 例外時のみ cache フォールバック(auth 応答は無改変で素通し)。
async function handleViewerApiRequest(request) {
  let networkResponse;
  try {
    networkResponse = await fetch(request);
  } catch (networkError) {
    // ★ここに来るのは throw(=ネットワーク到達不能)のときだけ。
    const cached = await matchActiveUserCache(request);
    if (cached) return cached;
    throw networkError; // cache 無し → 失敗を伝播(成功で覆い隠さない)。
  }
  // ★応答が返った(200/401/403/404/500 いずれも)→ 無改変で素通し。
  if (networkResponse.ok) {
    try {
      const cache = await openViewerCache();
      await cache.put(request, networkResponse.clone());
    } catch {
      // warm cache 失敗は無視。
    }
  }
  return networkResponse;
}

// ── ライフサイクル ────────────────────────────────────────────────────────────
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

// ── メッセージ(client → SW) ───────────────────────────────────────────────────
// SET_ACTIVE_USER / CACHE_PAPER / PURGE_USER。PURGE_USER は完了通知(ports 経由)して
// client 側が待てるようにする(ログアウト/削除→login 遷移前に purge を待つ)。
self.addEventListener("message", (event) => {
  const data = event.data;
  if (!data || typeof data !== "object") return;
  const reply = (payload) => {
    const port = event.ports && event.ports[0];
    if (port) port.postMessage(payload);
  };

  if (data.type === "SET_ACTIVE_USER") {
    event.waitUntil(setActiveUser(data.userId ?? null));
  } else if (data.type === "CACHE_PAPER") {
    event.waitUntil(
      cachePaper({
        userId: data.userId,
        itemId: data.itemId,
        revisionId: data.revisionId,
        urls: Array.isArray(data.urls) ? data.urls : [],
      }),
    );
  } else if (data.type === "PURGE_USER") {
    event.waitUntil(
      purgeUser(data.userId)
        .then(() => reply({ type: "PURGE_USER_DONE", userId: data.userId, ok: true }))
        .catch(() => reply({ type: "PURGE_USER_DONE", userId: data.userId, ok: false })),
    );
  }
});

// ── fetch ────────────────────────────────────────────────────────────────────
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

  // v2: 対象 viewer API(GET)→ network-first + 例外時のみ cache フォールバック。
  //     auth/非対象 API はこの分岐に入らないため従来どおり素通しする。
  if (isSameOrigin && isCacheableViewerRequest(request.url)) {
    event.respondWith(handleViewerApiRequest(request));
    return;
  }

  // v2: /papers/{itemId} ナビゲーションの「ネットワーク失敗時」だけ /offline シェルを返す。
  //     ネットワークが応答を返した場合(401→login リダイレクト含む)は素通しする。
  if (isSameOrigin && request.mode === "navigate" && url.pathname.startsWith("/papers/")) {
    event.respondWith(handleViewerNavigation(request));
    return;
  }

  // それ以外(HTML ナビゲーション・auth・非対象 API・その他)は素通し。
  // respondWith を呼ばない = ブラウザ既定のネットワーク処理に委ねる。
});

// /papers/* ナビゲーション: network-first。throw(オフライン)時のみ /offline シェルへ。
async function handleViewerNavigation(request) {
  try {
    return await fetch(request);
  } catch (networkError) {
    const cache = await caches.open(STATIC_CACHE);
    const shell = await cache.match("/offline");
    if (shell) return shell;
    throw networkError;
  }
}

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
