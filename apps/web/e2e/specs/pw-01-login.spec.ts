import { expect, test } from "@playwright/test";

/**
 * PW-01(plans/12 §4.3): ログイン(メールリンク)→ ダッシュボード表示・ヘッダにプロダクト名・
 * ベルアイコン。メールリンク認証自体は global.setup.ts が実経路で通す(= PY-AUTH-02 の E2E 版)。
 * 本 spec は認証済みセッションで、ログイン後の既定画面(M1-10 以降は /dashboard)とヘッダを検証する。
 */
test.describe("PW-01 ログイン→ダッシュボード", () => {
  test("認証後 / は /dashboard へ振り分けられ、ヘッダにプロダクト名とベルがある", async ({
    page,
  }) => {
    await page.goto("/");
    await expect(page).toHaveURL(/\/dashboard$/);

    // ヘッダのプロダクト名「訳読 / YAKUDOKU」(docs/00・VT-UI-01 の E2E 版)。
    const header = page.locator("header").first();
    await expect(header.getByText("訳読", { exact: true })).toBeVisible();
    await expect(header.getByText("YAKUDOKU", { exact: true })).toBeVisible();

    // 通知ベル。
    await expect(page.getByRole("button", { name: "通知" })).toBeVisible();

    // ダッシュボード本体(すぐ読むキュー見出し)。
    await expect(page.getByRole("heading", { name: "すぐ読むキュー", level: 2 })).toBeVisible();
  });
});
