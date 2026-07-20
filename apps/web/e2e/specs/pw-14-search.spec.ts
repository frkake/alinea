/* eslint-disable no-restricted-imports -- E2E 補助モジュールは親ディレクトリから import する(src の @/ エイリアス規約は外) */
import { expect, test } from "@playwright/test";
import { ORIGIN, resolveRfItemId, waitForJob } from "../fixtures/api";

/**
 * PW-14(plans/12 §4.3・§11): 横断検索。
 * 1e ドロップダウン(プレビュー+すべての結果)→ 4e 全結果(源バッジ・論文グループ)→
 * 源別遷移。§11 の日英クロスコーパスのうち body(S1/S2 相当。RF 原文+chat.json の
 * 「EMA teacher」)は §14 シードにそのまま存在する。note(S4 相当)は notes.json が
 * seed.py から読み込まれていない(未接続の pre-wire フィクスチャ)ため、本 spec が
 * API 経由で作成して用意する。
 *
 * 記事(article)ソースは Task 32 で実操作化する: 記事生成バックエンド(M2-03)は
 * 実装済みのため、本 spec が RF アイテムの記事を生成し、記事本文の決定的トークンで
 * 「記事」バッジと「記事モードで開く →」遷移を検証する(article_v1 fake fixture 由来)。
 */
test.describe("PW-14 横断検索", () => {
  test("ドロップダウン→全結果→源バッジ・源別遷移(本文/メモ/チャット)", async ({ page }) => {
    const itemId = await resolveRfItemId(page);

    // note ソース(S4 相当)を用意。
    const noteRes = await page.request.post(`/api/library-items/${itemId}/notes`, {
      headers: { "Content-Type": "application/json", Origin: ORIGIN },
      data: { content_md: "reflow の反復回数と直線性の関係を後で確認" },
    });
    expect(noteRes.status(), await noteRes.text()).toBe(201);
    const { id: noteId } = (await noteRes.json()) as { id: string };

    await page.goto("/dashboard");

    // 1e ドロップダウン: 「EMA teacher」は本文(英語 abstract)+チャット(chat.json)の双方に
    // ヒットする(§11 の S2 相当)。
    const searchbox = page.getByRole("searchbox", {
      name: "ライブラリ全体を検索 — 本文・訳文・メモ・チャット",
    });
    await searchbox.click();
    await searchbox.fill("EMA teacher");
    const listbox = page.getByRole("listbox");
    await expect(listbox).toBeVisible();
    await expect(listbox.getByRole("option").first()).toBeVisible();
    await expect(listbox.getByText(/本文でヒット|チャット履歴/).first()).toBeVisible();

    await listbox.getByText(/すべての結果を表示/).click();
    await expect(page).toHaveURL(/\/search\?q=EMA(\+|%20)teacher/);
    await expect(page.getByText(/「EMA teacher」の結果/)).toBeVisible();

    // 源別遷移: ヒット行全体が1つの <a>(SearchHitRow)。ジャンプ文言(jumpLabelForTarget)で
    // 種別を判別してクリックする(plans/11 §4・§7)。

    // (1) 本文ヒット→ビューアの該当ブロックへ。
    await page.goto(`/search?q=${encodeURIComponent("EMA teacher")}`);
    const bodyResult = page.getByRole("link", { name: /該当位置へ →/ }).first();
    await expect(bodyResult).toBeVisible();
    await bodyResult.click();
    await expect(page).toHaveURL(/\/papers\/.+\?block=.+&hl=/);

    // (2) メモヒット→メモパネル。
    await page.goto(`/search?q=${encodeURIComponent("reflow")}`);
    const noteResult = page.getByRole("link", { name: /メモを開く →/ }).first();
    await expect(noteResult).toBeVisible();
    await noteResult.click();
    await expect(page).toHaveURL(/\/papers\/.+\?panel=notes&note=/);
    await expect(page.getByRole("tab", { name: "メモ" })).toHaveAttribute("aria-selected", "true");

    // (3) チャットヒット→チャットスレッド該当メッセージへ。
    await page.goto(`/search?q=${encodeURIComponent("蒸留")}`);
    const chatResult = page.getByRole("link", { name: /スレッドを開く →/ }).first();
    await expect(chatResult).toBeVisible();
    await chatResult.click();
    await expect(page).toHaveURL(/\/papers\/.+\?panel=chat&thread=.+&message=/);
    await expect(page.getByRole("tab", { name: "チャット" })).toHaveAttribute("aria-selected", "true");

    // 後片付け(自分が作ったデータのみ削除。§14 の運用規則。VR-4e 等の他 spec の決定性に影響しない)。
    await page.request.delete(`/api/notes/${noteId}`, { headers: { Origin: ORIGIN } });
  });

  test("記事ソースのバッジ「記事」・「記事モードで開く →」遷移", async ({ page }) => {
    test.setTimeout(120_000);
    const itemId = await resolveRfItemId(page);

    // 記事を生成(初学者向け)。--reset により実行時点では記事は未生成。
    const genRes = await page.request.post(`/api/library-items/${itemId}/article`, {
      headers: { "Content-Type": "application/json", Origin: ORIGIN },
      data: { preset: "beginner" },
    });
    // 既に存在(409)なら生成は不要。それ以外は 202 でジョブ完了を待つ。
    if (genRes.status() === 202) {
      const { job_id } = (await genRes.json()) as { job_id: string };
      expect((await waitForJob(page, job_id, 90_000)).status).toBe("succeeded");
    } else {
      expect(genRes.status(), await genRes.text()).toBe(409);
    }

    // 記事本文の決定的トークン(article_v1 fake fixture の「手法」章段落)。
    const q = "確率フローを直線化する";
    await page.goto(`/search?q=${encodeURIComponent(q)}`);
    await expect(page.getByText(new RegExp(`「${q}」の結果`))).toBeVisible();

    // 「記事」ソースへ絞り込む(facet rail)。
    await page.getByRole("button", { name: "記事", exact: true }).click();

    // 記事ヒット行: 「記事」バッジ + 「記事モードで開く →」ジャンプ。
    await expect(page.getByText("記事", { exact: true }).first()).toBeVisible();
    const articleResult = page.getByRole("link", { name: /記事モードで開く →/ }).first();
    await expect(articleResult).toBeVisible();
    await articleResult.click();

    // 記事ヒットは view=article + article_block を伴って論文を開く(searchNav.hrefForSearchTarget)。
    await expect(page).toHaveURL(/\/papers\/.+\?/);
    await expect(page).toHaveURL(/(view=article|mode=article)/);
    await expect(page).toHaveURL(/article_block=/);
  });
});
