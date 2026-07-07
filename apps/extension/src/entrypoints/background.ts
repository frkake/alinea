// バックグラウンド(M0-36)。ツールバーバッジの状態機械 + アクティブジョブのポーリング。
// 3a §2.3・§5.5。MV3 service worker は SSE を維持できないためポーリング方式(plans/01 §3.1)。
//
// 注: 3a §5.5 はプリレンダ PNG の setIcon 切替を規定するが、Task 33 のバッジ契約
// (badgeStateFor → {color,text})は setBadgeText/setBadgeBackgroundColor で表現する
// (琥珀ドット=●)。PNG スピナー巡回と chrome.alarms 安全網は permissions を
// [activeTab, storage] に保つため本スライスでは省略(deviations/followups)。
import { defineBackground } from "wxt/utils/define-background";
import { browser } from "wxt/browser";

import { apiGetJob, apiMe } from "@/lib/api";
import { badgeStateFor, type BadgeState } from "@/lib/badge";
import { getActiveJobs, removeActiveJob } from "@/lib/storage";

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

export default defineBackground(() => {
  // service worker 起動 / 再起動時。
  browser.runtime.onStartup.addListener(() => void runLoop());
  browser.runtime.onInstalled.addListener(() => void runLoop());
  // ポップアップが yk_active_jobs を更新したらポーリングを開始/再開(3a §2.3)。
  browser.storage.onChanged.addListener((changes, area) => {
    if (area === "local" && changes.yk_active_jobs) void runLoop();
  });
  void runLoop();
});
