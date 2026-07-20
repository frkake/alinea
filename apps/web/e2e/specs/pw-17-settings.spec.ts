/* eslint-disable no-restricted-imports -- E2E 補助モジュールは親ディレクトリから import する(src の @/ エイリアス規約は外) */
import { expect, test } from "@playwright/test";
import { ORIGIN, resolveRfItemId } from "../fixtures/api";

/**
 * PW-17(plans/12 §4.3): 設定。
 * 8 カテゴリ表示・翻訳トグルの反映(PATCH 往復で永続)・アクセント 4 色切替(`<html
 * data-accent>` で検証)・本文書体切替(`<html data-body-font>`)・BYOK 登録→マスク表示・
 * 平文再表示不可を検証する。
 *
 * 「付録(未翻訳セクション)の目次表示」は Task 32 で実操作化する。§14 シードは既定で
 * 要旨+先頭セクションのみ訳済み(sec-0/sec-1)で、以降(sec-2 以降)は「取り込み直後の
 * 未翻訳=オンデマンド」状態になる。これは付録の自動翻訳オフ時と同じ TOC 表示分岐
 * (「— 未翻訳」「未翻訳セクションを一括翻訳」)を通るため、この機構を決定的に検証できる。
 * 付録セクションそのものを持つ固定論文は現状のシード/モックに無いため、付録専用文言では
 * なく共通の未翻訳表示機構を検証する。
 */
test.describe("PW-17 設定", () => {
  test("8カテゴリ・翻訳トグル永続化・アクセント/書体切替・BYOK", async ({ page }) => {
    // 冪等性(2 連続実行対策): OpenAI キーが前回実行分で残っていれば消しておく
    // (「設定」ボタンが BYOK_PROVIDERS 先頭= OpenAI に来る前提を保つ)。
    await page.request.delete("/api/settings/api-keys/openai", { headers: { Origin: ORIGIN } });

    await page.goto("/settings");

    const nav = page.getByRole("navigation", { name: "設定カテゴリ" });
    await expect(nav).toBeVisible();
    const categories = ["アカウント", "表示", "翻訳", "読書の計測と提案", "チャット", "通知", "エクスポート", "ブラウザ拡張"];
    for (const label of categories) {
      await expect(nav.getByRole("button", { name: label })).toBeVisible();
    }

    // 翻訳カテゴリ: トグル切替→ PATCH →リロードでも値が残る(永続化)。
    await nav.getByRole("button", { name: "翻訳" }).click();
    const appendixToggle = page.getByRole("switch", { name: "付録(Appendix)を自動翻訳しない" });
    await expect(appendixToggle).toBeVisible();
    const before = await appendixToggle.getAttribute("aria-checked");
    await appendixToggle.click();
    await expect(appendixToggle).not.toHaveAttribute("aria-checked", before ?? "");
    const after = await appendixToggle.getAttribute("aria-checked");
    await page.reload();
    await expect(page.getByRole("switch", { name: "付録(Appendix)を自動翻訳しない" })).toHaveAttribute(
      "aria-checked",
      after ?? "",
    );

    // 表示カテゴリ: アクセント 4 色切替(<html data-accent>)+本文書体切替(<html data-body-font>)。
    await page.getByRole("navigation", { name: "設定カテゴリ" }).getByRole("button", { name: "表示" }).click();
    const accentGroup = page.getByRole("radiogroup", { name: "アクセントカラー" });
    await accentGroup.getByRole("radio", { name: "緑" }).click();
    await expect(page.locator("html")).toHaveAttribute("data-accent", "green");
    await accentGroup.getByRole("radio", { name: "紫" }).click();
    await expect(page.locator("html")).toHaveAttribute("data-accent", "purple");
    await accentGroup.getByRole("radio", { name: "スレートブルー" }).click();
    await expect(page.locator("html")).toHaveAttribute("data-accent", "slate");

    await page.getByRole("radio", { name: "ゴシック", exact: true }).click();
    await expect(page.locator("html")).toHaveAttribute("data-body-font", "sans");
    await page.getByRole("radio", { name: "明朝", exact: true }).click();
    await expect(page.locator("html")).toHaveAttribute("data-body-font", "serif");

    // アカウントカテゴリ: BYOK 登録→マスク表示、平文は API 応答にも含まれない。
    // OpenAI は BYOK_PROVIDERS の先頭(types.ts)= 未設定時に最初に現れる「設定」ボタン。
    await page.getByRole("navigation", { name: "設定カテゴリ" }).getByRole("button", { name: "アカウント" }).click();
    await expect(page.getByText("OpenAI", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "設定" }).first().click();
    const keyInput = page.getByRole("textbox", { name: "OpenAI の API キー" });
    await keyInput.fill("sk-e2e-test-abcd");
    await page.getByRole("button", { name: "保存" }).click();
    await expect(page.getByText(/^sk-…abcd/)).toBeVisible();

    const listRes = await page.request.get("/api/settings/api-keys");
    const listBody = await listRes.text();
    expect(listBody).not.toContain("sk-e2e-test-abcd");

    // 後片付け(自分が作ったデータのみ削除。§14 の運用規則)。
    await page.request.delete("/api/settings/api-keys/openai", { headers: { Origin: ORIGIN } });
  });

  test("未翻訳(オンデマンド)セクションが目次に「— 未翻訳」+一括翻訳導線で表示される", async ({
    page,
  }) => {
    // §14 シードの RF アイテムを訳文モードで開く(既定で sec-2 以降が未翻訳=オンデマンド)。
    const itemId = await resolveRfItemId(page);
    await page.goto(`/papers/${itemId}?mode=translation`);
    await expect(page.getByRole("radiogroup", { name: "表示モード" })).toBeVisible();

    // 目次を開く(折りたたみレール → パネル)。
    const openToc = page.getByRole("button", { name: "目次を開く" });
    if (await openToc.isVisible().catch(() => false)) {
      await openToc.click();
    }
    const toc = page.getByRole("navigation", { name: "目次" });
    await expect(toc).toBeVisible();

    // 付録の自動翻訳オフ時と同じ機構: 未翻訳セクションは「— 未翻訳」を表示し、
    // 「未翻訳セクションを一括翻訳」導線が現れる。
    await expect(toc.getByText("— 未翻訳").first()).toBeVisible();
    await expect(
      toc.getByRole("button", { name: "未翻訳セクションを一括翻訳" }),
    ).toBeVisible();
  });
});
