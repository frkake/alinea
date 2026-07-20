/* eslint-disable no-restricted-imports -- E2E 補助モジュールは親ディレクトリから import する(src の @/ エイリアス規約は外) */
import { expect, test } from "@playwright/test";
import { freshArxivUrl, ingestArxiv, waitForJob } from "../fixtures/api";
import { openViewer } from "../fixtures/viewer";

/**
 * PW-18(plans/12 §4.3・plans/09-screens/1g): 読了フロー。
 * 最終セクション到達→読了提案(status→done の実操作起点はステータスピル。§14 RF アイテムは
 * すでに status=reading のため、都度取り込んだ新規アイテムで検証する)→モーダル(読了日・
 * 累計時間の自動記録表示・理解度・重要度・ひとことメモ・「すべてスキップ」経路)を検証する。
 *
 * 「記事モードで読み返す →」カードは M2-07 で表示化済み(FinishReadingDialog の docstring)。
 * Task 32 で実操作化する: モーダル内のカードをクリックすると記事モード(?mode=article)へ
 * 遷移することを検証する。カードは常時表示(要約有無に依らない)。
 */
test.describe("PW-18 読了フロー", () => {
  test("ステータス→「読んだ」でモーダル起動→理解度/重要度/メモ→保存", async ({ page }) => {
    const url = freshArxivUrl();
    const { job_id, library_item_id } = await ingestArxiv(page, url);
    const final = await waitForJob(page, job_id);
    expect(final.status).toBe("succeeded");

    await openViewer(page, library_item_id, "translation");

    const statusPill = page.getByRole("button", { name: /読む予定|すぐ読む|読んでいる/ });
    await statusPill.click();
    await page.getByRole("menuitemradio", { name: "読んだ" }).click();

    const dialog = page.getByRole("dialog", { name: "「読んだ」にしました" });
    await expect(dialog).toBeVisible();
    await expect(dialog.getByText(/読了日 .+(自動記録)/)).toBeVisible();

    await dialog.getByRole("radio", { name: /4\/5 —/ }).click();
    await dialog.getByRole("radio", { name: "高", exact: true }).click();
    await dialog.getByRole("textbox", { name: "ひとことメモ" }).fill("PDF系の実装参考に良い");
    await dialog.getByRole("button", { name: "保存", exact: true }).click();
    await expect(dialog).toBeHidden();

    await expect(page.getByRole("button", { name: /読んだ\s*▾/ })).toBeVisible();
  });

  test("すべてスキップ経路", async ({ page }) => {
    const url = freshArxivUrl();
    const { job_id, library_item_id } = await ingestArxiv(page, url);
    const final = await waitForJob(page, job_id);
    expect(final.status).toBe("succeeded");

    await openViewer(page, library_item_id, "translation");
    await page.getByRole("button", { name: /読む予定|すぐ読む|読んでいる/ }).click();
    await page.getByRole("menuitemradio", { name: "読んだ" }).click();

    const dialog = page.getByRole("dialog", { name: "「読んだ」にしました" });
    await expect(dialog).toBeVisible();
    await dialog.getByRole("button", { name: "すべてスキップ" }).click();
    await expect(dialog).toBeHidden();

    // スキップしてもステータス自体の変更(→読んだ)は既に反映されている(finished_at 記録は
    // PATCH 成功時点。§3.3 の理解度等は任意項目)。
    await expect(page.getByRole("button", { name: /読んだ\s*▾/ })).toBeVisible();
  });

  test("「記事モードで読み返す →」カードで記事モードへ遷移する", async ({ page }) => {
    const url = freshArxivUrl();
    const { job_id, library_item_id } = await ingestArxiv(page, url);
    const final = await waitForJob(page, job_id);
    expect(final.status).toBe("succeeded");

    await openViewer(page, library_item_id, "translation");
    await page.getByRole("button", { name: /読む予定|すぐ読む|読んでいる/ }).click();
    await page.getByRole("menuitemradio", { name: "読んだ" }).click();

    const dialog = page.getByRole("dialog", { name: "「読んだ」にしました" });
    await expect(dialog).toBeVisible();

    // 記事モードで読み返すカード(常時表示)。クリックで ?mode=article へ push 遷移する。
    const rereadCard = dialog.getByRole("button", { name: /記事モードで読み返す/ });
    await expect(rereadCard).toBeVisible();
    await rereadCard.click();

    await expect(dialog).toBeHidden();
    await expect(page).toHaveURL(new RegExp(`/papers/${library_item_id}\\?mode=article`));
  });
});
