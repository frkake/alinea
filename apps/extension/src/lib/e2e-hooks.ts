// タブ URL の取得と E2E フック(Task 31)。
// WXT_E2E=1 のときは popup を通常のページとして開き、?tab_url= / ?tab_title= で
// 現在タブの URL/タイトルを差し替えられるようにする(chrome.tabs に依存せずテスト可能)。
import { browser } from "wxt/browser";

const env = import.meta.env as unknown as Record<string, string | undefined>;

export const IS_E2E = env.WXT_E2E === "1";

export interface ActiveTab {
  url: string;
  title: string;
}

/** 現在タブの URL/タイトル。E2E モードでは URL クエリを優先する。 */
export async function getActiveTab(): Promise<ActiveTab> {
  if (IS_E2E && typeof location !== "undefined") {
    const params = new URLSearchParams(location.search);
    const tabUrl = params.get("tab_url");
    if (tabUrl) {
      return { url: tabUrl, title: params.get("tab_title") ?? "" };
    }
  }
  const tabs = await browser.tabs.query({ active: true, currentWindow: true });
  const tab = tabs[0];
  return { url: tab?.url ?? "", title: tab?.title ?? "" };
}
