// API ラッパ(3a §1・§2.1)。@yakudoku/api-client の生成 SDK を拡張向けに設定する薄い層。
// - セッションクッキー(yk_session)共有: credentials:"include"。
// - baseUrl は WXT_API_BASE(開発 http://localhost:3000)。拡張は別オリジンなので絶対 URL が必須。
import {
  authMe,
  client,
  collectionsList,
  ingestArxiv,
  ingestCheck,
  ingestPdf,
  ingestRecent,
  jobsGet,
  libraryItemsDelete,
  libraryItemsUpdate,
  type CollectionListItem,
  type IngestArxivRequest,
  type IngestArxivResponse,
  type IngestCheckResponse,
  type IngestRecentItem,
  type JobOut,
  type MeResponse,
} from "@yakudoku/api-client";

import type { Status } from "./status";

const env = import.meta.env as unknown as Record<string, string | undefined>;

/** API オリジン。全 API 呼び出しと「サイトで開く」タブの起点(3a §1)。
 * 変数名は Task 31 の WXT_API_BASE を優先し、3a §1 の WXT_APP_ORIGIN もフォールバックで受ける。 */
export const API_BASE = env.WXT_API_BASE ?? env.WXT_APP_ORIGIN ?? "http://localhost:3000";

// 生成クライアントを拡張向けに再設定(この bundle 内の singleton にのみ影響)。
client.setConfig({ baseUrl: API_BASE, credentials: "include" });

/** サイト内 URL を絶対 URL 化(新規タブ・ビューア遷移に使う)。 */
export function siteUrl(path: string): string {
  return `${API_BASE}${path.startsWith("/") ? "" : "/"}${path}`;
}

/** GET /api/auth/me。401(未ログイン)なら null。 */
export async function apiMe(): Promise<MeResponse | null> {
  const res = await authMe();
  if (res.response.status === 401) return null;
  if (!res.data) throw new Error(`auth/me failed: ${res.response.status}`);
  return res.data;
}

/** GET /api/ingest/check。 */
export async function apiCheck(url: string): Promise<IngestCheckResponse> {
  const res = await ingestCheck({ query: { url } });
  if (!res.data) throw new Error(`ingest/check failed: ${res.response.status}`);
  return res.data;
}

export type SaveOutcome =
  | { kind: "accepted"; data: IngestArxivResponse }
  | { kind: "duplicate" }
  // ネットワーク/5xx/429: 再試行対象(3a §5.1)。
  | { kind: "retryable"; status: number; message?: string }
  // 422 等の恒久エラー: 再送しても直らない。
  | { kind: "permanent"; status: number; message: string };

/**
 * POST /api/ingest/arxiv。Idempotency-Key を付与(二重登録防止・plans/03 §3.2)。
 * 202→accepted / 409 または duplicate フラグ→duplicate / 5xx・429・ネットワーク→retryable。
 */
export async function apiSaveArxiv(
  body: IngestArxivRequest,
  idempotencyKey: string = crypto.randomUUID(),
): Promise<SaveOutcome> {
  try {
    const res = await ingestArxiv({
      body,
      headers: { "Idempotency-Key": idempotencyKey },
    });
    const status = res.response.status;
    if (status === 409) return { kind: "duplicate" };
    if (res.data) {
      return res.data.duplicate ? { kind: "duplicate" } : { kind: "accepted", data: res.data };
    }
    if (status === 429 || status >= 500) {
      return { kind: "retryable", status, message: readProblemMessage(res.error, "") };
    }
    return { kind: "permanent", status, message: readProblemMessage(res.error, "送信に失敗しました") };
  } catch {
    // fetch reject(ネットワーク不通)は再試行対象。
    return { kind: "retryable", status: 0 };
  }
}

/** GET /api/jobs/{job_id}。 */
export async function apiGetJob(jobId: string): Promise<JobOut> {
  const res = await jobsGet({ path: { job_id: jobId } });
  if (!res.data) throw new Error(`jobs/${jobId} failed: ${res.response.status}`);
  return res.data;
}

/** GET /api/ingest/recent?limit=。取得失敗時は空配列(フッタ非表示・§4.4 決定)。 */
export async function apiGetRecent(limit = 3): Promise<IngestRecentItem[]> {
  try {
    const res = await ingestRecent({ query: { limit } });
    return res.data?.items ?? [];
  } catch {
    return [];
  }
}

/**
 * GET /api/collections(保存前フォームのコレクション選択・docs/10 §2 の M2 決定)。
 * 取得失敗時は空配列(欄自体は非表示にしない — 選択肢が「なし」のみになるだけ)。
 */
export async function apiListCollections(): Promise<CollectionListItem[]> {
  try {
    const res = await collectionsList();
    return res.data?.items ?? [];
  } catch {
    return [];
  }
}

/** PATCH /api/library-items/{id} でステータス変更(3a §5.3)。成功で true。 */
export async function apiPatchStatus(itemId: string, status: Status): Promise<boolean> {
  try {
    const res = await libraryItemsUpdate({ path: { item_id: itemId }, body: { status } });
    return Boolean(res.data);
  } catch {
    return false;
  }
}

/**
 * DELETE /api/library-items/{id} で取り込みをキャンセル(docs/08 §2.2)。
 * ライブラリ項目ごと削除する(取り込み中の部分データも含めて消える)。成功で true。
 */
export async function apiCancelIngest(itemId: string): Promise<boolean> {
  try {
    const res = await libraryItemsDelete({ path: { item_id: itemId } });
    return res.response.status === 204;
  } catch {
    return false;
  }
}

/** POST /api/ingest/pdf の multipart `meta` フィールド(plans/03 §3.3 IngestPdfMeta 逐語)。 */
export interface PdfSendMeta {
  source_url: string;
  title_guess: string | null;
  status: Status;
  tags: string[];
  collection_id: string | null;
  quick_note: string | null;
}

/** 409 duplicate 応答の `existing`(既存 LibraryItem の要約。plans/03 §3.2 と同形)。 */
export interface DuplicateExisting {
  library_item_id: string;
  status: string;
  added_at: string;
  progress_pct: number;
  last_position: { section_display: string; saved_at: string } | null;
}

function readProblemMessage(error: unknown, fallback: string): string {
  if (error && typeof error === "object") {
    const detail = (error as { detail?: unknown }).detail;
    if (typeof detail === "string" && detail.trim() !== "") return detail;
    const title = (error as { title?: unknown }).title;
    if (typeof title === "string" && title.trim() !== "") return title;
  }
  return fallback;
}

function readDuplicateExisting(error: unknown): DuplicateExisting | undefined {
  if (!error || typeof error !== "object") return undefined;
  const existing = (error as { existing?: unknown }).existing;
  if (!existing || typeof existing !== "object") return undefined;
  return existing as DuplicateExisting;
}

export type PdfSendOutcome =
  | { kind: "accepted"; data: IngestArxivResponse }
  | { kind: "duplicate"; existing?: DuplicateExisting }
  // ネットワーク/5xx/429: §11.3 の失敗キュー対象(3a §5.1 と同方針)。
  | { kind: "retryable"; status: number }
  // 413/415/422 等の恒久エラー: サーバーの Problem detail をそのまま表示する。
  | { kind: "permanent"; status: number; message: string };

/**
 * POST /api/ingest/pdf(plans/10 §11.2)。タブ内 PDF バイト列の直接送信。
 * multipart の `meta` は JSON 文字列化して送る(生成クライアントが FormData 化する)。
 */
export async function apiSendPdf(
  file: Blob,
  meta: PdfSendMeta,
  idempotencyKey: string = crypto.randomUUID(),
): Promise<PdfSendOutcome> {
  try {
    const res = await ingestPdf({
      body: { file, meta: JSON.stringify(meta) },
      headers: { "Idempotency-Key": idempotencyKey },
    });
    const status = res.response.status;
    if (status === 409) return { kind: "duplicate", existing: readDuplicateExisting(res.error) };
    if (res.data) return { kind: "accepted", data: res.data };
    if (status === 429 || status >= 500) return { kind: "retryable", status };
    return { kind: "permanent", status, message: readProblemMessage(res.error, "送信に失敗しました") };
  } catch {
    return { kind: "retryable", status: 0 };
  }
}
