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

  test("スタイル切替(自然訳→直訳。初回=オンデマンド生成開始、再切替=即時)がヘッダに反映される", async ({
    page,
  }) => {
    // 残りセクション分のオンデマンド生成を待つため、テスト全体の予算を広げる(既定 60s)。
    test.setTimeout(90_000);
    const styleButton = page.getByRole("button", { name: /スタイル: (自然訳|直訳)/ });
    await expect(styleButton).toContainText("自然訳");
    await styleButton.click();
    await page.getByRole("menuitem", { name: "直訳" }).click();
    // 初回: §14 シードの literal セットは §1 のみ(オンデマンド途中状態)のため、
    // クライアント側は常に生成要求から入る(「(生成中…)」を経て「直訳」に収束する)。
    await expect(styleButton).toContainText("直訳");
    // 未翻訳の残りセクション分をオンデマンドで生成する(ブロック数によっては数十秒かかり得る)。
    await expect(styleButton).not.toContainText("生成中", { timeout: 60_000 });

    // 再切替: 自然訳→直訳の 2 回目は同一セッション内で literalStatus="ready" のため即時
    // (生成中インジケータが再度出ない)。
    await styleButton.click();
    await page.getByRole("menuitem", { name: "自然訳" }).click();
    await expect(styleButton).toContainText("自然訳");
    await styleButton.click();
    await page.getByRole("menuitem", { name: "直訳" }).click();
    await expect(styleButton).toContainText("直訳");
    await expect(styleButton).not.toContainText("生成中");
  });

  test("段落ホバーで「対」→ 対訳ポップが開く", async ({ page }) => {
    // 前回位置バナーが本文上部を覆う場合は閉じる。
    const dismiss = page.getByRole("button", { name: "閉じる" });
    if (await dismiss.isVisible().catch(() => false)) await dismiss.click();

    const para = page.locator(".alinea-paragraph[data-block-id]").first();
    await expect(para).toBeVisible();
    await para.scrollIntoViewIfNeeded();
    await para.hover();
    const toggle = para.getByRole("button", { name: "対訳を表示" });
    await expect(toggle).toBeVisible();
    await toggle.click();
    // 対訳ポップのフッタ(1b §5.6 逐語)。
    await expect(page.getByText("訳がおかしい?")).toBeVisible();
  });

  test("再翻訳ボタンで proposal カードが表示され採用できる(Task 6)", async ({
    page,
  }) => {
    // 前回位置バナーが本文上部を覆う場合は閉じる。
    const dismiss = page.getByRole("button", { name: "閉じる" });
    if (await dismiss.isVisible().catch(() => false)) await dismiss.click();

    // .first() は Abstract ¶1(seed が state="edited" に固定)。指示なし再翻訳は
    // discard_edit を送らないため 409 edit_protected になり proposal が出ない。
    // state="machine" の Introduction ¶1(nth(1))を対象にする。
    const para = page.locator(".alinea-paragraph[data-block-id]").nth(1);
    await expect(para).toBeVisible();
    await para.scrollIntoViewIfNeeded();
    await para.hover();
    const toggle = para.getByRole("button", { name: "対訳を表示" });
    await expect(toggle).toBeVisible();
    await toggle.click();

    // 対訳ポップの再翻訳ボタンをクリック。
    const popover = page.getByRole("dialog", { name: "対訳" });
    await expect(popover).toBeVisible();
    const retranslateBtn = popover.getByRole("button", { name: /再翻訳/ });
    await expect(retranslateBtn).toBeEnabled();
    await retranslateBtn.click();

    // 再翻訳中はボタンが disabled になる(または再翻訳中インジケータが表示される)。
    // 完了後に proposal カードが表示される(最大 30 秒待機)。
    const proposalCard = page.getByTestId("retranslation-proposal");
    await expect(proposalCard).toBeVisible({ timeout: 30_000 });

    // 採用ボタンをクリックして proposal を確定。
    await proposalCard.getByRole("button", { name: "採用" }).click();

    // proposal カードが消える(採用完了)。
    await expect(proposalCard).not.toBeVisible({ timeout: 10_000 });
  });

  // 未翻訳付録を開く→オンデマンド翻訳開始は seed の付録セクション状態に依存(PY-TR-08 が担保)。
  test.fixme("未翻訳付録のオンデマンド翻訳(PY-TR-08 が担保)", async () => {});
});
