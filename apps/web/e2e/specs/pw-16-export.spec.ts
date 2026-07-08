import { expect, test } from "@playwright/test";

/**
 * PW-16(plans/12 §4.3・M2-17): エクスポート。
 * 設定画面のエクスポートカテゴリから Markdown / BibTeX / CSV をダウンロードし、
 * JSON 一括(ジョブ完了→ download_url →自動ダウンロード)を検証する。
 */
test.describe("PW-16 エクスポート", () => {
  test("Markdown/BibTeX/CSV ダウンロード + JSON 一括", async ({ page }) => {
    await page.goto("/settings");
    await page.getByRole("navigation", { name: "設定カテゴリ" }).getByRole("button", { name: "エクスポート" }).click();
    await expect(page.getByText("論文単位 Markdown")).toBeVisible();

    // 1) 論文単位 Markdown: ピッカーで論文を選び、ダウンロードが発生する。
    const [mdDownload] = await Promise.all([
      page.waitForEvent("download"),
      (async () => {
        await page.getByRole("button", { name: "論文単位 Markdown をエクスポート" }).click();
        const modal = page.getByRole("dialog", { name: "エクスポートする論文を選択" });
        await expect(modal).toBeVisible();
        await modal.getByRole("textbox", { name: "タイトル・著者で検索" }).fill("Rectified");
        await modal.getByText("Flow Straight and Fast", { exact: false }).first().click();
      })(),
    ]);
    expect(mdDownload.suggestedFilename()).toMatch(/\.md$/);

    // 2) BibTeX。
    const [bibDownload] = await Promise.all([
      page.waitForEvent("download"),
      (async () => {
        await page.getByRole("button", { name: "BibTeX / CSV をエクスポート" }).click();
        await page.getByRole("menuitem", { name: "BibTeX (.bib)" }).click();
      })(),
    ]);
    expect(bibDownload.suggestedFilename()).toMatch(/\.bib$/);

    // 3) CSV(書誌一括。UTF-8 BOM・16 列は PY-EXP-03 が担保)。
    const [csvDownload] = await Promise.all([
      page.waitForEvent("download"),
      (async () => {
        await page.getByRole("button", { name: "BibTeX / CSV をエクスポート" }).click();
        await page.getByRole("menuitem", { name: "CSV (.csv)" }).click();
      })(),
    ]);
    expect(csvDownload.suggestedFilename()).toMatch(/\.csv$/);

    // 4) JSON 一括(export_full ジョブ→ download_url →自動ダウンロード)。
    const [jsonDownload] = await Promise.all([
      page.waitForEvent("download", { timeout: 30_000 }),
      page.getByRole("button", { name: "JSON 一括 をエクスポート" }).click(),
    ]);
    expect(jsonDownload.suggestedFilename()).toMatch(/\.zip$/);
  });
});
