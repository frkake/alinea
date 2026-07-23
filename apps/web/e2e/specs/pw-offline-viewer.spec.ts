/* eslint-disable no-restricted-imports -- E2E 補助モジュールは親ディレクトリから import する(src の @/ エイリアス規約は適用外) */
import { expect, test } from "@playwright/test";
import { resolveRfItemId } from "../fixtures/api";
import { openViewer } from "../fixtures/viewer";

/**
 * PW-OFFLINE(Task 23 / spec 2026-07-16-pwa-offline-design §B v2): 直近論文のオフライン閲覧。
 *
 * 検証項目(brief Step 5):
 *   1) 一度オンラインで開いた論文は、context を offline にして同 URL を再読込しても
 *      本文と訳文が表示される(SW の network-first + cache フォールバック)。
 *   2) 未訪問の論文をオフラインで開くと offline シェル(再接続案内)になる。
 *   3) オンラインでセッションが 401 のときは login へ進む(=SW は 401 を cache で
 *      成功応答に置換しない。plan §4 の最重要不変条件)。
 *
 * ─────────────────────────────────────────────────────────────────────────────
 * 実行は Task 32 へ延期(DEFERRED)。
 *   本 spec は「フルスタック(web + api + worker + seed)が起動し、かつ Service Worker が
 *   実際に登録される本番相当ビルド」を要求する。加えて Playwright の BrowserContext を
 *   offline に切り替える必要があり、既存 E2E ハーネス(dev サーバ・SW 未登録)では成立しない。
 *   Task 32(全機能の E2E 統合)で本番相当ビルド+SW 登録+offline トグルを整えて実行する。
 *   それまでは test.describe.fixme でスキップし、実行対象から外す(コードレビュー用に spec は
 *   完成させておく)。
 * ─────────────────────────────────────────────────────────────────────────────
 */
test.describe.fixme(
  "PW-OFFLINE 直近論文のオフライン閲覧(実行は Task 32 へ延期 / SW 登録+offline トグルが必要)",
  () => {
    test("一度開いた論文はオフライン再読込でも本文・訳文が表示される", async ({ page, context }) => {
      const itemId = await resolveRfItemId(page);

      // 1) オンラインで開く → SW が viewer データ+訳文+図を cache する(CACHE_PAPER)。
      await openViewer(page, itemId, "translation");
      await expect(page.getByRole("radiogroup", { name: "表示モード" })).toBeVisible();
      // SW の warm cache / CACHE_PAPER 完了を待つ(controller 就任後の postMessage 反映)。
      await page.waitForTimeout(1000);

      // 2) offline に切り替えて同一 URL を再読込。
      await context.setOffline(true);
      await page.reload();

      // 本文と訳文が cache から描画される(login にも offline シェルにも飛ばない)。
      await expect(page).toHaveURL(new RegExp(`/papers/${itemId}`));
      await expect(page.getByRole("radiogroup", { name: "表示モード" })).toBeVisible();
      await expect(page.getByText("オフラインです")).toHaveCount(0);

      await context.setOffline(false);
    });

    test("未訪問の論文をオフラインで開くと offline シェル(再接続案内)になる", async ({
      page,
      context,
    }) => {
      // 一度もオンラインで開いていない itemId。
      const unseen = "li_never_visited_offline";
      await context.setOffline(true);
      await page.goto(`/papers/${unseen}`);

      // ナビゲーション失敗 → SW が /offline シェルを返す(login リダイレクトではない)。
      await expect(page.getByText("オフラインです")).toBeVisible();
      await expect(page.getByText("保存済みの論文")).toBeVisible();

      await context.setOffline(false);
    });

    test("オンラインで 401 のときは login へ進む(SW は 401 を cache で置換しない)", async ({
      page,
      context,
    }) => {
      const itemId = await resolveRfItemId(page);
      await openViewer(page, itemId, "translation");
      await page.waitForTimeout(1000);

      // セッション Cookie を破棄 → オンラインのまま再読込すると viewer API は 401 を返す。
      await context.clearCookies();
      await page.reload();

      // cache に成功応答があっても 401 が素通しし、通常の login リダイレクトが働く。
      await expect(page).toHaveURL(/\/login/);
    });
  },
);
