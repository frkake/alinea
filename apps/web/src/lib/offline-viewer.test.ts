import { beforeEach, describe, expect, test, vi } from "vitest";
import {
  MAX_PAPERS,
  MAX_PAPER_BYTES,
  MAX_TOTAL_BYTES,
  OFFLINE_VIEWER_CACHE,
  OFFLINE_VIEWER_MESSAGE,
  createOfflineViewerRuntime,
  evictManifests,
  isCacheableViewerRequest,
  type OfflinePaperManifest,
} from "./offline-viewer";

/**
 * Task 23: 直近 10 論文のオフライン閲覧(SW キャッシュ)の中核ロジック検証。
 *
 * 最重要の不変条件(plan §4「401 を Service Worker のキャッシュで成功応答へ置換しない」):
 *   fetch() が throw したとき(=ネットワーク障害)だけ cache を返す。
 *   401 / 403 / 404 / 500 を含む「あらゆる HTTP レスポンス」はそのまま素通しする。
 *
 * その他:
 *   - 対象 GET は viewer init / document / translation units / figures / references / assets に限定。
 *   - LRU 10 本上限、1 論文 50 MiB、全体 200 MiB 超過論文は丸ごと evict。
 *   - ログアウト / 別ユーザー切替後は前ユーザーのキャッシュを選択しない(per-user 分離)。
 */

// ---- テスト用の最小 Cache / CacheStorage 実装(SW の caches を模す) --------------------

class FakeCache {
  private store = new Map<string, Response>();

  private key(request: RequestInfo | URL): string {
    if (typeof request === "string") return request;
    if (request instanceof URL) return request.toString();
    return (request as Request).url;
  }

  async put(request: RequestInfo | URL, response: Response): Promise<void> {
    this.store.set(this.key(request), response);
  }

  async match(request: RequestInfo | URL): Promise<Response | undefined> {
    const hit = this.store.get(this.key(request));
    return hit ? hit.clone() : undefined;
  }

  async delete(request: RequestInfo | URL): Promise<boolean> {
    return this.store.delete(this.key(request));
  }

  async keys(): Promise<Request[]> {
    return [...this.store.keys()].map((url) => new Request(url));
  }

  // テスト用
  rawKeys(): string[] {
    return [...this.store.keys()];
  }
}

class FakeCacheStorage {
  private caches = new Map<string, FakeCache>();

  async open(name: string): Promise<FakeCache> {
    let c = this.caches.get(name);
    if (!c) {
      c = new FakeCache();
      this.caches.set(name, c);
    }
    return c;
  }

  async has(name: string): Promise<boolean> {
    return this.caches.has(name);
  }

  async keys(): Promise<string[]> {
    return [...this.caches.keys()];
  }

  async delete(name: string): Promise<boolean> {
    return this.caches.delete(name);
  }

  cache(name: string): FakeCache | undefined {
    return this.caches.get(name);
  }
}

const ORIGIN = "https://app.example.test";

function url(path: string): string {
  return `${ORIGIN}${path}`;
}

/** content-length ヘッダで見かけのバイト数を宣言する(実バッファは確保しない)。 */
function sizedResponse(bytes: number, status = 200, body = "x"): Response {
  return new Response(body, {
    status,
    headers: { "content-length": String(bytes), "content-type": "application/json" },
  });
}

function makeManifest(over: Partial<OfflinePaperManifest> & { itemId: string }): OfflinePaperManifest {
  return {
    userId: "user-a",
    revisionId: `rev-${over.itemId}`,
    urls: [url(`/api/library-items/${over.itemId}/viewer`)],
    bytes: 1_000,
    lastAccessedAt: 0,
    ...over,
  };
}

// ---------------------------------------------------------------------------------------

describe("isCacheableViewerRequest — 対象 GET の限定", () => {
  test("viewer init / document / units / figures / references / assets を許可する", () => {
    expect(isCacheableViewerRequest(url("/api/library-items/li_1/viewer"))).toBe(true);
    expect(isCacheableViewerRequest(url("/api/revisions/rev_1/document"))).toBe(true);
    expect(
      isCacheableViewerRequest(url("/api/revisions/rev_1/translations/natural/units")),
    ).toBe(true);
    expect(isCacheableViewerRequest(url("/api/revisions/rev_1/figures"))).toBe(true);
    expect(isCacheableViewerRequest(url("/api/revisions/rev_1/references"))).toBe(true);
    expect(isCacheableViewerRequest(url("/api/assets/asset_1"))).toBe(true);
  });

  test("auth / mutation / 無関係 API は対象外", () => {
    expect(isCacheableViewerRequest(url("/api/auth/me"))).toBe(false);
    expect(isCacheableViewerRequest(url("/api/library-items/li_1"))).toBe(false);
    expect(isCacheableViewerRequest(url("/api/chat/threads"))).toBe(false);
    expect(isCacheableViewerRequest(url("/api/settings"))).toBe(false);
    expect(isCacheableViewerRequest(url("/papers/li_1"))).toBe(false);
  });
});

describe("evictManifests — LRU + サイズ eviction", () => {
  test("11 本目で最古論文を 1 本 evict し 10 本に収める", () => {
    const manifests: OfflinePaperManifest[] = [];
    for (let i = 0; i < MAX_PAPERS + 1; i++) {
      manifests.push(makeManifest({ itemId: `li_${i}`, lastAccessedAt: i, bytes: 1_000 }));
    }
    const { kept, evicted } = evictManifests(manifests, {
      maxPapers: MAX_PAPERS,
      maxPaperBytes: MAX_PAPER_BYTES,
      maxTotalBytes: MAX_TOTAL_BYTES,
    });
    expect(kept).toHaveLength(MAX_PAPERS);
    expect(evicted.map((m) => m.itemId)).toEqual(["li_0"]); // 最古(lastAccessedAt=0)
    expect(kept.some((m) => m.itemId === "li_0")).toBe(false);
  });

  test("1 論文 50 MiB 超は丸ごと evict する(LRU とは独立)", () => {
    const fat = makeManifest({ itemId: "fat", lastAccessedAt: 999, bytes: MAX_PAPER_BYTES + 1 });
    const small = makeManifest({ itemId: "small", lastAccessedAt: 1, bytes: 1_000 });
    const { kept, evicted } = evictManifests([fat, small], {
      maxPapers: MAX_PAPERS,
      maxPaperBytes: MAX_PAPER_BYTES,
      maxTotalBytes: MAX_TOTAL_BYTES,
    });
    expect(evicted.map((m) => m.itemId)).toContain("fat"); // 最新でもサイズ超過で消える
    expect(kept.map((m) => m.itemId)).toEqual(["small"]);
  });

  test("全体 200 MiB 超は上限内に収まるまで最古から evict する", () => {
    const big = 60 * 1024 * 1024; // 60 MiB × 4 = 240 MiB > 200 MiB
    const manifests = [
      makeManifest({ itemId: "p0", lastAccessedAt: 10, bytes: big }),
      makeManifest({ itemId: "p1", lastAccessedAt: 20, bytes: big }),
      makeManifest({ itemId: "p2", lastAccessedAt: 30, bytes: big }),
      makeManifest({ itemId: "p3", lastAccessedAt: 40, bytes: big }),
    ];
    const { kept, evicted } = evictManifests(manifests, {
      maxPapers: MAX_PAPERS,
      maxPaperBytes: MAX_PAPER_BYTES + big, // per-paper 制限は無効化して total だけを見る
      maxTotalBytes: MAX_TOTAL_BYTES,
    });
    const total = kept.reduce((s, m) => s + m.bytes, 0);
    expect(total).toBeLessThanOrEqual(MAX_TOTAL_BYTES);
    expect(evicted.map((m) => m.itemId)).toContain("p0"); // 最古から
  });
});

describe("createOfflineViewerRuntime — network-first + auth 安全性 + per-user 分離", () => {
  let caches: FakeCacheStorage;
  let now: number;

  beforeEach(() => {
    caches = new FakeCacheStorage();
    now = 1_000;
  });

  function runtime(fetchImpl: typeof fetch) {
    return createOfflineViewerRuntime({
      caches: caches as unknown as CacheStorage,
      fetch: fetchImpl,
      now: () => now,
    });
  }

  const VIEWER_URL = url("/api/library-items/li_1/viewer");

  async function seedPaper(userId: string, itemId: string, bytes = 2_000): Promise<void> {
    const cache = await caches.open(OFFLINE_VIEWER_CACHE);
    const u = url(`/api/library-items/${itemId}/viewer`);
    await cache.put(u, sizedResponse(bytes, 200, "cached-body"));
    const rt = runtime(async () => sizedResponse(bytes, 200, "cached-body"));
    await rt.setActiveUser(userId);
    await rt.cachePaper({ userId, itemId, revisionId: `rev-${itemId}`, urls: [u] });
  }

  test("正常時: network-first で最新レスポンスを返し、200 を warm cache する", async () => {
    const fetchImpl = vi.fn(async () => sizedResponse(1_234, 200, "fresh"));
    const rt = runtime(fetchImpl as unknown as typeof fetch);
    await rt.setActiveUser("user-a");

    const res = await rt.handleApiRequest(new Request(VIEWER_URL));
    expect(res.status).toBe(200);
    expect(await res.text()).toBe("fresh");
    expect(fetchImpl).toHaveBeenCalledTimes(1);
  });

  test("[認証安全性] fetch が 401 を返したら、cache に成功応答があっても 401 を素通しする", async () => {
    await seedPaper("user-a", "li_1", 2_000); // cache に 200 が存在
    const fetchImpl = vi.fn(async () => new Response("unauth", { status: 401 }));
    const rt = runtime(fetchImpl as unknown as typeof fetch);
    await rt.setActiveUser("user-a");

    const res = await rt.handleApiRequest(new Request(VIEWER_URL));
    expect(res.status).toBe(401); // cache の 200 で置換しない
    expect(await res.text()).toBe("unauth");
  });

  test("[認証安全性] 403 / 404 / 500 も cache で置換せずそのまま返す", async () => {
    await seedPaper("user-a", "li_1", 2_000);
    for (const status of [403, 404, 500]) {
      const rt = runtime((async () => new Response("err", { status })) as unknown as typeof fetch);
      await rt.setActiveUser("user-a");
      const res = await rt.handleApiRequest(new Request(VIEWER_URL));
      expect(res.status).toBe(status);
    }
  });

  test("fetch が throw したとき(=ネットワーク障害)だけ cache を返す", async () => {
    await seedPaper("user-a", "li_1", 2_000);
    const rt = runtime((async () => {
      throw new TypeError("Failed to fetch");
    }) as unknown as typeof fetch);
    await rt.setActiveUser("user-a");

    const res = await rt.handleApiRequest(new Request(VIEWER_URL));
    expect(res.status).toBe(200);
    expect(await res.text()).toBe("cached-body");
  });

  test("ネットワーク障害かつ cache 無し → 例外を伝播(素通し失敗、成功で覆い隠さない)", async () => {
    const rt = runtime((async () => {
      throw new TypeError("Failed to fetch");
    }) as unknown as typeof fetch);
    await rt.setActiveUser("user-a");
    await expect(rt.handleApiRequest(new Request(VIEWER_URL))).rejects.toThrow();
  });

  test("manifest に相対 URL が入っていてもオフラインで cache フォールバックできる(絶対 URL の request と照合)", async () => {
    // buildPaperCacheUrls は相対パスを返すため、manifest は相対 URL を保持しうる。
    // 一方 SW の fetch request.url は絶対 URL。両者を pathname で照合できることを保証する。
    const cache = await caches.open(OFFLINE_VIEWER_CACHE);
    const relative = "/api/library-items/li_1/viewer";
    await cache.put(url(relative), sizedResponse(2_000, 200, "cached-body"));
    const seed = runtime(async () => sizedResponse(2_000, 200, "cached-body"));
    await seed.setActiveUser("user-a");
    await seed.cachePaper({
      userId: "user-a",
      itemId: "li_1",
      revisionId: "rev-li_1",
      urls: [relative], // ← 相対 URL を保存
    });

    const rt = runtime((async () => {
      throw new TypeError("offline");
    }) as unknown as typeof fetch);
    await rt.setActiveUser("user-a");
    const res = await rt.handleApiRequest(new Request(VIEWER_URL)); // ← 絶対 URL で要求
    expect(res.status).toBe(200);
    expect(await res.text()).toBe("cached-body");
  });

  test("[per-user 分離] 別ユーザーに切替後は前ユーザーの cache を返さない", async () => {
    await seedPaper("user-a", "li_1", 2_000); // user-a のキャッシュ
    const rt = runtime((async () => {
      throw new TypeError("offline");
    }) as unknown as typeof fetch);

    // user-b に切替 → user-a のキャッシュはオフラインでも選択されない
    await rt.setActiveUser("user-b");
    await expect(rt.handleApiRequest(new Request(VIEWER_URL))).rejects.toThrow();

    // user-a に戻すと再びオフラインで復帰する
    await rt.setActiveUser("user-a");
    const res = await rt.handleApiRequest(new Request(VIEWER_URL));
    expect(res.status).toBe(200);
  });

  test("[per-user 分離] PURGE_USER 後は当該ユーザーの cache が消える", async () => {
    await seedPaper("user-a", "li_1", 2_000);
    const rt = runtime((async () => {
      throw new TypeError("offline");
    }) as unknown as typeof fetch);
    await rt.setActiveUser("user-a");

    await rt.purgeUser("user-a");
    await expect(rt.handleApiRequest(new Request(VIEWER_URL))).rejects.toThrow();

    const cache = caches.cache(OFFLINE_VIEWER_CACHE);
    expect(cache?.rawKeys().includes(VIEWER_URL)).toBe(false);
  });

  test("cachePaper は 11 本目で最古を evict し、その cache エントリも削除する", async () => {
    const rt = runtime((async () => sizedResponse(1_000, 200)) as unknown as typeof fetch);
    await rt.setActiveUser("user-a");

    for (let i = 0; i < MAX_PAPERS + 1; i++) {
      now = 1_000 + i; // lastAccessedAt を単調増加させる
      const u = url(`/api/library-items/li_${i}/viewer`);
      const cache = await caches.open(OFFLINE_VIEWER_CACHE);
      await cache.put(u, sizedResponse(1_000, 200));
      await rt.cachePaper({ userId: "user-a", itemId: `li_${i}`, revisionId: `rev_${i}`, urls: [u] });
    }

    const manifests = await rt.debugManifests();
    expect(manifests).toHaveLength(MAX_PAPERS);
    expect(manifests.some((m) => m.itemId === "li_0")).toBe(false); // 最古が消えた
    const cache = caches.cache(OFFLINE_VIEWER_CACHE);
    expect(cache?.rawKeys().includes(url("/api/library-items/li_0/viewer"))).toBe(false);
  });

  test("cachePaper は 1 論文 50 MiB 超を保存せず evict する", async () => {
    const rt = runtime((async () => sizedResponse(MAX_PAPER_BYTES + 1, 200)) as unknown as typeof fetch);
    await rt.setActiveUser("user-a");
    const u = url("/api/library-items/li_fat/viewer");
    const cache = await caches.open(OFFLINE_VIEWER_CACHE);
    await cache.put(u, sizedResponse(MAX_PAPER_BYTES + 1, 200));
    await rt.cachePaper({ userId: "user-a", itemId: "li_fat", revisionId: "rev_fat", urls: [u] });

    const manifests = await rt.debugManifests();
    expect(manifests.some((m) => m.itemId === "li_fat")).toBe(false);
    expect(cache.rawKeys().includes(u)).toBe(false);
  });

  test("SET_ACTIVE_USER / CACHE_PAPER / PURGE_USER の message 型を公開する", () => {
    expect(OFFLINE_VIEWER_MESSAGE.SET_ACTIVE_USER).toBe("SET_ACTIVE_USER");
    expect(OFFLINE_VIEWER_MESSAGE.CACHE_PAPER).toBe("CACHE_PAPER");
    expect(OFFLINE_VIEWER_MESSAGE.PURGE_USER).toBe("PURGE_USER");
  });
});
