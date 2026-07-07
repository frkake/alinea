/* eslint-disable no-restricted-imports -- E2E 補助モジュールは親ディレクトリから import する(src の @/ エイリアス規約は適用外) */
import { expect, test } from "@playwright/test";
import { resolveRfItemId } from "../fixtures/api";
import { openViewer } from "../fixtures/viewer";

/**
 * PW-09(plans/12 §4.3): 読書位置。シード論文には前回位置(reading_position)が入っている。
 * ビューアを開くと「続きから ↓」バナーが出て、1 クリックで復帰(バナーが消える)。読書位置は
 * サーバ側(viewer-init の last_position)に保存されるため、リロード/別コンテキストでも同位置から
 * 再開できる(= 別デバイス相当)。
 */
test.describe("PW-09 読書位置(続きから復帰)", () => {
  test("前回位置バナー → 1 クリック復帰", async ({ page }) => {
    const itemId = await resolveRfItemId(page);
    await openViewer(page, itemId, "translation");

    const banner = page.getByRole("status").filter({ hasText: "前回はここまで" });
    await expect(banner).toBeVisible();
    await banner.getByRole("button", { name: "続きから ↓" }).click();
    await expect(banner).toBeHidden();
  });

  test("読書位置はサーバ保存でリロード後も残る(別デバイス相当)", async ({ page }) => {
    const itemId = await resolveRfItemId(page);
    await openViewer(page, itemId, "translation");
    await expect(page.getByRole("status").filter({ hasText: "前回はここまで" })).toBeVisible();

    await page.reload();
    await expect(page.getByRole("radiogroup", { name: "表示モード" })).toBeVisible();
    await expect(page.getByRole("status").filter({ hasText: "前回はここまで" })).toBeVisible();
  });
});
