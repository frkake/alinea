import { expect, test as setup, type APIRequestContext, type Page } from "@playwright/test";

/**
 * 認証セットアップ(plans/12 §4.2)。メールリンク認証を実経路で通す(= PY-AUTH-02 の E2E 版)。
 * (1) /login でメール送信 →(2) Mailpit API から最新メールのリンク抽出 →(3) リンク遷移で
 * セッション確立 →(4) storageState を保存。dev(所有ライブラリあり)と member(共有・担当用)。
 */

const MAILPIT = "http://localhost:8025";
const LINK_RE = /https?:\/\/[^\s]+\/api\/auth\/email\/verify\?token=[^\s]+/;

async function clearMailpit(request: APIRequestContext): Promise<void> {
  await request.delete(`${MAILPIT}/api/v1/messages`);
}

async function extractLoginLink(request: APIRequestContext, address: string): Promise<string> {
  for (let attempt = 0; attempt < 30; attempt += 1) {
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
        const match = (body.Text ?? "").match(LINK_RE) ?? (body.HTML ?? "").match(LINK_RE);
        if (match) return match[0];
      }
    }
    await new Promise((r) => setTimeout(r, 500));
  }
  throw new Error(`login link for ${address} not found in Mailpit`);
}

async function loginViaEmail(
  page: Page,
  request: APIRequestContext,
  address: string,
): Promise<void> {
  await clearMailpit(request);
  await page.goto("/login");
  await page.locator("#login-email").fill(address);
  await page.getByRole("button", { name: "ログインリンクを送信" }).click();
  // メール送信 fetch は Next dev のオンデマンドコンパイル + API プロキシ経由で遅延しうるため
  // 余裕を持って待つ(通常は 1s 未満)。
  await expect(page.getByText("ログインリンクを送信しました")).toBeVisible({ timeout: 30_000 });

  const link = await extractLoginLink(request, address);
  await page.goto(link); // 302 -> / -> /dashboard(M1-10。セッション Cookie が張られる)
  await expect(page).toHaveURL(/\/dashboard$/);
  await expect(page.getByRole("heading", { name: "すぐ読むキュー", level: 2 })).toBeVisible();
}

setup("authenticate dev user", async ({ page, request }) => {
  await loginViaEmail(page, request, "dev@alinea.test");
  await page.context().storageState({ path: "e2e/.auth/user.json" });
});

setup("authenticate member user", async ({ page, request }) => {
  await loginViaEmail(page, request, "member@alinea.test");
  await page.context().storageState({ path: "e2e/.auth/member.json" });
});
