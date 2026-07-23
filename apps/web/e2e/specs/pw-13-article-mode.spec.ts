/* eslint-disable no-restricted-imports -- E2E 補助モジュールは親ディレクトリから import する(src の @/ エイリアス規約は外) */
import { expect, test } from "@playwright/test";
import { resolveRfItemId } from "../fixtures/api";
import { openViewer } from "../fixtures/viewer";

/**
 * PW-13(plans/12 §4.3・M2-17): 記事モード。
 * モード切替から開く→プリセット選択生成→メタ行→概要図(3 カード・版管理・SVG ⤓)→
 * ブロックホバー 3 操作→根拠チップ→原文ジャンプ→出典ブロック末尾固定、および
 * 「公開/限定公開/コメント UI が存在しない」の否定検査(A17)を検証する。
 *
 * §14 シード(rectified-flow)には記事が事前投入されていない(`article.json`/
 * `overview_dsl.json` は未使用の死んだ fixture — followups 参照)ため、本テストが
 * `ArticleGenerateCTA` から実際に生成する。`--reset` により実行ごとに記事は存在しないため、
 * 2 連続実行のいずれも初回生成から検証できる。
 */
test.describe("PW-13 記事モード", () => {
  test("生成→メタ行→概要図(版管理・SVG⤓)→ブロック操作→根拠ジャンプ→出典固定・公開UI不在", async ({
    page,
  }) => {
    // 記事生成+概要図書き直し×2 を直列で行うため既定 60s では窮屈になり得る。
    test.setTimeout(120_000);
    const itemId = await resolveRfItemId(page);
    await openViewer(page, itemId, "translation");

    // 1) モード切替から記事モードを開く。
    await page.getByRole("radio", { name: "記事", exact: true }).click();
    await expect(page).toHaveURL(/mode=article/);

    // 2) 未生成 CTA: プリセット選択→生成。
    await expect(page.getByText("この論文の記事はまだありません")).toBeVisible();
    // 読者タイプは role=tab(tablist「読者タイプ別の記事」)。未生成時は名前に " ＋" が付く。
    await page.getByRole("tab", { name: "初学者向け ＋", exact: true }).click();
    await page.getByRole("button", { name: "✦ 記事を生成" }).click();
    await expect(page.getByText(/✦ 記事を生成しています/)).toBeVisible();

    // 3) メタ行(AI生成・生成日付・免責逐語)。サイドパネル(チャット)にも既存シードの
    //    「AI生成」バッジが表示されているため、main(記事本体)側に絞って検証する。
    //    免責文の逐語(一意なテキスト)を先に待つことで、生成ジョブの完了を確実に待機する
    //    (FakeLLM でも記事+概要図の生成には時間がかかり得るため既定 10s では不足)。
    const main = page.getByRole("main");
    await expect(
      main.getByText("訳文・メモ・チャット履歴から自動構成", { exact: false }),
    ).toBeVisible({ timeout: 45_000 });
    await expect(main.getByText("AI生成", { exact: true }).first()).toBeVisible();
    await expect(main.getByText("元の論文とは別物です", { exact: false })).toBeVisible();

    // 4) 概要図: 3 カード・版 1・SVG ⤓ ダウンロード。
    await expect(page.getByText("✦ 全体概要図")).toBeVisible();
    await expect(page.getByText("AI生成 · 版 1")).toBeVisible();
    const [svgDownload] = await Promise.all([
      page.waitForEvent("download"),
      page.getByRole("link", { name: "SVG ⤓" }).click(),
    ]);
    expect(svgDownload.suggestedFilename()).toMatch(/\.svg$/);

    // 5) 書き直し指示 → 版 2 → 書き直し指示 → 版 3 → 版 2 へ復帰。
    await page.getByRole("button", { name: "✦ 書き直し指示" }).first().click();
    await page.getByRole("textbox", { name: "書き直し指示" }).fill("結果セクションをもっと簡潔に");
    await page.getByRole("button", { name: "✦ 書き直す" }).click();
    await expect(page.getByText("AI生成 · 版 2")).toBeVisible({ timeout: 20_000 });

    await page.getByRole("button", { name: "✦ 書き直し指示" }).first().click();
    await page.getByRole("textbox", { name: "書き直し指示" }).fill("課題セクションを補強");
    await page.getByRole("button", { name: "✦ 書き直す" }).click();
    await expect(page.getByText("AI生成 · 版 3")).toBeVisible({ timeout: 20_000 });

    await page.getByRole("button", { name: "AI生成 · 版 3" }).click();
    await page.getByRole("button", { name: "この版に戻す" }).first().click();
    await expect(page.getByText("AI生成 · 版 2")).toBeVisible({ timeout: 20_000 });

    // 6) ブロックホバー 3 操作(✦書き直し指示/再生成/根拠を表示)。根拠は
    //    「手法」章の段落ブロック(article_v1 の固定 fixture が sec-2 の evidence を持つ)。
    const methodParagraph = page.getByText("確率フローを直線化する。");
    await methodParagraph.hover();
    const toolbar = page.getByRole("toolbar", { name: "ブロック操作" });
    await expect(toolbar).toBeVisible();
    await expect(toolbar.getByRole("button", { name: "✦ 書き直し指示" })).toBeVisible();
    await expect(toolbar.getByRole("button", { name: "再生成" })).toBeVisible();
    const showEvidence = toolbar.getByRole("button", { name: "根拠を表示" });
    await expect(showEvidence).toBeVisible();

    // 7) 根拠チップ→原文ジャンプ(mode=source へ遷移)。
    await showEvidence.click();
    await page.getByRole("button", { name: "原文で見る →" }).click();
    await expect(page).toHaveURL(/mode=source/);

    // 8) 出典ブロックが末尾固定・ホバーしても操作ツールバーが出ない(locked)。
    // 根拠ジャンプは router.replace(履歴を追加しない)のため goBack ではなく明示的に再遷移する。
    await page.goto(`/papers/${itemId}?mode=article`);
    const attribution = page.getByText("自動挿入 · 削除不可");
    await expect(page).toHaveURL(/mode=article/);
    await expect(attribution).toBeVisible();
    const allBlocks = page.locator("[data-article-block]");
    const lastBlockHtml = await allBlocks.last().innerHTML();
    expect(lastBlockHtml).toContain("自動挿入");
    await attribution.hover();
    await expect(page.getByRole("toolbar", { name: "ブロック操作" })).toHaveCount(0);

    // 9) 否定検査(A17 改訂): 記事本文内にはコメント UI が無く、公開モーダルは閉じている。
    //    ※ 記事の「公開」ボタン自体は Task 26 で実装済み(pw-article-publication.spec.ts が担保)
    //      のため、ボタン非存在の旧検査は撤回する。ここでは公開「モーダル/コメント」が
    //      閲覧状態で開いていないことのみを確認する。
    await expect(page.getByRole("button", { name: /コメントを(投稿|追加)/ })).toHaveCount(0);
    await expect(page.getByText(/この記事を公開/)).toHaveCount(0);
    await expect(page.getByText(/限定公開/)).toHaveCount(0);
  });
});
