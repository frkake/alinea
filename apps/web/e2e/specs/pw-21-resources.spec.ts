/* eslint-disable no-restricted-imports -- E2E 補助モジュールは親ディレクトリから import する(src の @/ エイリアス規約は外) */
import { expect, test } from "@playwright/test";
import { ORIGIN, resolveRfItemId } from "../fixtures/api";
import { openViewer } from "../fixtures/viewer";

/**
 * PW-21(plans/12 §4.3・M2-17): リソース(5a)。
 * URL 貼り付け(4 種)→自動判定・メタ表示→公式実装の破線提案カード「+ 追加」→公式バッジ→
 * メモに §チップ→クリックで本文ジャンプを検証する。
 *
 * kind 判定は URL ホスト/拡張子のみで決まる(PY-RES-01。ネットワーク不要)。メタ取得
 * (GitHub/YouTube)は worker が実 github.com/youtube.com を叩く実装のため(§8.4 のモック
 * サーバに接続先を切り替える仕組みが無い — followups 参照)、決定的モックに依存せず
 * 「取得失敗でも kind・URL 登録自体は完了する」(PY-RES-02)という設計の余裕を使い、fake な
 * リポジトリ/一般ページ URL では kind のみを検証する。YouTube のサムネ+再生時間バッジ
 * (AC-12-04)のみ、既知の安定した実動画 1 件で実ネットワーク越しに検証する。
 */
test.describe("PW-21 リソース", () => {
  test("URL貼付→kind自動判定→公式実装提案→追加→§チップ→本文ジャンプ", async ({ page }) => {
    const itemId = await resolveRfItemId(page);
    await openViewer(page, itemId, "translation");

    await page.getByRole("tab", { name: "リソース" }).click();
    const urlInput = page.getByRole("textbox", { name: "リソースの URL" });
    await expect(urlInput).toBeVisible();

    const before = (await (
      await page.request.get(`/api/library-items/${itemId}/resources`)
    ).json()) as { count: number };
    const baseCount = before.count;

    const suffix = Date.now();
    const urls: Array<{ url: string; kind: string }> = [
      { url: `https://github.com/e2e-test-org/nonexistent-repo-${suffix}`, kind: "github" },
      { url: `https://example.com/e2e/blog/post-${suffix}`, kind: "article" },
      { url: `https://example.com/e2e/slides-${suffix}.pdf`, kind: "slides" },
    ];

    for (const { url } of urls) {
      await urlInput.fill(url);
      await page.getByRole("button", { name: "追加", exact: true }).click();
      await expect(urlInput).toHaveValue("");
    }

    await expect(page.locator("[data-resource-id]")).toHaveCount(urls.length, { timeout: 15_000 });

    const afterAdd = (await (
      await page.request.get(`/api/library-items/${itemId}/resources`)
    ).json()) as { items: { url: string; kind: string }[] };
    for (const { url, kind } of urls) {
      const found = afterAdd.items.find((it) => it.url === url);
      expect(found?.kind).toBe(kind);
    }

    // 「開く ↗」が新規タブ(target=_blank + noopener noreferrer)。
    const firstCard = page.locator("[data-resource-id]").first();
    const openLink = firstCard.getByRole("link", { name: "開く ↗" });
    await expect(openLink).toHaveAttribute("target", "_blank");
    await expect(openLink).toHaveAttribute("rel", /noopener/);

    // 公式実装 自動検出→「+ 追加」→公式バッジ(paper.official_repo_url。§14 シード固有)。
    const suggestion = page.getByText("✦ 公式実装を検出しました");
    await expect(suggestion).toBeVisible();
    await page.getByRole("button", { name: "+ 追加" }).click();
    await expect(page.getByText("公式実装")).toBeVisible({ timeout: 15_000 });
    await expect(suggestion).toHaveCount(0);

    // 件数バッジ = active のみ(suggested/dismissed を数えない)。追加後は urls.length+1。
    const finalCount = (await (
      await page.request.get(`/api/library-items/${itemId}/resources`)
    ).json()) as { count: number };
    expect(finalCount.count).toBe(baseCount + urls.length + 1);

    // メモの §チップ→クリックで本文ジャンプ。
    await firstCard.getByRole("button", { name: "リソースの操作" }).click();
    await page.getByRole("menuitem", { name: "メモを追加" }).click();
    await page.getByRole("textbox", { name: "ひとことメモ" }).fill("関連: [[sec:sec-2|§2 Method]]");
    await page.getByRole("button", { name: "保存" }).click();

    // ノート本文プレビュー(「💬 関連: §2 Method」)も部分一致するため exact で実チップに絞る。
    const chip = firstCard.getByRole("button", { name: "§2 Method", exact: true });
    await expect(chip).toBeVisible();
    await chip.click();
    await expect(page.getByRole("heading", { name: /Method/ }).first()).toBeInViewport({
      timeout: 10_000,
    });
  });

  test("YouTube: サムネ+再生時間バッジ(実ネットワーク・既知の安定動画)", async ({ page }) => {
    // worker の実メタ取得(fetch_resource_meta.py)は httpx trust_env=False(直接接続)を使う
    // (§8.4 のモックサーバに接続先を切り替える仕組みが無いための本レーンの既知の制約。
    // pw-21 冒頭コメント参照)。企業プロキシ環境では直接の DNS 解決/接続ができないため、実行前に
    // 到達可否を確認し、不可なら skip する(production の trust_env=False は変更しない。
    // followups 参照。CI 等の直接 internet アクセス可能な環境ではそのまま実行される)。
    let reachable = true;
    try {
      const res = await fetch("https://www.youtube.com/oembed?url=https%3A%2F%2Fwww.youtube.com%2Fwatch%3Fv%3DdQw4w9WgXcQ&format=json", {
        signal: AbortSignal.timeout(4_000),
      });
      reachable = res.ok;
    } catch {
      reachable = false;
    }
    test.skip(!reachable, "youtube.com へ直接到達できない(企業プロキシ環境。followups 参照)");

    const itemId = await resolveRfItemId(page);
    await openViewer(page, itemId, "translation");
    await page.getByRole("tab", { name: "リソース" }).click();

    const urlInput = page.getByRole("textbox", { name: "リソースの URL" });
    await urlInput.fill("https://www.youtube.com/watch?v=dQw4w9WgXcQ");
    await page.getByRole("button", { name: "追加", exact: true }).click();

    const card = page.locator("[data-resource-id]").last();
    await expect(card.getByRole("img")).toBeVisible({ timeout: 20_000 });
    await expect(card.getByText(/\d+:\d{2}/)).toBeVisible({ timeout: 20_000 });

    // 後片付け(UI の「削除」は 6 秒後の確定 DELETE。念のため API でも直接削除する)。
    await card.getByRole("button", { name: "リソースの操作" }).click();
    await page.getByRole("menuitem", { name: "削除" }).click();
    try {
      const listRes = await page.request.get(`/api/library-items/${itemId}/resources`);
      const body = (await listRes.json()) as { items: { id: string; url: string }[] };
      const leftover = body.items.find((it) => it.url.includes("dQw4w9WgXcQ"));
      if (leftover) {
        await page.request.delete(`/api/resources/${leftover.id}`, { headers: { Origin: ORIGIN } });
      }
    } catch {
      // ベストエフォート(§14 の運用規則)。
    }
  });
});
