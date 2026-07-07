// バックグラウンド(M0-36)。ツールバーバッジの状態機械 + アクティブジョブのポーリング。
// 3a §2.3・§5.5。MV3 service worker は SSE を維持できないためポーリング方式(plans/01 §3.1)。
//
// 注: 3a §5.5 はプリレンダ PNG の setIcon 切替を規定するが、Task 33 のバッジ契約
// (badgeStateFor → {color,text})は setBadgeText/setBadgeBackgroundColor で表現する
// (琥珀ドット=●)。PNG スピナー巡回と chrome.alarms 安全網は permissions を
// [activeTab, storage] に保つため本スライスでは省略(deviations/followups)。
import { defineBackground } from "wxt/utils/define-background";
import { browser } from "wxt/browser";

import { apiCheck, apiGetJob, apiMe, apiSaveArxiv, siteUrl } from "@/lib/api";
import { badgeStateFor, type BadgeState } from "@/lib/badge";
import { isPillMessage, type PillMessage, type PillResult } from "@/lib/pill-protocol";
import { addActiveJob, getActiveJobs, removeActiveJob } from "@/lib/storage";

// バッジ状態機械は background.ts の一部(M0-36)。純粋判定は lib/badge に切り出し再エクスポートする。
export { badgeStateFor, type BadgeState } from "@/lib/badge";

const POLL_MS = 15_000;
const COMPLETE_BADGE_MS = 4_000;

let timer: ReturnType<typeof setTimeout> | undefined;
let completedUntil = 0;

async function applyBadge(state: BadgeState): Promise<void> {
  await browser.action.setBadgeText({ text: state.text });
  if (state.text && state.color) {
    await browser.action.setBadgeBackgroundColor({ color: state.color });
  }
}

/** アクティブジョブと未読を確認しバッジを更新。まだ処理中ジョブが残れば true。 */
async function pollOnce(): Promise<boolean> {
  const active = await getActiveJobs();
  const statuses: Array<{ status: string }> = [];
  let succeededSomething = false;

  for (const id of active) {
    try {
      const job = await apiGetJob(id);
      statuses.push({ status: job.status });
      if (job.status === "succeeded" || job.status === "failed") {
        await removeActiveJob(id);
        if (job.status === "succeeded") succeededSomething = true;
      }
    } catch {
      // 一時的な取得失敗は「処理中」として残す。
      statuses.push({ status: "running" });
    }
  }

  let unread = 0;
  try {
    const me = await apiMe();
    unread = me?.unread_notifications ?? 0;
  } catch {
    /* 未読が取れなくてもバッジは他条件で決める */
  }

  if (succeededSomething) completedUntil = Date.now() + COMPLETE_BADGE_MS;
  const justCompleted = Date.now() < completedUntil;

  await applyBadge(badgeStateFor(statuses, { unread, justCompleted }));

  const remaining = await getActiveJobs();
  return remaining.length > 0;
}

async function runLoop(): Promise<void> {
  if (timer) {
    clearTimeout(timer);
    timer = undefined;
  }
  const hasActive = await pollOnce();
  // 処理中ジョブが残る間、または完了チェック表示中は継続。
  if (hasActive || Date.now() < completedUntil) {
    timer = setTimeout(() => void runLoop(), POLL_MS);
  }
}

// arXiv ページ内ピル(plans/10 §10.3)。content script は API を直接呼ばず、background に
// 判定・保存を委譲する(same-site クッキー送信は拡張コンテキスト発が条件のため)。

/** 初期化: GET /api/ingest/check で表示状態を決める。判定失敗・未ログインは非表示(再試行しない)。 */
async function checkPill(url: string): Promise<PillResult> {
  try {
    const me = await apiMe();
    if (!me) return { state: "hidden" };
    const check = await apiCheck(url);
    return { state: check.saved ? "saved" : "idle" };
  } catch {
    return { state: "hidden" };
  }
}

/** クリック: 状態1の既定値(planned・タグ無し・コレクション無し・メモ無し)で保存する。 */
async function savePill(url: string): Promise<PillResult> {
  const outcome = await apiSaveArxiv({
    url,
    status: "planned",
    tags: [],
    collection_id: null,
    quick_note: null,
  });
  switch (outcome.kind) {
    case "accepted":
      await addActiveJob(outcome.data.job_id);
      void runLoop();
      return { state: "saved" };
    case "duplicate":
      return { state: "saved" };
    case "retryable":
      // キューには入れない(ピルは1クリック UI。再試行はポップアップに誘導。plans/10 §10.3 決定)。
      return { state: "error" };
    default:
      if (outcome.status === 401) {
        void browser.tabs.create({ url: siteUrl("/login?from=extension") });
        return { state: "idle" };
      }
      return { state: "error" };
  }
}

async function handlePillMessage(msg: PillMessage): Promise<PillResult> {
  if (msg.type === "PILL_CHECK") return checkPill(msg.url);
  return savePill(msg.url);
}

export default defineBackground(() => {
  // service worker 起動 / 再起動時。
  browser.runtime.onStartup.addListener(() => void runLoop());
  browser.runtime.onInstalled.addListener(() => void runLoop());
  // ポップアップが yk_active_jobs を更新したらポーリングを開始/再開(3a §2.3)。
  browser.storage.onChanged.addListener((changes, area) => {
    if (area === "local" && changes.yk_active_jobs) void runLoop();
  });
  browser.runtime.onMessage.addListener((msg: unknown, _sender, sendResponse) => {
    if (!isPillMessage(msg)) return undefined;
    handlePillMessage(msg).then(sendResponse);
    return true; // 非同期応答(sendResponse を後で呼ぶ)。
  });
  void runLoop();
});
