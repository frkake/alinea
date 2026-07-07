/* eslint-disable no-restricted-imports -- E2E 補助モジュールは親ディレクトリから import する(src の @/ エイリアス規約は適用外) */
import { expect, test, type Page } from "@playwright/test";
import { resolveRfItemId } from "../fixtures/api";

/**
 * VR-1a/1b/1c(plans/12 §9.2): ビューア 3 画面のビジュアルリグレッション。
 * 基準ビューポート 1440×900・シードデータ・animations disabled・caret hide(config)。
 * 決定性のため前回位置バナー(相対時刻を含む)は撮影前に閉じ、時刻依存要素を排除する。
 *
 * 注記: フォントは CI/ローカルとも決定的ラスタライズのため Docker イメージ
 * (mcr.microsoft.com/playwright:vX-noble)内実行が正(§9.1)。本環境はローカル Chromium で
 * 基準を生成しており、CI(異なるフォントラスタライズ)では再生成が必要になり得る(followups)。
 */

async function prepViewer(page: Page, itemId: string, mode: string): Promise<void> {
  await page.goto(`/papers/${itemId}?mode=${mode}`);
  await expect(page.getByRole("radiogroup", { name: "表示モード" })).toBeVisible();
  // 本文ブロック描画を待つ(networkidle は SSE /api/events が開きっぱなしで到達しないため使わない)。
  await expect(page.locator("[data-block-id]").first()).toBeVisible();
  // 相対時刻を含む前回位置バナーを閉じる(決定性)。
  const dismiss = page.getByRole("button", { name: "閉じる" });
  if (await dismiss.isVisible().catch(() => false)) await dismiss.click();
  // KaTeX / フォント適用の安定待ち。
  await page.waitForTimeout(800);
  await page.mouse.move(0, 0); // ホバー状態を消す
}

test.describe("VR ビューア", () => {
  test("VR-1a 対訳モード", async ({ page }) => {
    const itemId = await resolveRfItemId(page);
    await prepViewer(page, itemId, "parallel");
    await expect(page).toHaveScreenshot("vr-1a-parallel.png");
  });

  test("VR-1b 訳文モード", async ({ page }) => {
    const itemId = await resolveRfItemId(page);
    await prepViewer(page, itemId, "translation");
    await expect(page).toHaveScreenshot("vr-1b-translation.png");
  });

  test("VR-1c ダーク + 図表タブ", async ({ page, context }) => {
    await context.addCookies([{ name: "yk_theme", value: "dark", domain: "localhost", path: "/" }]);
    const itemId = await resolveRfItemId(page);
    await prepViewer(page, itemId, "translation");
    await page.getByRole("tab", { name: "図表" }).click();
    await page.waitForTimeout(300);
    await page.mouse.move(0, 0);
    await expect(page).toHaveScreenshot("vr-1c-dark-figures.png");
  });
});
