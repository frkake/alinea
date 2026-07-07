import { expect, type Page } from "@playwright/test";

/** Rectified Flow シード論文(§14。全 spec の共通データ源)。 */
export const RF_TITLE_PREFIX = "Flow Straight and Fast";
export const RF_ARXIV_ID = "2209.03003";

/** 状態変更リクエストの CSRF Origin(plans/03 §1.3)。ブラウザ由来 fetch と同一。 */
export const ORIGIN = "http://localhost:3000";

interface LibrarySummary {
  id: string;
  paper?: { title?: string; arxiv_id?: string | null };
}

export async function listLibraryItems(page: Page): Promise<LibrarySummary[]> {
  const res = await page.request.get("/api/library-items?limit=100");
  expect(res.ok()).toBeTruthy();
  const data = (await res.json()) as { items?: LibrarySummary[] };
  return data.items ?? [];
}

/** シードされた Rectified Flow 論文の library_item id を解決する。 */
export async function resolveRfItemId(page: Page): Promise<string> {
  const items = await listLibraryItems(page);
  const rf = items.find(
    (i) => i.paper?.arxiv_id === RF_ARXIV_ID || (i.paper?.title ?? "").startsWith(RF_TITLE_PREFIX),
  );
  if (!rf) throw new Error("Rectified Flow seed item not found in library");
  return rf.id;
}

export interface IngestResult {
  job_id: string;
  library_item_id: string;
  paper_id: string;
  duplicate: boolean;
}

/** 取り込み開始(拡張と同じ API を直呼び。plans/12 PW-03)。Origin/Idempotency-Key を付ける。 */
export async function ingestArxiv(page: Page, url: string): Promise<IngestResult> {
  const res = await page.request.post("/api/ingest/arxiv", {
    headers: {
      "Content-Type": "application/json",
      Origin: ORIGIN,
      "Idempotency-Key": `e2e-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    },
    data: { url, status: "planned", tags: [], quick_note: null, collection_id: null },
  });
  expect(res.status(), await res.text()).toBe(202);
  return (await res.json()) as IngestResult;
}

/** テストごとに一意な arXiv URL(重複 409 回避。モックは任意 ID を決定的に配信)。 */
export function freshArxivUrl(): string {
  const n = (Date.now() % 90000) + 10000; // 10000..99999
  return `https://arxiv.org/abs/2401.${String(n).padStart(5, "0")}`;
}

export interface JobState {
  stage: string;
  status: string;
  progress_pct: number;
}

export async function getJob(page: Page, jobId: string): Promise<JobState> {
  const res = await page.request.get(`/api/jobs/${jobId}`);
  expect(res.ok()).toBeTruthy();
  return (await res.json()) as JobState;
}

/** ジョブが succeeded になるまでポーリング(取り込みは FakeLLM + モックで数秒)。 */
export async function waitForJob(page: Page, jobId: string, timeoutMs = 45_000): Promise<JobState> {
  const deadline = Date.now() + timeoutMs;
  let last: JobState = { stage: "queued", status: "queued", progress_pct: 0 };
  while (Date.now() < deadline) {
    last = await getJob(page, jobId);
    if (last.status === "succeeded" || last.status === "failed") return last;
    await new Promise((r) => setTimeout(r, 1000));
  }
  return last;
}
