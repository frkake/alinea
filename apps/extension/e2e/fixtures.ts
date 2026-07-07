/* eslint-disable react-hooks/rules-of-hooks -- Playwright fixture の `use` は React hook ではない */
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  chromium,
  expect,
  test as base,
  type APIRequestContext,
  type BrowserContext,
  type Cookie,
} from "@playwright/test";
import { extensionDist } from "./_env";

const MAILPIT = "http://localhost:8025";
const LINK_RE = /https?:\/\/[^\s]+\/api\/auth\/email\/verify\?token=[^\s]+/;

/**
 * 拡張をロードした persistent context を新規に起動する(共有 / 使い捨ての双方で使う)。
 * 拡張は headless では読み込めないため `--headless=new` で起動する(DISPLAY 不要)。
 * `userDataDir` を明示すると同一プロファイル(= chrome.storage.local を含む)で再起動できる
 * (XT-10: コンテキスト再起動後もキューが残ることの検証に使う)。省略時は使い捨ての一時
 * ディレクトリを毎回生成する(既存呼び出しはすべてこの既定動作)。
 */
export async function createExtensionContext(userDataDir?: string): Promise<BrowserContext> {
  const dir = userDataDir ?? mkdtempSync(join(tmpdir(), "yk-ext-"));
  return chromium.launchPersistentContext(dir, {
    headless: false,
    args: [
      "--headless=new",
      "--no-sandbox",
      // ローカルのみに接続する。企業プロキシ(HTTPS_PROXY 等)への迂回を無効化する。
      "--proxy-server=direct://",
      "--proxy-bypass-list=<-loopback>",
      `--disable-extensions-except=${extensionDist}`,
      `--load-extension=${extensionDist}`,
    ],
    viewport: { width: 900, height: 700 },
    locale: "ja-JP",
    timezoneId: "Asia/Tokyo",
  });
}

/** context から拡張 ID を取り出す(service worker の URL から)。 */
export async function extensionIdOf(context: BrowserContext): Promise<string> {
  let [sw] = context.serviceWorkers();
  if (!sw) sw = await context.waitForEvent("serviceworker");
  return sw.url().split("/")[2] ?? "";
}

/**
 * 拡張ロード context と ID は test スコープで生成する(Playwright 拡張テストの標準形)。
 * ログインは ensureLoggedIn が dev のセッション Cookie を 1 度だけ実取得し、以後の context には
 * addCookies で使い回す(auth/email/request 5/600s のレート制限を消費しない)。
 */
export const test = base.extend<{ extContext: BrowserContext; extensionId: string }>({
  // eslint-disable-next-line no-empty-pattern -- 依存フィクスチャなし(Playwright の形)
  extContext: async ({}, use) => {
    const context = await createExtensionContext();
    await use(context);
    await context.close();
  },
  extensionId: async ({ extContext }, use) => {
    await use(await extensionIdOf(extContext));
  },
});

export { expect };

/** popup.html を対象タブ URL 付きで開くための URL(WXT_E2E フック)。 */
export function popupUrl(extensionId: string, tabUrl: string, tabTitle = "arXiv"): string {
  const q = new URLSearchParams({ tab_url: tabUrl, tab_title: tabTitle });
  return `chrome-extension://${extensionId}/popup.html?${q.toString()}`;
}

let savedSession: Cookie | null = null;

async function extractLink(request: APIRequestContext, address: string): Promise<string> {
  for (let i = 0; i < 30; i += 1) {
    const res = await request.get(`${MAILPIT}/api/v1/messages?limit=20`);
    if (res.ok()) {
      const data = (await res.json()) as {
        messages?: Array<{ ID: string; To?: Array<{ Address: string }> }>;
      };
      const msg = (data.messages ?? []).find((m) =>
        (m.To ?? []).some((t) => t.Address.toLowerCase() === address.toLowerCase()),
      );
      if (msg) {
        const detail = await request.get(`${MAILPIT}/api/v1/message/${msg.ID}`);
        const body = (await detail.json()) as { Text?: string; HTML?: string };
        const m = (body.Text ?? "").match(LINK_RE) ?? (body.HTML ?? "").match(LINK_RE);
        if (m) return m[0];
      }
    }
    await new Promise((r) => setTimeout(r, 500));
  }
  throw new Error(`login link for ${address} not found`);
}

/**
 * context を dev としてログイン状態にする。初回はメールリンク実経路で Cookie を取得して保存し、
 * 以後は保存済み Cookie を addCookies で注入する(実 auth 要求は worker あたり 1 回だけ)。
 */
export async function ensureLoggedIn(context: BrowserContext): Promise<void> {
  if (savedSession) {
    await context.addCookies([savedSession]);
    return;
  }
  const page = await context.newPage();
  await context.request.delete(`${MAILPIT}/api/v1/messages`);
  await page.goto("http://localhost:3000/login");
  await page.locator("#login-email").fill("dev@yakudoku.test");
  await page.getByRole("button", { name: "ログインリンクを送信" }).click();
  await expect(page.getByText("ログインリンクを送信しました")).toBeVisible({ timeout: 30_000 });
  const link = await extractLink(context.request, "dev@yakudoku.test");
  await page.goto(link);
  await expect(page).toHaveURL(/\/dashboard$/);
  await page.close();
  const cookie = (await context.cookies("http://localhost:3000")).find(
    (c) => c.name === "yk_session",
  );
  if (cookie) savedSession = cookie;
}
