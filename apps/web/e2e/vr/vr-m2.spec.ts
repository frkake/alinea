/* eslint-disable no-restricted-imports -- E2E 補助モジュールは親ディレクトリから import する(src の @/ エイリアス規約は適用外) */
import { spawnSync } from "node:child_process";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { expect, test, type Page } from "@playwright/test";
import { ORIGIN, freshArxivUrl, ingestArxiv, resolveRfItemId, waitForJob } from "../fixtures/api";
import { openViewer } from "../fixtures/viewer";

const __dirname = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(__dirname, "..", "..", "..", "..");
const NO_PROXY = "localhost,127.0.0.1";

/**
 * §14 シード(vocab.json の "boil down to")は `alinea_api.seed` から実際には投入されない
 * (article.json/overview_dsl.json と同様の死んだ fixture。pw-13 冒頭コメント・followups 参照)。
 * VR-4d の詳細パネル撮影用に、同一の値で直接 INSERT する(seed_due_vocab.py と同方針)。
 */
function seedBoilDownTo(userEmail: string): { vocab_id: string; term: string } {
  const result = spawnSync(
    "uv",
    ["run", "--no-sync", "python", "apps/web/e2e/scripts/seed_vocab_boil_down_to.py", userEmail],
    { cwd: repoRoot, env: { ...process.env, NO_PROXY, no_proxy: NO_PROXY }, encoding: "utf-8" },
  );
  if (result.status !== 0) {
    throw new Error(`seed_vocab_boil_down_to.py failed: ${result.stderr || result.stdout}`);
  }
  return JSON.parse(result.stdout.trim()) as { vocab_id: string; term: string };
}

/**
 * VR-1e/1h/4b/4c/4d/5a(plans/12 §9.2・M2-17)。基準ビューポート 1440×900・
 * animations disabled・caret hide(config)。
 *
 * VR-1d/4a/4e と同方針(vr-m1.spec.ts): 他 spec・他実行の副作用で変動し得る一覧・件数領域は
 * §9.1 の「日時マスク」方針を拡張してマスクし、レイアウト回帰検出に的を絞る。
 * 各テストは他 spec ファイルの実行順に依存しないよう、必要な前提データを自己完結で作る。
 *
 * 注記(§9.1 と同様): フォントの決定的ラスタライズは Docker イメージ内実行が正。本基準は
 * ローカル Chromium で生成しており、CI(異なるフォントラスタライズ)では再生成が必要になり
 * 得る(followups)。
 */

async function dismissResumeBanner(page: Page): Promise<void> {
  const dismiss = page.getByRole("button", { name: "閉じる" });
  if (await dismiss.isVisible().catch(() => false)) await dismiss.click();
}

test.describe("VR M2 追加画面", () => {
  test("VR-1e ライブラリ(表): 検索ドロップダウン開・2行選択で一括バー", async ({ page }) => {
    await page.goto("/library?view=table");
    await page.getByRole("radio", { name: "テーブル", exact: true }).click();
    const rows = page.getByRole("row");
    await expect(rows.nth(2)).toBeVisible();
    await rows.nth(1).getByRole("checkbox").check();
    await rows.nth(2).getByRole("checkbox").check();
    await expect(page.getByRole("toolbar", { name: "一括操作" })).toBeVisible();

    await page.getByPlaceholder("ライブラリ全体を検索 — 本文・訳文・メモ・チャット").fill("flow");
    await page.waitForTimeout(500);
    await page.mouse.move(0, 0);
    await expect(page).toHaveScreenshot("vr-1e-library-table.png", {
      mask: [page.locator("main"), page.getByRole("toolbar", { name: "一括操作" })],
    });
  });

  test("VR-1h 記事モード: 概要図 版2・ブロックホバー", async ({ page }) => {
    const itemId = await resolveRfItemId(page);
    await page.goto(`/papers/${itemId}?mode=article`);
    await dismissResumeBanner(page);

    // `.isVisible()` は即時判定(リトライなし)のため、ページ遷移直後(記事の有無を問う
    // クエリがまだ loading 中)に呼ぶと CTA・本文のどちらも未描画で false になる競合状態が
    // あった(M2-17 で VR 実行時に発見。deviations 参照)。`waitFor` で状態確定を待つ。
    const generateCta = page.getByText("この論文の記事はまだありません");
    const needsGenerate = await generateCta
      .waitFor({ state: "visible", timeout: 10_000 })
      .then(() => true)
      .catch(() => false);
    if (needsGenerate) {
      const main = page.getByRole("main");
      await page.getByRole("button", { name: "✦ 記事を生成" }).click();
      // サイドパネル(チャット)の既存シード回答にも「AI生成」が出るため main に絞る。
      await expect(main.getByText("AI生成", { exact: true }).first()).toBeVisible({
        timeout: 30_000,
      });
      await page.getByRole("button", { name: "✦ 書き直し指示" }).first().click();
      await page.getByRole("textbox", { name: "書き直し指示" }).fill("VR 基準画像用の書き直し");
      await page.getByRole("button", { name: "✦ 書き直す" }).click();
      await expect(main.getByText("AI生成 · 版 2")).toBeVisible({ timeout: 20_000 });
    }

    const methodParagraph = page.getByText("確率フローを直線化する。");
    await methodParagraph.hover();
    await expect(page.getByRole("toolbar", { name: "ブロック操作" })).toBeVisible();
    await page.waitForTimeout(300);
    await expect(page).toHaveScreenshot("vr-1h-article-mode.png", {
      mask: [page.getByText(/^\d{4}-\d{2}-\d{2}$/)],
    });
  });

  test("VR-4b コレクション詳細: 締切・担当・共有リンク発行済み", async ({ page }) => {
    const itemId = await resolveRfItemId(page);
    const createRes = await page.request.post("/api/collections", {
      headers: { Origin: ORIGIN },
      data: { name: "VR-4b 輪読会" },
    });
    const collection = (await createRes.json()) as { id: string };
    try {
      await page.request.post(`/api/collections/${collection.id}/entries`, {
        headers: { Origin: ORIGIN },
        data: { library_item_id: itemId },
      });
      await page.request.patch(`/api/collections/${collection.id}`, {
        headers: { Origin: ORIGIN },
        data: { deadline: "2099-12-31" },
      });
      await page.request.post(`/api/collections/${collection.id}/share`, {
        headers: { Origin: ORIGIN },
      });

      await page.goto(`/collections/${collection.id}`);
      await expect(page.getByText("VR-4b 輪読会")).toBeVisible();
      await expect(page.getByText("発行済み")).toBeVisible();
      await page.waitForTimeout(300);
      await page.mouse.move(0, 0);
      // 共有トークンは実行ごとに変わり、締切残日数は撮影日に応じて変わるためマスクする
      // (§9.1 の日時マスク方針)。
      await expect(page).toHaveScreenshot("vr-4b-collection-detail.png", {
        mask: [page.getByText(/\/c\/[A-Za-z0-9]{8}/), page.getByText(/残り\s*\d+\s*日/)],
      });
    } finally {
      await page.request.delete(`/api/collections/${collection.id}`, { headers: { Origin: ORIGIN } }).catch(() => undefined);
    }
  });

  test("VR-4c 共有ページ(匿名コンテキスト)", async ({ browser }) => {
    const setupContext = await browser.newContext({
      viewport: { width: 1440, height: 900 },
      locale: "ja-JP",
      timezoneId: "Asia/Tokyo",
      storageState: "e2e/.auth/user.json",
    });
    const setupPage = await setupContext.newPage();
    const itemId = await resolveRfItemId(setupPage);
    const createRes = await setupPage.request.post("/api/collections", {
      headers: { Origin: ORIGIN },
      data: { name: "VR-4c 共有プレビュー" },
    });
    const collection = (await createRes.json()) as { id: string };
    let token = "";
    try {
      await setupPage.request.post(`/api/collections/${collection.id}/entries`, {
        headers: { Origin: ORIGIN },
        data: { library_item_id: itemId },
      });
      const shareRes = await setupPage.request.post(`/api/collections/${collection.id}/share`, {
        headers: { Origin: ORIGIN },
      });
      const share = (await shareRes.json()) as { token: string };
      token = share.token;
      await setupContext.close();

      const anonContext = await browser.newContext({
        viewport: { width: 1440, height: 900 },
        locale: "ja-JP",
        timezoneId: "Asia/Tokyo",
      });
      try {
        const anonPage = await anonContext.newPage();
        await anonPage.goto(`/c/${token}`);
        await expect(anonPage.getByText("Alineaをはじめる")).toBeVisible();
        await anonPage.waitForTimeout(300);
        // 「更新 YYYY-MM-DD」は撮影日に応じて変わるためマスクする(§9.1 の日時マスク方針)。
        await expect(anonPage).toHaveScreenshot("vr-4c-share-page.png", {
          mask: [anonPage.getByText(/更新\s*\d{4}-\d{2}-\d{2}/)],
        });
      } finally {
        await anonContext.close();
      }
    } finally {
      const cleanupContext = await browser.newContext({ storageState: "e2e/.auth/user.json" });
      const cleanupPage = await cleanupContext.newPage();
      await cleanupPage.request
        .delete(`/api/collections/${collection.id}`, { headers: { Origin: ORIGIN } })
        .catch(() => undefined);
      await cleanupContext.close();
      void token;
    }
  });

  test("VR-4d 語彙帳: 詳細パネル", async ({ page }) => {
    const seeded = seedBoilDownTo("dev@alinea.test");
    try {
      await page.goto("/vocab");
      // サイドバーの「語彙帳」リンクとも同名一致するため main 内に絞る。
      await expect(page.getByRole("main").getByText("語彙帳", { exact: true })).toBeVisible();
      await page.getByRole("option", { name: /^boil down to/ }).click();
      await expect(page.getByText("boil down to").first()).toBeVisible();
      await page.waitForTimeout(300);
      await page.mouse.move(0, 0);
      // 「次の復習」(相対日付)・総語数・復習期チップの件数は他 spec の副作用や撮影日に
      // 依存するためマスクする(§9.1 の日時マスク方針を件数領域にも拡張)。
      await expect(page).toHaveScreenshot("vr-4d-vocabulary.png", {
        mask: [page.getByText(/次の復習/), page.getByText(/読んだ論文の文脈から/), page.getByText(/^復習期/)],
      });
    } finally {
      await page.request
        .delete(`/api/vocab/${seeded.vocab_id}`, { headers: { Origin: ORIGIN } })
        .catch(() => undefined);
    }
  });

  test("VR-5a リソースタブ: 4種+公式提案カード", async ({ page }) => {
    const url = freshArxivUrl();
    const { library_item_id: itemId, job_id } = await ingestArxiv(page, url);
    // readable 到達を待つ(revision が無いと viewer 初期化 API が解決せずタブが出ない)。
    await waitForJob(page, job_id);
    await openViewer(page, itemId, "translation");
    await dismissResumeBanner(page);
    await page.getByRole("tab", { name: "リソース" }).click();

    const suffix = Date.now();
    const urls = [
      `https://github.com/e2e-vr-org/repo-${suffix}`,
      `https://example.com/vr/blog-${suffix}`,
      `https://example.com/vr/slides-${suffix}.pdf`,
    ];
    const urlInput = page.getByRole("textbox", { name: "リソースの URL" });
    for (const u of urls) {
      await urlInput.fill(u);
      await page.getByRole("button", { name: "追加", exact: true }).click();
      await expect(urlInput).toHaveValue("");
    }
    await expect(page.locator("[data-resource-id]")).toHaveCount(urls.length, { timeout: 15_000 });
    await page.waitForTimeout(500);
    await page.mouse.move(0, 0);
    await expect(page).toHaveScreenshot("vr-5a-resources-tab.png", {
      mask: [page.locator("[data-resource-id]")],
    });

    await page.request.delete(`/api/library-items/${itemId}`, { headers: { Origin: ORIGIN } }).catch(() => undefined);
  });
});
