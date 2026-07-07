import { expect, test, type Page } from "@playwright/test";

/**
 * PW-02(plans/12 §4.3・AC-00-03): 取り込み UI 非存在の否定検査。
 * ライブラリ(表/カード)・ヘッダの全域に「+追加」「取り込み」「アップロード」「ドロップ」に
 * 該当する操作要素・input[type=file] が存在しない(docs/00・02・08 — 取り込み経路は拡張のみ)。
 * M0 に /dashboard は無いため、対象はライブラリ 2 ビュー + 共通ヘッダ。
 */
// 取り込み「操作」の語彙のみに限定する(列見出し「追加日」・進捗表示「取り込み中」等の
// 非操作テキストを誤検知しないよう、追加系は完全一致/「+追加」「論文を追加」に絞る)。
const INGEST_ACTION_RE =
  /(アップロード|ドロップ|ファイルを選択|取り込む|インポート|^\+?\s*追加$|論文を追加)/;

async function assertNoIngestAffordances(page: Page): Promise<void> {
  // input[type=file] は全域で 0 件。
  await expect(page.locator('input[type="file"]')).toHaveCount(0);
  // 取り込み系のボタン/リンクが 0 件。
  await expect(page.getByRole("button", { name: INGEST_ACTION_RE })).toHaveCount(0);
  await expect(page.getByRole("link", { name: INGEST_ACTION_RE })).toHaveCount(0);
}

test.describe("PW-02 取り込み UI 非存在(拡張のみが取り込み経路)", () => {
  test("ライブラリ(表)とヘッダに取り込み操作が無い", async ({ page }) => {
    await page.goto("/library");
    await expect(page.getByRole("heading", { name: "ライブラリ" })).toBeVisible();
    await page.getByRole("radio", { name: "テーブル", exact: true }).click();
    await assertNoIngestAffordances(page);
  });

  test("ライブラリ(カード)に取り込み操作が無い", async ({ page }) => {
    await page.goto("/library");
    await expect(page.getByRole("heading", { name: "ライブラリ" })).toBeVisible();
    await page.getByRole("radio", { name: "カード", exact: true }).click();
    await assertNoIngestAffordances(page);
  });
});
