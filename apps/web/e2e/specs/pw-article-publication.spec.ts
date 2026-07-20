/* eslint-disable no-restricted-imports -- E2E 補助モジュールは親ディレクトリから import する(src の @/ エイリアス規約は外) */
import { expect, test } from "@playwright/test";
import { resolveRfItemId } from "../fixtures/api";
import { openViewer } from "../fixtures/viewer";

/**
 * PW-ARTICLE-PUBLICATION(Task 26 / plans remaining-features Task 26):
 * 記事公開と公開記事コメントのエンドツーエンド。
 *
 * === 実行の延期(Task 32)===
 * この spec は実ステップと実アサーションを備えるが、Playwright 実行はフルスタック
 * (API + worker + DB + オブジェクトストレージ + 記事生成)を必要とし、記事生成→公開→
 * 別ユーザーでのコメント投稿→公開者のモデレーションという長い直列フローを含む。
 * plans の Task 32(全 E2E の実行・回帰スイート整備)で実行を有効化する。
 * それまでは意図的に `test.describe.fixme` でマークして「サイレントなスキップ」ではなく
 * 「明示的な延期」であることを記録する(Task 26 のスコープは spec の記述まで)。
 *
 * Task 32 で有効化する手順:
 *   1) `test.describe.fixme(` を `test.describe(` に戻す。
 *   2) 2 人目のユーザー(コメント投稿者)を用意する認証ヘルパーを fixtures に追加する。
 *   3) global.setup のシードに「公開済み記事 + 1 コメント」を含めるか、本 spec の
 *      前段で記事生成まで行う(PW-13 と同じ ArticleGenerateCTA 経由)。
 */
test.describe.fixme("PW-ARTICLE-PUBLICATION 記事公開とコメント", () => {
  test("所有者が限定公開→公開→コメント→モデレーション→公開解除まで通せる", async ({ page, browser }) => {
    test.setTimeout(180_000);

    // --- 1) 記事を用意して記事モードを開く(所有者) ---
    const itemId = await resolveRfItemId(page);
    await openViewer(page, itemId, "article");
    await page.getByRole("radio", { name: "記事", exact: true }).click();
    await expect(page).toHaveURL(/mode=article/);

    // 記事が無ければ生成する(PW-13 と同じ導線)。
    if (await page.getByText("この論文の記事はまだありません").isVisible().catch(() => false)) {
      await page.getByRole("radio", { name: "初学者向け", exact: true }).click();
      await page.getByRole("button", { name: "✦ 記事を生成" }).click();
      await expect(page.getByRole("main").getByText("元の論文とは別物です", { exact: false })).toBeVisible({
        timeout: 60_000,
      });
    }

    // --- 2) 公開モーダルを開く(除外ブロック + ライセンス判定の説明を確認) ---
    await page.getByRole("button", { name: "公開", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: /この記事を公開/ });
    await expect(dialog).toBeVisible();
    await expect(dialog.getByText("公開されるもの")).toBeVisible();
    await expect(dialog.getByText("公開されないもの")).toBeVisible();
    await expect(dialog.getByText(/原論文の図・表/)).toBeVisible();
    await expect(dialog.getByText(/ライセンス判定/)).toBeVisible();

    // --- 3) 限定公開(unlisted)で公開する ---
    await dialog.getByRole("radio", { name: /限定公開/ }).click();
    await dialog.getByRole("button", { name: "限定公開する" }).click();
    await expect(page.getByText("記事を限定公開しました")).toBeVisible();

    // 公開設定を再度開き、公開 URL(slug)を取得する。
    await page.getByRole("button", { name: "公開設定", exact: true }).click();
    const slugLink = page.getByRole("link", { name: /^\/a\// });
    const slugHref = await slugLink.getAttribute("href");
    expect(slugHref).toBeTruthy();
    const slug = (slugHref ?? "").split("/a/")[1] ?? "";
    expect(slug.length).toBeGreaterThan(0);

    // --- 4) unlisted ページは noindex(検索索引を拒否)---
    const unlistedResp = await page.request.get(`/a/${slug}`);
    expect(unlistedResp.ok()).toBeTruthy();
    const unlistedHtml = await unlistedResp.text();
    expect(unlistedHtml).toMatch(/noindex/);

    // --- 5) public へ昇格する ---
    await page.getByRole("radio", { name: /^公開\(検索エンジンに載せる\)/ }).click();
    await page.getByRole("button", { name: "変更を保存" }).click();
    await expect(page.getByText("記事を公開しました")).toBeVisible();

    // public ページは記事本文・書誌・公開者・匿名向けログイン CTA を表示する。
    // 匿名コンテキスト(別ブラウザ)で開いて未ログイン状態を検証する。
    const anon = await browser.newContext();
    const anonPage = await anon.newPage();
    await anonPage.goto(`/a/${slug}`);
    await expect(anonPage.getByRole("heading", { level: 1 })).toBeVisible();
    await expect(anonPage.getByText("元の論文")).toBeVisible(); // 書誌カード
    await expect(anonPage.getByText(/Alinea/)).toBeVisible(); // 公開者
    await expect(anonPage.getByRole("link", { name: /ログイン/ }).first()).toBeVisible();
    // 匿名には投稿フォーム(textbox)が無い。
    await expect(anonPage.getByRole("textbox")).toHaveCount(0);
    await anon.close();

    // --- 6) 認証済みユーザーとしてブロックへコメント投稿(所有者本人で代用可) ---
    await page.goto(`/a/${slug}`);
    const composer = page.getByRole("textbox", { name: "コメントを入力" }).first();
    await composer.fill("とても分かりやすい解説でした。");
    await page.getByRole("button", { name: "投稿" }).first().click();
    await expect(page.getByText("とても分かりやすい解説でした。")).toBeVisible();

    // 返信(1 階層のみ)。
    await page.getByRole("button", { name: "返信" }).first().click();
    await page.getByRole("textbox", { name: "返信を入力" }).fill("同感です。");
    await page.getByRole("button", { name: "投稿" }).last().click();
    await expect(page.getByText("同感です。")).toBeVisible();

    // --- 7) 公開者モデレーション: 非表示 → 再表示 ---
    await page.getByRole("button", { name: "非表示" }).first().click();
    await expect(page.getByText(/公開者によって非表示にされました/)).toBeVisible();
    await page.getByRole("button", { name: "再表示" }).first().click();
    await expect(page.getByText("とても分かりやすい解説でした。")).toBeVisible();

    // --- 8) 投稿者による編集・削除 ---
    await page.getByRole("button", { name: "編集" }).first().click();
    const editBox = page.getByRole("textbox", { name: "コメントを編集" });
    await editBox.fill("とても分かりやすい解説でした(編集)。");
    await page.getByRole("button", { name: "保存" }).click();
    await expect(page.getByText("とても分かりやすい解説でした(編集)。")).toBeVisible();
    await page.getByRole("button", { name: "削除" }).first().click();
    await expect(page.getByText(/このコメントは削除されました/)).toBeVisible();

    // --- 9) 公開解除(slug は予約され public ページは 404 になる)---
    await page.goto(`/papers/${itemId}?mode=article`);
    await page.getByRole("button", { name: "公開設定", exact: true }).click();
    await page.getByRole("button", { name: "公開を解除" }).click();
    await expect(page.getByText(/公開を解除しました/)).toBeVisible();
    const goneResp = await page.request.get(`/a/${slug}`);
    expect(goneResp.status()).toBe(404);
  });
});
