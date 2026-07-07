// chrome.storage.local ラッパ(3a §2.3)。アクティブジョブ ID を保持し、
// バックグラウンドのバッジ・ポーリングと共有する。
import { browser } from "wxt/browser";

const ACTIVE_JOBS_KEY = "yk_active_jobs";

/** 進行中ジョブ ID の一覧(background がポーリング対象にする)。 */
export async function getActiveJobs(): Promise<string[]> {
  const record = await browser.storage.local.get(ACTIVE_JOBS_KEY);
  const value = record[ACTIVE_JOBS_KEY];
  return Array.isArray(value) ? (value as string[]) : [];
}

export async function setActiveJobs(ids: string[]): Promise<void> {
  await browser.storage.local.set({ [ACTIVE_JOBS_KEY]: ids });
}

/** ジョブ ID を追加(重複は無視)。保存成功時に呼ぶ。 */
export async function addActiveJob(id: string): Promise<void> {
  const current = await getActiveJobs();
  if (current.includes(id)) return;
  await setActiveJobs([...current, id]);
}

/** ジョブ ID を除去(succeeded/failed になったとき)。 */
export async function removeActiveJob(id: string): Promise<void> {
  const current = await getActiveJobs();
  const next = current.filter((jobId) => jobId !== id);
  if (next.length !== current.length) await setActiveJobs(next);
}
