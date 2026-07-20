/**
 * オフライン閲覧(直近 N 論文)の中核ロジック(Task 23 / spec 2026-07-16-pwa-offline-design §B v2)。
 *
 * このモジュールは Service Worker(public/sw.js)と「同一の」振る舞いを表す唯一の実装であり、
 * jsdom 上でユニットテスト可能にするために SW から切り出してある。sw.js はここと等価な
 * バニラ JS を内包する(import 不可のため手写しで同期。差異が出たら本ファイルの契約が正)。
 *
 * ────────────────────────────────────────────────────────────────────────
 * 最重要の不変条件(plan §4「401 を Service Worker のキャッシュで成功応答へ置換しない」):
 *   ネットワークが応答を返した場合(200 でも 401/403/404/500 でも)は、その応答を無改変で返す。
 *   fetch() が throw したとき(=ネットワーク到達不能=オフライン)だけ cache フォールバックする。
 *   → 期限切れセッションでオンラインなら 401 が素通しし、通常の /login リダイレクトが働く。
 * ────────────────────────────────────────────────────────────────────────
 *
 * per-user 分離:
 *   各キャッシュ済み応答は「どのユーザーが取得したか(userId)」を manifest で記録する。
 *   アクティブユーザーが切り替わったら、旧ユーザーの応答はフォールバック候補から除外する
 *   (別アカウントに他人の本文が漏れない)。明示ログアウト/アカウント削除は purgeUser で
 *   当該ユーザーのエントリを cache ごと削除する。
 *
 * LRU + サイズ eviction:
 *   最大 10 論文 / 1 論文 50 MiB / 全体 200 MiB。超過分は論文単位で丸ごと evict する。
 */

/** キャッシュ名(rollback は VERSION を上げるだけで本機能を無効化できる)。 */
export const OFFLINE_VIEWER_VERSION = "v1";
export const OFFLINE_VIEWER_CACHE = `alinea-viewer-${OFFLINE_VIEWER_VERSION}`;
/** manifest(論文→URL群/バイト数/最終アクセス)を格納する予約 URL(cache 内の擬似エントリ)。 */
export const OFFLINE_VIEWER_MANIFEST_URL = "https://alinea.offline/__viewer_manifest__";
/** アクティブユーザー ID を格納する予約 URL。 */
export const OFFLINE_VIEWER_ACTIVE_USER_URL = "https://alinea.offline/__active_user__";

/** LRU / サイズ上限。 */
export const MAX_PAPERS = 10;
export const MAX_PAPER_BYTES = 50 * 1024 * 1024; // 50 MiB
export const MAX_TOTAL_BYTES = 200 * 1024 * 1024; // 200 MiB

/** postMessage の type(ServiceWorkerRegistration ↔ SW ↔ 本モジュールで共有)。 */
export const OFFLINE_VIEWER_MESSAGE = {
  SET_ACTIVE_USER: "SET_ACTIVE_USER",
  CACHE_PAPER: "CACHE_PAPER",
  PURGE_USER: "PURGE_USER",
} as const;

/** paper 単位のキャッシュ manifest(brief Step 3 の型)。 */
export type OfflinePaperManifest = {
  userId: string;
  itemId: string;
  revisionId: string;
  urls: string[];
  bytes: number;
  lastAccessedAt: number;
};

/**
 * オフライン閲覧対象の GET 判定(allow-list)。
 * 対象: viewer init / document / translation units / figures / references / assets のみ。
 * これ以外(auth・mutation・一覧など)は決して cache しない。
 */
const CACHEABLE_PATTERNS: RegExp[] = [
  /^\/api\/library-items\/[^/]+\/viewer$/, // viewer init
  /^\/api\/revisions\/[^/]+\/document$/, // document
  /^\/api\/revisions\/[^/]+\/translations\/[^/]+\/units$/, // translation units
  /^\/api\/revisions\/[^/]+\/figures$/, // figures
  /^\/api\/revisions\/[^/]+\/references$/, // references
  /^\/api\/assets\/[^/]+$/, // assets
];

export function isCacheableViewerRequest(rawUrl: string): boolean {
  let pathname: string;
  try {
    pathname = new URL(rawUrl).pathname;
  } catch {
    // 相対 URL 等
    pathname = rawUrl.split("?")[0] ?? rawUrl;
  }
  return CACHEABLE_PATTERNS.some((re) => re.test(pathname));
}

/** レスポンスの見かけバイト数を推定する(content-length を優先。無ければ 0)。 */
export function estimateResponseBytes(response: { headers: { get(name: string): string | null } }): number {
  const raw = response.headers.get("content-length");
  if (!raw) return 0;
  const n = Number.parseInt(raw, 10);
  return Number.isFinite(n) && n >= 0 ? n : 0;
}

export type EvictionLimits = {
  maxPapers: number;
  maxPaperBytes: number;
  maxTotalBytes: number;
};

export type EvictionResult = {
  kept: OfflinePaperManifest[];
  evicted: OfflinePaperManifest[];
};

/**
 * LRU + サイズ eviction を純関数で計算する。
 *   1) 1 論文が maxPaperBytes を超えるものは(最新でも)丸ごと evict。
 *   2) 残りを LRU 順(lastAccessedAt 昇順=古い順)で並べ、maxPapers を超える古い分を evict。
 *   3) さらに合計が maxTotalBytes を超える間、最古から evict。
 * 返り値の kept は「新しい順」ではなく元順不問。呼び出し側は itemId で扱う。
 */
export function evictManifests(
  manifests: OfflinePaperManifest[],
  limits: EvictionLimits,
): EvictionResult {
  const evicted: OfflinePaperManifest[] = [];

  // 1) per-paper サイズ超過を除外。
  const survivors: OfflinePaperManifest[] = [];
  for (const m of manifests) {
    if (m.bytes > limits.maxPaperBytes) {
      evicted.push(m);
    } else {
      survivors.push(m);
    }
  }

  // LRU 順(古い→新しい)。
  survivors.sort((a, b) => a.lastAccessedAt - b.lastAccessedAt);

  // 2) 本数上限: 超過分を古い方から evict。
  while (survivors.length > limits.maxPapers) {
    const victim = survivors.shift();
    if (victim) evicted.push(victim);
  }

  // 3) 合計サイズ上限: 超過する間、古い方から evict。
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

// ---------------------------------------------------------------------------------------
// ランタイム(SW の caches / fetch / clock を注入して動かす)
// ---------------------------------------------------------------------------------------

export type OfflineViewerRuntimeDeps = {
  caches: CacheStorage;
  fetch: typeof fetch;
  now?: () => number;
  limits?: Partial<EvictionLimits>;
};

export type CachePaperInput = {
  userId: string;
  itemId: string;
  revisionId: string;
  urls: string[];
};

export type OfflineViewerRuntime = {
  /** アクティブユーザーを設定する(切替時、旧ユーザーの応答は選択対象外になる)。 */
  setActiveUser(userId: string | null): Promise<void>;
  /** オンライン表示完了後に呼ばれ、応答+関連 asset を同一 paper group として記録する。 */
  cachePaper(input: CachePaperInput): Promise<void>;
  /** 指定ユーザーのキャッシュ(応答 + manifest)を完全に削除する(ログアウト/削除時)。 */
  purgeUser(userId: string): Promise<void>;
  /** network-first + 例外時のみ cache フォールバック。auth 応答は無改変で素通し。 */
  handleApiRequest(request: Request): Promise<Response>;
  /** テスト用: 現在の manifest 一覧。 */
  debugManifests(): Promise<OfflinePaperManifest[]>;
};

export function createOfflineViewerRuntime(deps: OfflineViewerRuntimeDeps): OfflineViewerRuntime {
  const now = deps.now ?? (() => Date.now());
  const limits: EvictionLimits = {
    maxPapers: deps.limits?.maxPapers ?? MAX_PAPERS,
    maxPaperBytes: deps.limits?.maxPaperBytes ?? MAX_PAPER_BYTES,
    maxTotalBytes: deps.limits?.maxTotalBytes ?? MAX_TOTAL_BYTES,
  };

  async function openCache(): Promise<Cache> {
    return deps.caches.open(OFFLINE_VIEWER_CACHE);
  }

  async function readJson<T>(cache: Cache, url: string, fallback: T): Promise<T> {
    const res = await cache.match(url);
    if (!res) return fallback;
    try {
      return (await res.json()) as T;
    } catch {
      return fallback;
    }
  }

  async function writeJson(cache: Cache, url: string, value: unknown): Promise<void> {
    await cache.put(
      url,
      new Response(JSON.stringify(value), {
        headers: { "content-type": "application/json" },
      }),
    );
  }

  async function readManifests(cache: Cache): Promise<OfflinePaperManifest[]> {
    return readJson<OfflinePaperManifest[]>(cache, OFFLINE_VIEWER_MANIFEST_URL, []);
  }

  async function writeManifests(cache: Cache, manifests: OfflinePaperManifest[]): Promise<void> {
    await writeJson(cache, OFFLINE_VIEWER_MANIFEST_URL, manifests);
  }

  async function readActiveUser(cache: Cache): Promise<string | null> {
    return readJson<string | null>(cache, OFFLINE_VIEWER_ACTIVE_USER_URL, null);
  }

  /** manifest から削除された論文の cache エントリ(url 群)を一掃する。 */
  async function deleteEntries(cache: Cache, manifests: OfflinePaperManifest[]): Promise<void> {
    for (const m of manifests) {
      for (const u of m.urls) {
        await cache.delete(u);
      }
    }
  }

  async function setActiveUser(userId: string | null): Promise<void> {
    const cache = await openCache();
    await writeJson(cache, OFFLINE_VIEWER_ACTIVE_USER_URL, userId);
  }

  async function cachePaper(input: CachePaperInput): Promise<void> {
    const cache = await openCache();
    const manifests = await readManifests(cache);

    // 対象論文の実バイト数を、cache 済み応答の content-length 合計から求める。
    let bytes = 0;
    for (const u of input.urls) {
      const res = await cache.match(u);
      if (res) bytes += estimateResponseBytes(res);
    }

    const next: OfflinePaperManifest = {
      userId: input.userId,
      itemId: input.itemId,
      revisionId: input.revisionId,
      urls: [...input.urls],
      bytes,
      lastAccessedAt: now(),
    };

    // 同一 itemId の既存 manifest は置き換える(revision 更新・再訪問での上書き)。
    const withoutSelf = manifests.filter((m) => m.itemId !== input.itemId);
    const combined = [...withoutSelf, next];

    const { kept, evicted } = evictManifests(combined, limits);
    // evict された論文(自分自身が per-paper 超過の場合も含む)の cache エントリを削除。
    await deleteEntries(cache, evicted);
    await writeManifests(cache, kept);
  }

  async function purgeUser(userId: string): Promise<void> {
    const cache = await openCache();
    const manifests = await readManifests(cache);
    const target = manifests.filter((m) => m.userId === userId);
    const remaining = manifests.filter((m) => m.userId !== userId);
    await deleteEntries(cache, target);
    await writeManifests(cache, remaining);
  }

  async function handleApiRequest(request: Request): Promise<Response> {
    // network-first: まずネットワークへ。
    let networkResponse: Response;
    try {
      networkResponse = await deps.fetch(request);
    } catch (networkError) {
      // ★ここに来るのは「ネットワーク到達不能(throw)」のときだけ。
      //   このときに限り cache フォールバックする。
      const cached = await matchActiveUserCache(request);
      if (cached) return cached;
      throw networkError; // cache 無し → 失敗をそのまま伝播(成功で覆い隠さない)。
    }

    // ★ネットワークが応答を返した(200/401/403/404/500 いずれも)→ 無改変で素通し。
    //   ここで cache の成功応答に差し替えることは絶対にしない(auth 安全性)。
    // 200 のときだけ warm cache する(cachePaper とは独立の日和見的更新)。
    if (networkResponse.ok && isCacheableViewerRequest(request.url)) {
      try {
        const cache = await openCache();
        await cache.put(request, networkResponse.clone());
      } catch {
        // warm cache 失敗は無視(本応答には影響しない)。
      }
    }
    return networkResponse;
  }

  /** アクティブユーザーが所有する論文の cache のみをフォールバック候補にする。 */
  async function matchActiveUserCache(request: Request): Promise<Response | undefined> {
    const cache = await openCache();
    const activeUser = await readActiveUser(cache);
    if (!activeUser) return undefined;

    // manifest は相対 URL(buildPaperCacheUrls)を保持しうる一方、request.url は絶対 URL。
    // pathname+search で照合して両者の差を吸収する。
    const target = urlKey(request.url);
    const manifests = await readManifests(cache);
    const owned = manifests.some(
      (m) => m.userId === activeUser && m.urls.some((u) => urlKey(u) === target),
    );
    if (!owned) return undefined;

    return cache.match(request);
  }

  async function debugManifests(): Promise<OfflinePaperManifest[]> {
    const cache = await openCache();
    return readManifests(cache);
  }

  return {
    setActiveUser,
    cachePaper,
    purgeUser,
    handleApiRequest,
    debugManifests,
  };
}

/**
 * URL 比較キー(pathname + search)。相対 URL(manifest)と絶対 URL(request.url)を
 * 同一土俵で照合するため、オリジンを落として path/query だけを見る。
 */
function urlKey(rawUrl: string): string {
  try {
    const u = new URL(rawUrl, "https://alinea.local");
    return u.pathname + u.search;
  } catch {
    return rawUrl;
  }
}

// ---------------------------------------------------------------------------------------
// クライアント側ヘルパ(React コンポーネントから SW へ postMessage する)
// いずれも serviceWorker 非対応/未 controller の環境では安全に no-op になる。
// ---------------------------------------------------------------------------------------

function activeServiceWorker(): ServiceWorker | null {
  if (typeof navigator === "undefined" || !("serviceWorker" in navigator)) return null;
  return navigator.serviceWorker.controller ?? null;
}

/** 論文ビューアのオフライン保存対象 URL 群を組み立てる(ViewerShell が使用)。 */
export function buildPaperCacheUrls(input: {
  itemId: string;
  revisionId: string;
  translationStyle?: string | null;
}): string[] {
  const urls = [
    `/api/library-items/${input.itemId}/viewer`,
    `/api/revisions/${input.revisionId}/document`,
    `/api/revisions/${input.revisionId}/figures`,
    `/api/revisions/${input.revisionId}/references`,
  ];
  const style = input.translationStyle ?? "natural";
  urls.push(`/api/revisions/${input.revisionId}/translations/${style}/units`);
  return urls;
}

/** アクティブ認証ユーザーを SW へ通知する(user 切替で旧ユーザーの応答を選択対象外にする)。 */
export function postActiveUser(userId: string | null): void {
  const sw = activeServiceWorker();
  sw?.postMessage({ type: OFFLINE_VIEWER_MESSAGE.SET_ACTIVE_USER, userId });
}

/** オンライン表示完了後に、論文の viewer データ+関連 asset を同一 group で保存させる。 */
export function postCachePaper(input: {
  userId: string;
  itemId: string;
  revisionId: string;
  urls: string[];
}): void {
  const sw = activeServiceWorker();
  sw?.postMessage({ type: OFFLINE_VIEWER_MESSAGE.CACHE_PAPER, ...input });
}

/**
 * 指定ユーザーのオフラインキャッシュを完全削除し、完了を待つ(ログアウト/アカウント削除)。
 * MessageChannel で PURGE_USER_DONE を待機する。SW 非対応・controller 不在・タイムアウト時は
 * 解決して呼び出し側の遷移をブロックしない(削除は付加的な後始末であり遷移を阻害しない)。
 */
export function purgeUserAndWait(userId: string, timeoutMs = 3_000): Promise<void> {
  const sw = activeServiceWorker();
  if (!sw || typeof MessageChannel === "undefined") return Promise.resolve();

  return new Promise<void>((resolve) => {
    const channel = new MessageChannel();
    let done = false;
    const finish = () => {
      if (done) return;
      done = true;
      channel.port1.onmessage = null;
      resolve();
    };
    channel.port1.onmessage = (event) => {
      const data = event.data as { type?: string } | null;
      if (data?.type === "PURGE_USER_DONE") finish();
    };
    setTimeout(finish, timeoutMs);
    sw.postMessage({ type: OFFLINE_VIEWER_MESSAGE.PURGE_USER, userId }, [channel.port2]);
  });
}
