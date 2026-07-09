// 送信失敗キュー(plans/10 §11.3・docs/08 §6)。ブラウザ再起動をまたいで保持する。
// - arXiv URL 保存の失敗: storage.local `queue:failedSaves`(小さい JSON のみ)。
// - PDF 送信の失敗: IndexedDB(`alinea-ext` v1 / `failed_uploads`)。PDF バイト列は
//   storage.local の既定上限(10MB)に収まらないため。
// 決定(plans/10 §11.3・docs/08 §6): 自動再送はしない。再試行は FailedQueueBanner からの
// 明示操作のみ(PDF の「自動送信はしない」原則、および P3「黙って失われない」と整合)。
// 決定: 各種別 10 件が上限。超過時は最古(failedAt 最小)を破棄し、呼び出し側に通知する
// (黙って捨てない)。同一 id(=Idempotency-Key)の再登録は重複排除(上書き)する。
import { browser } from "wxt/browser";

import type { IngestArxivRequest } from "@alinea/api-client";

import type { PdfSendMeta } from "./api";

export const QUEUE_LIMIT = 10;

/** ネットワーク断由来の失敗を表すセンチネル(lastError)。表示は describeQueueError で行う。 */
export const NETWORK_ERROR = "network";

export function describeQueueError(lastError: string): string {
  return lastError === NETWORK_ERROR ? "ネットワークに接続できませんでした" : lastError;
}

export interface EnqueueResult<T> {
  record: T;
  /** 上限超過で破棄された最古の1件(通知用)。破棄が無ければ null。 */
  evicted: T | null;
}

// --- arXiv URL 保存の失敗(storage.local) --------------------------------------------

export interface FailedSaveRecord {
  /** = 保存時に使った Idempotency-Key(再試行でも同一キーを使い回す)。 */
  id: string;
  kind: "arxiv";
  request: IngestArxivRequest;
  title: string;
  failedAt: number;
  lastError: string;
}

const FAILED_SAVES_KEY = "queue:failedSaves";

export async function listFailedSaves(): Promise<FailedSaveRecord[]> {
  const store = await browser.storage.local.get(FAILED_SAVES_KEY);
  const value = store[FAILED_SAVES_KEY];
  const list = Array.isArray(value) ? (value as FailedSaveRecord[]) : [];
  return [...list].sort((a, b) => a.failedAt - b.failedAt);
}

async function setFailedSaves(records: FailedSaveRecord[]): Promise<void> {
  await browser.storage.local.set({ [FAILED_SAVES_KEY]: records });
}

export async function enqueueFailedSave(
  record: FailedSaveRecord,
): Promise<EnqueueResult<FailedSaveRecord>> {
  const withoutDup = (await listFailedSaves()).filter((r) => r.id !== record.id);
  const next = [...withoutDup, record];
  let evicted: FailedSaveRecord | null = null;
  if (next.length > QUEUE_LIMIT) {
    evicted = next.shift() ?? null; // 先頭(最古)を破棄
  }
  await setFailedSaves(next);
  return { record, evicted };
}

export async function removeFailedSave(id: string): Promise<void> {
  await setFailedSaves((await listFailedSaves()).filter((r) => r.id !== id));
}

export async function updateFailedSaveError(id: string, lastError: string): Promise<void> {
  await setFailedSaves(
    (await listFailedSaves()).map((r) => (r.id === id ? { ...r, lastError } : r)),
  );
}

// --- PDF 送信の失敗(IndexedDB。blob 保持のため) --------------------------------------

export interface FailedUploadRecord {
  /** = 送信時に使った Idempotency-Key。 */
  id: string;
  kind: "pdf";
  meta: PdfSendMeta;
  blob: Blob;
  titleGuess: string | null;
  failedAt: number;
  lastError: string;
}

const DB_NAME = "alinea-ext";
const DB_VERSION = 1;
const STORE_NAME = "failed_uploads";

function openDb(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, DB_VERSION);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains(STORE_NAME)) {
        db.createObjectStore(STORE_NAME, { keyPath: "id" });
      }
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error ?? new Error("IndexedDB を開けませんでした"));
  });
}

async function withStore<T>(
  mode: IDBTransactionMode,
  run: (store: IDBObjectStore) => IDBRequest<T>,
): Promise<T> {
  const db = await openDb();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_NAME, mode);
    const store = tx.objectStore(STORE_NAME);
    const req = run(store);
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error ?? new Error("IndexedDB 操作に失敗しました"));
  });
}

export async function listFailedUploads(): Promise<FailedUploadRecord[]> {
  const all = await withStore<FailedUploadRecord[]>("readonly", (store) => store.getAll());
  return [...all].sort((a, b) => a.failedAt - b.failedAt);
}

export async function enqueueFailedUpload(
  record: FailedUploadRecord,
): Promise<EnqueueResult<FailedUploadRecord>> {
  const withoutDup = (await listFailedUploads()).filter((r) => r.id !== record.id);
  await withStore("readwrite", (store) => store.put(record));
  let evicted: FailedUploadRecord | null = null;
  // put 後の総件数は withoutDup.length + 1(新規なら +1、上書きなら件数不変で同値)。
  if (withoutDup.length + 1 > QUEUE_LIMIT) {
    const oldest = withoutDup[0];
    if (oldest) {
      await withStore("readwrite", (store) => store.delete(oldest.id));
      evicted = oldest;
    }
  }
  return { record, evicted };
}

export async function removeFailedUpload(id: string): Promise<void> {
  await withStore("readwrite", (store) => store.delete(id));
}

export async function updateFailedUploadError(id: string, lastError: string): Promise<void> {
  const current = (await listFailedUploads()).find((r) => r.id === id);
  if (!current) return;
  await withStore("readwrite", (store) => store.put({ ...current, lastError }));
}
