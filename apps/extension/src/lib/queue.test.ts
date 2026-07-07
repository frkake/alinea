// node 環境で実行(既定の jsdom だと wxt/testing の内部処理が esbuild の
// TextEncoder/Uint8Array 不変条件チェックに失敗するため。本テストは DOM に依存しない)。
// @vitest-environment node
import { afterEach, beforeEach, expect, test, vi } from "vitest";

// VT-XTU-03: 失敗送信の永続化・重複排除・上限件数での最古破棄(plans/10 §11.3)。
// arXiv 保存の失敗(storage.local)は wxt/testing の fakeBrowser で、PDF 送信の失敗
// (IndexedDB)は本ファイル内の最小限の IndexedDB フェイクで検証する
// (fake-indexeddb パッケージは本ワークスペースに未配線のため。deviations 参照)。
vi.mock("wxt/browser", async () => {
  const { fakeBrowser } = await import("wxt/testing");
  return { browser: fakeBrowser };
});

// --- 最小限の IndexedDB フェイク(queue.ts が使う put/get/getAll/delete のみ対応) ------

interface FakeRequest<T> {
  result: T | undefined;
  error: Error | null;
  onsuccess: (() => void) | null;
  onerror: (() => void) | null;
}

function makeRequest<T>(): FakeRequest<T> {
  return { result: undefined, error: null, onsuccess: null, onerror: null };
}

function resolveRequest<T>(req: FakeRequest<T>, value: T): FakeRequest<T> {
  req.result = value;
  queueMicrotask(() => req.onsuccess?.());
  return req;
}

function installFakeIndexedDb(): Map<string, { id: string }> {
  const rows = new Map<string, { id: string }>();
  const store = {
    put: (value: { id: string }) => resolveRequest(makeRequest<string>(), (rows.set(value.id, value), value.id)),
    get: (key: string) => resolveRequest(makeRequest<unknown>(), rows.get(key)),
    getAll: () => resolveRequest(makeRequest<unknown[]>(), Array.from(rows.values())),
    delete: (key: string) => resolveRequest(makeRequest<undefined>(), (rows.delete(key), undefined)),
  };
  const db = {
    objectStoreNames: { contains: () => true },
    createObjectStore: () => store,
    transaction: () => ({ objectStore: () => store }),
  };
  const fakeIndexedDb = {
    open: () => {
      const req = makeRequest<typeof db>();
      req.result = db;
      queueMicrotask(() => {
        (req as unknown as { onupgradeneeded?: () => void }).onupgradeneeded?.();
        req.onsuccess?.();
      });
      return req;
    },
  };
  vi.stubGlobal("indexedDB", fakeIndexedDb);
  return rows;
}

beforeEach(() => {
  installFakeIndexedDb();
});

afterEach(async () => {
  vi.unstubAllGlobals();
  const { fakeBrowser } = await import("wxt/testing");
  fakeBrowser.reset();
});

// --- arXiv URL 保存の失敗キュー(storage.local) --------------------------------------

function makeSaveRecord(id: string, failedAt: number) {
  return {
    id,
    kind: "arxiv" as const,
    request: { url: `https://arxiv.org/abs/${id}`, status: "planned", tags: [], collection_id: null, quick_note: null },
    title: `Paper ${id}`,
    failedAt,
    lastError: "network",
  };
}

test("enqueueFailedSave persists across separate module calls (survives 'restart')", async () => {
  const { enqueueFailedSave, listFailedSaves } = await import("./queue");
  await enqueueFailedSave(makeSaveRecord("a", 1));
  const list = await listFailedSaves();
  expect(list).toHaveLength(1);
  expect(list[0]).toMatchObject({ id: "a", title: "Paper a" });
});

test("enqueueFailedSave dedupes by id (same Idempotency-Key overwrites, no duplicate row)", async () => {
  const { enqueueFailedSave, listFailedSaves } = await import("./queue");
  await enqueueFailedSave(makeSaveRecord("dup", 1));
  await enqueueFailedSave({ ...makeSaveRecord("dup", 2), lastError: "network-retry" });
  const list = await listFailedSaves();
  expect(list).toHaveLength(1);
  expect(list[0].lastError).toBe("network-retry");
});

test("enqueueFailedSave evicts the oldest entry once the 10-item cap is exceeded", async () => {
  const { enqueueFailedSave, listFailedSaves } = await import("./queue");
  for (let i = 0; i < 10; i += 1) {
    const result = await enqueueFailedSave(makeSaveRecord(`s${i}`, i));
    expect(result.evicted).toBeNull();
  }
  const overflow = await enqueueFailedSave(makeSaveRecord("s10", 10));
  expect(overflow.evicted).toMatchObject({ id: "s0" });
  const list = await listFailedSaves();
  expect(list).toHaveLength(10);
  expect(list.map((r) => r.id)).not.toContain("s0");
  expect(list.map((r) => r.id)).toContain("s10");
});

test("removeFailedSave deletes exactly the retried/discarded entry", async () => {
  const { enqueueFailedSave, listFailedSaves, removeFailedSave } = await import("./queue");
  await enqueueFailedSave(makeSaveRecord("keep", 1));
  await enqueueFailedSave(makeSaveRecord("gone", 2));
  await removeFailedSave("gone");
  const list = await listFailedSaves();
  expect(list.map((r) => r.id)).toEqual(["keep"]);
});

test("updateFailedSaveError updates lastError in place without removing the row", async () => {
  const { enqueueFailedSave, listFailedSaves, updateFailedSaveError } = await import("./queue");
  await enqueueFailedSave(makeSaveRecord("x", 1));
  await updateFailedSaveError("x", "network");
  const list = await listFailedSaves();
  expect(list[0].lastError).toBe("network");
});

// --- PDF 送信の失敗キュー(IndexedDB。blob を保持) --------------------------------------

function makeUploadRecord(id: string, failedAt: number) {
  return {
    id,
    kind: "pdf" as const,
    meta: { source_url: `https://host/${id}.pdf`, title_guess: null, status: "planned" as const, tags: [], collection_id: null, quick_note: null },
    blob: new Blob(["%PDF-"], { type: "application/pdf" }),
    titleGuess: null,
    failedAt,
    lastError: "network",
  };
}

test("enqueueFailedUpload persists the blob in IndexedDB", async () => {
  const { enqueueFailedUpload, listFailedUploads } = await import("./queue");
  await enqueueFailedUpload(makeUploadRecord("p1", 1));
  const list = await listFailedUploads();
  expect(list).toHaveLength(1);
  expect(list[0].blob).toBeInstanceOf(Blob);
  expect(list[0].blob.size).toBeGreaterThan(0);
});

test("enqueueFailedUpload dedupes by id (retry reuses the same Idempotency-Key)", async () => {
  const { enqueueFailedUpload, listFailedUploads } = await import("./queue");
  await enqueueFailedUpload(makeUploadRecord("dup", 1));
  await enqueueFailedUpload({ ...makeUploadRecord("dup", 2), lastError: "still failing" });
  const list = await listFailedUploads();
  expect(list).toHaveLength(1);
  expect(list[0].lastError).toBe("still failing");
});

test("enqueueFailedUpload evicts the oldest entry once the 10-item cap is exceeded", async () => {
  const { enqueueFailedUpload, listFailedUploads } = await import("./queue");
  for (let i = 0; i < 10; i += 1) {
    const result = await enqueueFailedUpload(makeUploadRecord(`u${i}`, i));
    expect(result.evicted).toBeNull();
  }
  const overflow = await enqueueFailedUpload(makeUploadRecord("u10", 10));
  expect(overflow.evicted).toMatchObject({ id: "u0" });
  const list = await listFailedUploads();
  expect(list).toHaveLength(10);
  expect(list.map((r) => r.id)).not.toContain("u0");
});

test("removeFailedUpload deletes exactly the retried/discarded entry", async () => {
  const { enqueueFailedUpload, listFailedUploads, removeFailedUpload } = await import("./queue");
  await enqueueFailedUpload(makeUploadRecord("keep", 1));
  await enqueueFailedUpload(makeUploadRecord("gone", 2));
  await removeFailedUpload("gone");
  const list = await listFailedUploads();
  expect(list.map((r) => r.id)).toEqual(["keep"]);
});

test("describeQueueError maps the network sentinel to a human message and passes through others", async () => {
  const { describeQueueError } = await import("./queue");
  expect(describeQueueError("network")).toBe("ネットワークに接続できませんでした");
  expect(describeQueueError("PDF として読み取れませんでした")).toBe("PDF として読み取れませんでした");
});
