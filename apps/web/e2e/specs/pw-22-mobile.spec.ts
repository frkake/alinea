/* eslint-disable no-restricted-imports -- E2E 補助モジュールは親ディレクトリから import する(src の @/ エイリアス規約は外) */
import { expect, test } from "@playwright/test";
import { ORIGIN, resolveRfItemId } from "../fixtures/api";

/**
 * PW-22(plans/12 §4.3): モバイル(viewport 390×844)。
 * ビューア閲覧・ステータス変更ができ、取り込み操作が要求されないことを検証する
 * (M1-26 のモバイル縮退レイアウトが前提。mobile.md §4.2 のヘッダ 5 要素・§5.1 のナビ
 * ドロワー化)。
 *
 * 否定検査は pw-02-no-ingest-ui.spec.ts と同じ語彙・スコープ(button/link ロール限定)を使う。
 * `getByText` の素朴な正規表現は、ダッシュボードの空状態コピー(RecentlyAdded.tsx の
 * 「取り込みはブラウザ拡張から行えます」等、取り込み非搭載を説明する記述文)を誤検知する。
 */
const INGEST_ACTION_RE =
  /(アップロード|ドロップ|ファイルを選択|取り込む|インポート|^\+?\s*追加$|論文を追加)/;

test.describe("PW-22 モバイル(390×844)", () => {
  test.use({ viewport: { width: 390, height: 844 } });

  test("ダッシュボード・ビューア閲覧・ステータス変更・取り込みUI非存在", async ({ page }) => {
    await page.goto("/dashboard");
    // モバイル縮退: ハンバーガーからのドロワーナビ(常時表示のサイドバーは非描画)。
    const menuButton = page.getByRole("button", { name: "メニューを開く" });
    await expect(menuButton).toBeVisible();
    await menuButton.click();
    const navDrawer = page.getByRole("dialog", { name: "ナビゲーション" });
    await expect(navDrawer).toBeVisible();
    await expect(navDrawer.getByRole("link", { name: "ライブラリ" })).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(navDrawer).toBeHidden();

    // 取り込みUI非存在(PW-02 と同じ否定検査。モバイルでも要求されない。docs/00)。
    await expect(page.getByRole("button", { name: INGEST_ACTION_RE })).toHaveCount(0);
    await expect(page.getByRole("link", { name: INGEST_ACTION_RE })).toHaveCount(0);
    await expect(page.locator('input[type="file"]')).toHaveCount(0);

    // ビューア閲覧: モバイル縮退ヘッダ(戻る/目次を開く/タイトル/ステータスピル)。
    const itemId = await resolveRfItemId(page);
    await page.goto(`/papers/${itemId}`);
    await expect(page.getByRole("button", { name: "戻る" })).toBeVisible();
    await expect(page.getByRole("button", { name: "目次を開く" })).toBeVisible();
    // モード切替タブは非描画(mobile.md §4.2)。常に訳文表示に強制。
    await expect(page.getByRole("radiogroup", { name: "表示モード" })).toHaveCount(0);
    await expect(page.locator(".alinea-paragraph[data-block-id]").first()).toBeVisible();

    // 取り込みUI非存在(ビューア側)。
    await expect(page.getByRole("button", { name: INGEST_ACTION_RE })).toHaveCount(0);
    await expect(page.getByRole("link", { name: INGEST_ACTION_RE })).toHaveCount(0);

    // ステータス変更(StatusPill はモバイルでも interactive のまま)。
    const statusPill = page.getByRole("button", { name: /読む予定|すぐ読む|読んでいる|保留|あとで再読/ });
    await statusPill.click();
    await page.getByRole("menuitemradio", { name: "あとで再読" }).click();
    await expect(page.getByRole("button", { name: /あとで再読/ })).toBeVisible();

    await expect
      .poll(async () => {
        const r = await page.request.get(`/api/library-items/${itemId}`);
        const body = (await r.json()) as { status: string };
        return body.status;
      })
      .toBe("reread");

    // 元のステータスへ戻す(自分が変えたデータの後片付け)。
    await page.request.patch(`/api/library-items/${itemId}`, {
      headers: { "Content-Type": "application/json", Origin: ORIGIN },
      data: { status: "reading" },
    });
  });
});
