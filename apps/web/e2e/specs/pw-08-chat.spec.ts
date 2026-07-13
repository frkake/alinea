/* eslint-disable no-restricted-imports -- E2E 補助モジュールは親ディレクトリから import する(src の @/ エイリアス規約は適用外) */
import { expect, test } from "@playwright/test";
import { CHAT_DISCLAIMER_SNIPPET } from "../fixtures/constants";
import { resolveRfItemId } from "../fixtures/api";
import { dragSelect, openViewer } from "../fixtures/viewer";

/**
 * PW-08(plans/12 §4.3・M0 スコープ+M1-24 追補): チャット。
 * 本 spec は M0 で確実に配線された経路(本文選択 → 選択メニュー「✦ AIに質問」→ チャットタブ)に
 * 加え、M1-24 で「↑ メモに保存」経路の fixme を解除する: §14 シード(chat.json)には既に
 * 根拠アンカー付きの assistant 回答が投入済みのため、これを使って 根拠チップ→本文ジャンプ→
 * 双方向ハイライト→メモに保存 を実 API 往復で検証できる(ライブ SSE 生成は不要)。
 *
 * fixme(理由を明記):
 * - 新規質問への SSE 回答での根拠チップ生成: モック LLM サーバ(§8.4)がプロンプト中の実在
 *   block_id を引用する `[[evidence:blk-XXX]]` を返す必要があるが現状はエコー応答で根拠を
 *   出さない。加えてチャット既定モデルの anthropic/openai ストリーミングアダプタが同梱 SDK と
 *   非互換(`output_config` / Responses API)。これらは packages/llm(別レーン)の課題。
 *   UI 単体は VT-VIEW-09/10(EvidenceChip/ChatMarkdown)が担保。followups 参照。
 */
test.describe("PW-08 チャット(選択→AIに質問→チャットタブ)", () => {
  test("本文選択→「✦ AIに質問」でチャットタブが開き、入力欄と免責文が出る", async ({ page }) => {
    const itemId = await resolveRfItemId(page);
    await openViewer(page, itemId, "translation");

    // 既定はチャットタブ。まず情報タブへ切り替え、AIに質問でチャットへ戻ることを確かめる。
    const chatTab = page.getByRole("tab", { name: "チャット" });
    await page.getByRole("tab", { name: "情報" }).click();
    await expect(chatTab).toHaveAttribute("aria-selected", "false");

    // 前回位置バナーが本文上部を覆う場合は閉じる。
    const dismiss = page.getByRole("button", { name: "閉じる" });
    if (await dismiss.isVisible().catch(() => false)) await dismiss.click();

    // 本文段落をドラッグ選択 → 選択メニュー。
    const para = page.locator(".alinea-paragraph[data-block-id]").first();
    await expect(para).toBeVisible();
    await para.scrollIntoViewIfNeeded();
    await dragSelect(page, para);

    const menu = page.getByRole("menu", { name: "選択メニュー" });
    await expect(menu).toBeVisible();
    await menu.getByRole("menuitem", { name: /AIに質問/ }).click();

    // チャットタブへ切り替わり、入力欄と免責文(逐語)が見える。
    await expect(chatTab).toHaveAttribute("aria-selected", "true");
    await expect(page.getByRole("textbox", { name: "この論文について質問" })).toBeVisible();
    await expect(page.getByText(CHAT_DISCLAIMER_SNIPPET)).toBeVisible();
  });

  test.fixme(
    "新規質問への SSE 回答での根拠チップ生成(モック LLM 根拠出力/アダプタ互換が前提)",
    async () => {},
  );

  test("既存回答: 根拠チップ→本文ジャンプ→双方向ハイライト→メモに保存", async ({ page }) => {
    const itemId = await resolveRfItemId(page);
    // 対訳(parallel)モード: 本文側「✦ チャットの根拠」バッジは BilingualPane の数式ブロックが
    // 実装対象(plans/09 1a §4.4)。§14 シード(chat.json)の 2 番目の assistant 回答が
    // blk-2-1-eq2-2dfc(式(2))を根拠に持つ。
    await openViewer(page, itemId, "parallel");

    const chatTab = page.getByRole("tab", { name: "チャット" });
    await expect(chatTab).toHaveAttribute("aria-selected", "true");

    const assistantMsg = page
      .locator("[data-message-id]")
      .filter({ hasText: "アシスタント" })
      .filter({ hasText: "整流フローの学習目的" });
    await expect(assistantMsg).toBeVisible();

    // 根拠チップ(display は block_search_index から決定的に導出。式(2) = blk-2-1-eq2-2dfc)。
    const chip = assistantMsg.getByRole("button", { name: "式(2)" });
    await expect(chip).toBeVisible();
    await chip.click();

    // 本文ジャンプ+双方向ハイライト: 該当数式ブロックに「✦ チャットの根拠 · 式(2)」バッジ。
    const targetBlock = page.locator('[data-block-id="blk-2-1-eq2-2dfc"]');
    await expect(targetBlock).toBeVisible();
    await expect(targetBlock.getByText("✦ チャットの根拠 · 式(2)")).toBeVisible();

    // 「↑ メモに保存」→ トースト→ メモタブに反映(source_message_id 経由で根拠アンカー複写)。
    await assistantMsg.getByRole("button", { name: "↑ メモに保存" }).click();
    await expect(page.getByText("✓ メモに保存しました")).toBeVisible();

    await page.getByRole("tab", { name: "メモ" }).click();
    await expect(
      page.locator("[data-note-id]").filter({ hasText: "最小二乗回帰に帰着します" }),
    ).toBeVisible();
  });
});
