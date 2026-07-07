import {
  createExtensionContext,
  ensureLoggedIn,
  expect,
  extensionIdOf,
  popupUrl,
  test,
} from "./fixtures";

/**
 * 拡張 E2E(plans/12 §5.2)。ビルド済み拡張をロードした persistent context で popup.html を
 * 直接開いて各状態を検証する。XT-01(未ログイン)→ ログイン → 以降(ログイン済み)の順に直列実行する。
 */

// service worker 評価内で使う chrome 拡張 API の最小型(TS 用宣言。実体は SW ランタイム)。
declare const chrome: {
  storage: { local: { set(items: Record<string, unknown>): Promise<void> } };
  action: {
    getBadgeText(details: Record<string, never>): Promise<string>;
    getBadgeBackgroundColor(details: Record<string, never>): Promise<number[]>;
  };
};

const SEED_ARXIV = "https://arxiv.org/abs/2209.03003"; // シード済み(状態3 用)

function freshArxiv(): string {
  const n = (Date.now() % 90000) + 10000;
  return `https://arxiv.org/abs/2403.${String(n).padStart(5, "0")}`;
}

test.describe.serial("拡張 E2E", () => {
  test("XT-01 未ログインではログイン導線が出る", async () => {
    // 共有 context はログイン済みになるため、未ログイン検証は使い捨て context で行う。
    const ctx = await createExtensionContext();
    try {
      const id = await extensionIdOf(ctx);
      const page = await ctx.newPage();
      await page.goto(popupUrl(id, SEED_ARXIV));
      await expect(page.getByText("保存にはログインが必要です。")).toBeVisible();
      await expect(page.getByRole("button", { name: "ログイン" })).toBeVisible();
    } finally {
      await ctx.close();
    }
  });

  test("XT-02 状態1(保存前): 書誌プレビュー + 品質A見込み", async ({ extContext, extensionId }) => {
    await ensureLoggedIn(extContext);
    const url = freshArxiv();
    const id = url.split("/abs/")[1];
    const page = await extContext.newPage();
    await page.goto(popupUrl(extensionId, url));
    await expect(page.getByText(`Mock Paper for ${id}`)).toBeVisible();
    await expect(page.getByText("品質レベル A 見込み")).toBeVisible();
    await page.close();
  });

  test("XT-03/04 保存操作(Enter)→ 状態2(保存直後)進捗 + サイトで開く", async ({
    extContext,
    extensionId,
  }) => {
    await ensureLoggedIn(extContext);
    const url = freshArxiv();
    const page = await extContext.newPage();
    await page.goto(popupUrl(extensionId, url));
    await expect(page.getByText("品質レベル A 見込み")).toBeVisible();
    // Enter キーで保存(3a §5.2)。
    await page.locator(".ext-note-input").press("Enter");
    // 状態2: ヘッダ「保存しました」+ パイプライン進捗 + 「サイトで開く ↗」。
    await expect(page.locator(".ext-header-title")).toHaveText("保存しました");
    await expect(page.getByRole("button", { name: "サイトで開く ↗" })).toBeVisible();
    await page.close();
  });

  test("XT-05 状態3(既にライブラリ): 重複保存 UI なし + 続きから開く/ステータス変更", async ({
    extContext,
    extensionId,
  }) => {
    await ensureLoggedIn(extContext);
    const page = await extContext.newPage();
    await page.goto(popupUrl(extensionId, SEED_ARXIV));
    await expect(page.locator(".ext-header-title")).toHaveText("既にライブラリにあります");
    await expect(page.getByRole("button", { name: "続きから開く ↗" })).toBeVisible();
    await expect(page.getByRole("button", { name: "ステータス変更 ▾" })).toBeVisible();
    // 保存前フォームの品質見込み行は出ない(重複保存 UI なし)。
    await expect(page.getByText("品質レベル A 見込み")).toHaveCount(0);
    await page.close();
  });

  test("XT-07 フッタ「直近の取り込み」が処理履歴を表示", async ({ extContext, extensionId }) => {
    await ensureLoggedIn(extContext);
    const page = await extContext.newPage();
    await page.goto(popupUrl(extensionId, SEED_ARXIV));
    const footer = page.locator(".ext-footer");
    await expect(footer.getByText("直近の取り込み")).toBeVisible();
    await expect(footer.locator(".ext-recent-row").first()).toBeVisible();
    await page.close();
  });

  test("XT-09 バッジ: アクティブジョブありで琥珀ドット(background service worker)", async ({
    extContext,
  }) => {
    let [sw] = extContext.serviceWorkers();
    if (!sw) sw = await extContext.waitForEvent("serviceworker");
    // アクティブジョブを storage に注入 → onChanged で runLoop → pollOnce → バッジ更新。
    await sw.evaluate(async () => {
      await chrome.storage.local.set({ yk_active_jobs: ["xt09-nonexistent-job"] });
    });
    await expect
      .poll(async () => sw.evaluate(() => chrome.action.getBadgeText({})), { timeout: 10_000 })
      .toBe("●");
    const color = await sw.evaluate(() => chrome.action.getBadgeBackgroundColor({}));
    // AMBER #C49432 = rgb(196,148,50)。
    expect(color).toEqual([196, 148, 50, 255]);
    // 後片付け。
    await sw.evaluate(async () => {
      await chrome.storage.local.set({ yk_active_jobs: [] });
    });
  });
});
