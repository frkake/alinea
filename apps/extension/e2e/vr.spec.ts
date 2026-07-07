import {
  createExtensionContext,
  ensureLoggedIn,
  expect,
  extensionIdOf,
  popupUrl,
  test,
} from "./fixtures";

/**
 * VR-3a(plans/12 §9.2): 拡張ポップアップの状態別スクリーンショット。拡張ロードが必要なため
 * web の visual プロジェクトではなくこちらで撮影する。決定性のため、時刻を含む要素(追加日・
 * 前回位置)は mask し、保存前は固定 arXiv ID(未取り込み)を使う。3 状態: 未ログイン / 保存前 /
 * 既にライブラリ。
 *
 * 注記: フォントの決定的ラスタライズは Docker イメージ内実行が正(§9.1)。本環境はローカル
 * Chromium で基準生成のため、CI では再生成が必要になり得る(followups)。
 */
const VR_ARXIV = "https://arxiv.org/abs/2312.09990"; // 保存前用(取り込まれない固定 ID)
const SEED_ARXIV = "https://arxiv.org/abs/2209.03003"; // 既にライブラリ

test.describe.serial("VR-3a 拡張ポップアップ", () => {
  test("VR-3a 未ログイン", async () => {
    const ctx = await createExtensionContext();
    try {
      const id = await extensionIdOf(ctx);
      const page = await ctx.newPage();
      await page.goto(popupUrl(id, SEED_ARXIV));
      const popup = page.locator(".ext-popup");
      await expect(popup.getByText("保存にはログインが必要です。")).toBeVisible();
      await expect(popup).toHaveScreenshot("vr-3a-login.png");
    } finally {
      await ctx.close();
    }
  });

  test("VR-3a 保存前", async ({ extContext, extensionId }) => {
    await ensureLoggedIn(extContext);
    const page = await extContext.newPage();
    await page.goto(popupUrl(extensionId, VR_ARXIV));
    const popup = page.locator(".ext-popup");
    await expect(popup.getByText("品質レベル A 見込み")).toBeVisible();
    // フッタ「直近の取り込み」は dev の取り込み履歴で変動するため mask(決定性)。
    await expect(popup).toHaveScreenshot("vr-3a-saveform.png", {
      mask: [page.locator(".ext-footer")],
    });
    await page.close();
  });

  test("VR-3a 既にライブラリ", async ({ extContext, extensionId }) => {
    await ensureLoggedIn(extContext);
    const page = await extContext.newPage();
    await page.goto(popupUrl(extensionId, SEED_ARXIV));
    const popup = page.locator(".ext-popup");
    await expect(popup.getByRole("button", { name: "続きから開く ↗" })).toBeVisible();
    // 追加日・前回位置は相対時刻、フッタは取り込み履歴で変動するため mask(決定性)。
    await expect(popup).toHaveScreenshot("vr-3a-existing.png", {
      mask: [
        page.locator(".ext-existing-meta"),
        page.locator(".ext-last-position"),
        page.locator(".ext-footer"),
      ],
    });
    await page.close();
  });
});
