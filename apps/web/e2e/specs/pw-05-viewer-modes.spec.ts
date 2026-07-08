/* eslint-disable no-restricted-imports -- E2E 補助モジュールは親ディレクトリから import する(src の @/ エイリアス規約は適用外) */
import { expect, test } from "@playwright/test";
import { resolveRfItemId } from "../fixtures/api";
import { openViewer, switchMode } from "../fixtures/viewer";

/**
 * PW-05(plans/12 §4.3・完全化は M2-17): ビューア基本。
 * 表示 5 モード(訳文/対訳/原文/PDF/記事)のワンクリック切替、品質バッジ常時、
 * サイドパネル 6 タブ(チャット/メモ/注釈/図表/リソース/情報)の排他選択を検証する
 * (M2-13 でリソースタブが追加された。plans/13 §4.2)。
 *
 * PDF モードは §14 シード(quality A・arxiv_html 由来)に PDF の SourceAsset が無いため
 * ラジオが disabled になる(PW-12 の決定と同じ理由)。実際の PDF モード切替・表示は
 * quality B アイテムで PW-12 が検証するため、本 spec では「disabled のまま」を確認する。
 * 図表参照ポップはコンテンツ依存のため test.fixme(下記)。
 */
test.describe("PW-05 ビューア基本(3 モード・タブ排他)", () => {
  let itemId: string;
  test.beforeEach(async ({ page }) => {
    itemId = await resolveRfItemId(page);
    await openViewer(page, itemId, "translation");
  });

  test("表示 5 モードをワンクリック切替できる(PDF は quality A で disabled)", async ({ page }) => {
    await switchMode(page, "対訳");
    await expect(page).toHaveURL(/mode=parallel/);
    await switchMode(page, "原文");
    await expect(page).toHaveURL(/mode=source/);
    await switchMode(page, "記事");
    await expect(page).toHaveURL(/mode=article/);
    await expect(page.getByRole("radio", { name: "PDF", exact: true })).toBeDisabled();
    await switchMode(page, "訳文");
    await expect(page).toHaveURL(/mode=translation/);
  });

  test("品質バッジ(A)が常時表示される", async ({ page }) => {
    await expect(page.getByTitle(/品質レベルA/)).toBeVisible();
  });

  test("サイドパネルは 6 タブ(チャット/メモ/注釈/図表/リソース/情報)で排他選択される", async ({
    page,
  }) => {
    const tablist = page.getByRole("tablist");
    await expect(tablist).toBeVisible();
    const chat = page.getByRole("tab", { name: "チャット" });
    const notes = page.getByRole("tab", { name: "メモ" });
    const annotations = page.getByRole("tab", { name: "注釈" });
    const figures = page.getByRole("tab", { name: "図表" });
    const resources = page.getByRole("tab", { name: "リソース" });
    const info = page.getByRole("tab", { name: "情報" });
    await expect(chat).toBeVisible();
    await expect(notes).toBeVisible();
    await expect(annotations).toBeVisible();
    await expect(figures).toBeVisible();
    await expect(resources).toBeVisible();
    await expect(info).toBeVisible();

    await resources.click();
    await expect(resources).toHaveAttribute("aria-selected", "true");
    await expect(chat).toHaveAttribute("aria-selected", "false");

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

  // 「図2」「式(5)」クリックでその場ポップ(スクロール位置不変)は VT-VIEW-06 が担保。
  // E2E ではシード本文中の参照インラインの存在に依存するため fixme。
  test.fixme("図表参照クリックでその場ポップ(コンテンツ依存 / VT-VIEW-06)", async () => {});
});
