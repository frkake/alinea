/* eslint-disable no-restricted-imports -- E2E 補助モジュールは親ディレクトリから import する(src の @/ エイリアス規約は適用外) */
import { expect, test } from "@playwright/test";
import { resolveRfItemId } from "../fixtures/api";
import { openViewer, switchMode } from "../fixtures/viewer";

/**
 * PW-05(plans/12 §4.3・M0 スコープ+M1 タブ追補): ビューア基本。
 * M0 は表示 3 モード(訳文 / 対訳 / 原文)のワンクリック切替(plans/13 §1.5)。品質バッジ常時、
 * サイドパネルは M1(M1-02/M1-04)で メモ・注釈 タブが追加され 5 タブ(チャット / メモ / 注釈 /
 * 図表 / 情報)の排他選択になった(リソースは M2-13 まで非表示)。PDF / 記事モードの切替は M2
 * スコープ、図表参照ポップはコンテンツ依存のため test.fixme(下記)。
 */
test.describe("PW-05 ビューア基本(3 モード・タブ排他)", () => {
  let itemId: string;
  test.beforeEach(async ({ page }) => {
    itemId = await resolveRfItemId(page);
    await openViewer(page, itemId, "translation");
  });

  test("表示 3 モードをワンクリック切替できる", async ({ page }) => {
    await switchMode(page, "対訳");
    await expect(page).toHaveURL(/mode=parallel/);
    await switchMode(page, "原文");
    await expect(page).toHaveURL(/mode=source/);
    await switchMode(page, "訳文");
    await expect(page).toHaveURL(/mode=translation/);
  });

  test("品質バッジ(A)が常時表示される", async ({ page }) => {
    await expect(page.getByTitle(/品質レベルA/)).toBeVisible();
  });

  test("サイドパネルは 5 タブ(チャット/メモ/注釈/図表/情報)で排他選択される", async ({
    page,
  }) => {
    const tablist = page.getByRole("tablist");
    await expect(tablist).toBeVisible();
    const chat = page.getByRole("tab", { name: "チャット" });
    const notes = page.getByRole("tab", { name: "メモ" });
    const annotations = page.getByRole("tab", { name: "注釈" });
    const figures = page.getByRole("tab", { name: "図表" });
    const info = page.getByRole("tab", { name: "情報" });
    await expect(chat).toBeVisible();
    await expect(notes).toBeVisible();
    await expect(annotations).toBeVisible();
    await expect(figures).toBeVisible();
    await expect(info).toBeVisible();
    // リソースタブは M2-13 まで非表示。
    await expect(page.getByRole("tab", { name: "リソース" })).toHaveCount(0);

    await info.click();
    await expect(info).toHaveAttribute("aria-selected", "true");
    await expect(chat).toHaveAttribute("aria-selected", "false");
    await figures.click();
    await expect(figures).toHaveAttribute("aria-selected", "true");
    await expect(info).toHaveAttribute("aria-selected", "false");
    await annotations.click();
    await expect(annotations).toHaveAttribute("aria-selected", "true");
    await expect(figures).toHaveAttribute("aria-selected", "false");
    await notes.click();
    await expect(notes).toHaveAttribute("aria-selected", "true");
    await expect(annotations).toHaveAttribute("aria-selected", "false");
  });

  // PDF / 記事モードは M0 の 3 モード集合外(plans/13 §1.5)。M2/概要図レーンで有効化。
  test.fixme("PDF モード・記事モードの切替(M2 スコープ)", async () => {});
  // 「図2」「式(5)」クリックでその場ポップ(スクロール位置不変)は VT-VIEW-06 が担保。
  // E2E ではシード本文中の参照インラインの存在に依存するため fixme。
  test.fixme("図表参照クリックでその場ポップ(コンテンツ依存 / VT-VIEW-06)", async () => {});
});
