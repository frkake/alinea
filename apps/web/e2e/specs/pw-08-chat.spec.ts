/* eslint-disable no-restricted-imports -- E2E 補助モジュールは親ディレクトリから import する(src の @/ エイリアス規約は適用外) */
import { expect, test } from "@playwright/test";
import { CHAT_DISCLAIMER_SNIPPET } from "../fixtures/constants";
import { resolveRfItemId } from "../fixtures/api";
import { dragSelect, openViewer } from "../fixtures/viewer";

/**
 * PW-08(plans/12 §4.3・M0 スコープ): チャット。
 * 本 spec は M0 で確実に配線された経路を検証する: 本文を選択 → 選択メニュー「✦ AIに質問」→
 * サイドパネルがチャットタブへ切り替わり、入力欄と免責文が出る。
 *
 * fixme(理由を明記):
 * - 引用チップ・SSE 回答・根拠チップ→本文ジャンプ・双方向ハイライト・「↑ メモに保存」:
 *   E2E で決定的な根拠チップを出すには、モック LLM サーバ(§8.4)がプロンプト中の実在 block_id を
 *   引用する `[[evidence:blk-XXX]]` を返す必要があるが現状はエコー応答で根拠を出さない。加えて
 *   チャット既定モデルの anthropic/openai ストリーミングアダプタが同梱 SDK と非互換
 *   (`output_config` / Responses API)。これらは packages/llm(別レーン)の課題。UI 単体は
 *   VT-VIEW-09/10(EvidenceChip/EvidenceHighlight)が担保。followups 参照。
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
    const para = page.locator(".yk-paragraph[data-block-id]").first();
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
    "SSE 回答→根拠チップ→本文ジャンプ→双方向ハイライト→メモに保存(モック LLM 根拠出力/アダプタ互換が前提)",
    async () => {},
  );
});
