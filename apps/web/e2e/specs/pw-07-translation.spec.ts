/* eslint-disable no-restricted-imports -- E2E 補助モジュールは親ディレクトリから import する(src の @/ エイリアス規約は適用外) */
import { expect, test } from "@playwright/test";
import { resolveRfItemId } from "../fixtures/api";
import { openViewer } from "../fixtures/viewer";

/**
 * PW-07(plans/12 §4.3・M0 スコープ): 翻訳操作。
 * M0 で確実に検証できるのは (a) スタイル切替(自然訳 ⇄ 直訳)と (b) 段落ホバー「対」→ 対訳ポップ。
 * 指示つき再翻訳(proposal 差分→採用)・未翻訳付録のオンデマンド翻訳は M1/後続レーンのため fixme。
 */
test.describe("PW-07 翻訳操作(M0)", () => {
  let itemId: string;
  test.beforeEach(async ({ page }) => {
    itemId = await resolveRfItemId(page);
    await openViewer(page, itemId, "translation");
  });

  test("スタイル切替(自然訳 → 直訳)がヘッダに反映される", async ({ page }) => {
    const styleButton = page.getByRole("button", { name: /スタイル: (自然訳|直訳)/ });
    await expect(styleButton).toContainText("自然訳");
    await styleButton.click();
    await page.getByRole("menuitem", { name: "直訳" }).click();
    await expect(styleButton).toContainText("直訳");
  });

  test("段落ホバーで「対」→ 対訳ポップが開く", async ({ page }) => {
    // 前回位置バナーが本文上部を覆う場合は閉じる。
    const dismiss = page.getByRole("button", { name: "閉じる" });
    if (await dismiss.isVisible().catch(() => false)) await dismiss.click();

    const para = page.locator(".yk-paragraph[data-block-id]").first();
    await expect(para).toBeVisible();
    await para.scrollIntoViewIfNeeded();
    await para.hover();
    const toggle = para.getByRole("button", { name: "対訳を表示" });
    await expect(toggle).toBeVisible();
    await toggle.click();
    // 対訳ポップのフッタ(1b §5.6 逐語)。
    await expect(page.getByText("訳がおかしい?")).toBeVisible();
  });

  // 指示つき再翻訳(proposal 差分→採用)・スタイル初回=生成開始は M1(retranslate レーン)。
  test.fixme("指示つき再翻訳の proposal→採用(M1)", async () => {});
  // 未翻訳付録を開く→オンデマンド翻訳開始は seed の付録セクション状態に依存(PY-TR-08 が担保)。
  test.fixme("未翻訳付録のオンデマンド翻訳(PY-TR-08 が担保)", async () => {});
});
