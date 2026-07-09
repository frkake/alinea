/* eslint-disable no-restricted-imports -- E2E 補助モジュールは親ディレクトリから import する(src の @/ エイリアス規約は外) */
import { expect, test } from "@playwright/test";
import { ORIGIN, freshArxivUrl, ingestArxiv, resolveRfItemId } from "../fixtures/api";

interface CollectionEntryDetail {
  library_item: { id: string };
}

interface CollectionDetailResponse {
  share: { token: string | null; status: string };
  entries: CollectionEntryDetail[];
}

/**
 * PW-15(plans/12 §4.3・M2-17): コレクション+共有。
 *
 * コレクション作成 UI は v1 に存在しない(`components/collections/BulkActionBar.tsx` の
 * `CollectionPopover` は既存コレクションへの追加のみ)。他 spec と同方針で `POST
 * /api/collections` を直接叩いて前提データを作る。並べ替えは `CollectionEntryList.tsx` の
 * 既存コメントが記す決定(dnd-kit を追加せず上下ボタンで実現)に従い、実装済みの「▲/▼」
 * ボタンで検証する(plans/12 の「順序ドラッグ」は plans/13 §1.5 の実装差分としてこの決定に
 * 読み替える。followups 参照)。
 *
 * 共有ページのサーバー fetch は `next: { revalidate: 60 }`(Data Cache)を使うため、一度
 * アクティブな状態で閲覧したトークンを直後に無効化して再読込しても最大 60 秒キャッシュされた
 * 200 が返り得る。そのため「無効化で404」は**閲覧したことのない**トークンで検証する
 * (発行直後・一度も `/c/{token}` を開かずに無効化。キャッシュ汚染を避ける決定)。
 */
test.describe("PW-15 コレクション+共有", () => {
  test("並べ替え・担当・締切→共有発行→匿名閲覧→無効化で404", async ({ page, browser }) => {
    const rfItemId = await resolveRfItemId(page);
    const second = await ingestArxiv(page, freshArxivUrl());
    const secondItemId = second.library_item_id;

    const createRes = await page.request.post("/api/collections", {
      headers: { Origin: ORIGIN },
      data: { name: `PW-15 輪読会 ${Date.now()}` },
    });
    expect(createRes.ok()).toBeTruthy();
    const collection = (await createRes.json()) as { id: string };
    const collectionId = collection.id;

    const getDetail = async (): Promise<CollectionDetailResponse> =>
      (await (await page.request.get(`/api/collections/${collectionId}`)).json()) as CollectionDetailResponse;

    try {
      for (const libraryItemId of [rfItemId, secondItemId]) {
        const res = await page.request.post(`/api/collections/${collectionId}/entries`, {
          headers: { Origin: ORIGIN },
          data: { library_item_id: libraryItemId },
        });
        expect(res.ok()).toBeTruthy();
      }

      await page.goto(`/collections/${collectionId}`);
      await expect(page.getByText(/輪読会/).first()).toBeVisible();

      const downButtons = page.getByRole("button", { name: "下へ移動" });
      await expect(downButtons).toHaveCount(2);

      // 1) 並べ替え: 1 番目(RF 論文。追加順)を下へ移動 → 2 番目になる。
      await downButtons.first().click();
      await expect
        .poll(async () => {
          const detail = await getDetail();
          return detail.entries.map((e) => e.library_item.id);
        })
        .toEqual([secondItemId, rfItemId]);

      // 2) 担当・発表時間・注記(2 番目 = RF 論文の行を編集)。
      await page.getByRole("button", { name: "編集" }).nth(1).click();
      await page.getByRole("checkbox", { name: "自分が担当" }).check();
      await page.getByLabel("発表時間(分)").fill("15");
      await page.getByLabel("注記").fill("時間があれば深掘り");
      await page.getByRole("button", { name: "保存" }).click();
      await expect(page.getByText("担当: 自分")).toBeVisible();

      // 3) 締切設定。
      await page.getByRole("button", { name: "締切を設定" }).click();
      const deadline = new Date();
      deadline.setDate(deadline.getDate() + 5);
      await page.locator("input[type='date']").fill(deadline.toISOString().slice(0, 10));
      await page.getByRole("button", { name: "保存" }).click();
      await expect(page.getByText(/締切.*残り\s*\d+\s*日/)).toBeVisible();

      // 4) 共有リンク発行→「メモを含める」ON(既定 false)→匿名閲覧(書誌+要約+許可メモのみ・
      //    noindex・「Alineaをはじめる」CTA)。
      await page.getByRole("button", { name: "共有リンクを発行" }).click();
      await expect(page.getByText("発行済み")).toBeVisible();
      await page.getByRole("switch", { name: "共有ページにメモを含める" }).click();
      await expect(page.getByRole("switch", { name: "共有ページにメモを含める" })).toHaveAttribute(
        "aria-checked",
        "true",
      );
      const tokenA = (await getDetail()).share.token;
      expect(tokenA).toMatch(/^[A-Za-z0-9]{8}$/);

      const anonContext = await browser.newContext({
        viewport: { width: 1440, height: 900 },
        locale: "ja-JP",
        timezoneId: "Asia/Tokyo",
      });
      try {
        const anonPage = await anonContext.newPage();
        const res = await anonPage.goto(`/c/${tokenA}`);
        expect(res?.status()).toBe(200);
        await expect(anonPage.getByText("Flow Straight and Fast", { exact: false })).toBeVisible();
        await expect(anonPage.getByText("直線経路で 1 ステップ生成。reflow が肝。")).toBeVisible();
        await expect(anonPage.getByText(/サンプルを直線で結ぶ最小二乗回帰/)).toBeVisible();
        await expect(anonPage.getByRole("link", { name: "Alineaをはじめる" })).toBeVisible();
        await expect(anonPage.locator('meta[name="robots"]')).toHaveAttribute("content", /noindex/);
      } finally {
        await anonContext.close();
      }

      // 5) 無効化→再発行(新トークン、一度も閲覧しない)→即無効化→初回アクセスで 404
      //    (§ 冒頭決定: revalidate キャッシュ汚染を避けるため未閲覧トークンで検証)。
      await page.getByRole("button", { name: "リンクを無効化" }).click();
      await page.getByRole("button", { name: "無効化する" }).click();
      await expect(page.getByText("未発行")).toBeVisible();

      await page.getByRole("button", { name: "共有リンクを発行" }).click();
      await expect(page.getByText("発行済み")).toBeVisible();
      const tokenB = (await getDetail()).share.token; // 無効化前に取得(revoke 後は null になる)。
      expect(tokenB).toMatch(/^[A-Za-z0-9]{8}$/);
      expect(tokenB).not.toBe(tokenA);

      await page.getByRole("button", { name: "リンクを無効化" }).click();
      await page.getByRole("button", { name: "無効化する" }).click();
      await expect(page.getByText("未発行")).toBeVisible();

      const anonContext2 = await browser.newContext({
        viewport: { width: 1440, height: 900 },
        locale: "ja-JP",
        timezoneId: "Asia/Tokyo",
      });
      try {
        const anonPage2 = await anonContext2.newPage();
        const res2 = await anonPage2.goto(`/c/${tokenB}`);
        expect(res2?.status()).toBe(404);
      } finally {
        await anonContext2.close();
      }
    } finally {
      // 後片付け(自分が作ったデータのみ。§14 の運用規則)。
      await page.request
        .delete(`/api/collections/${collectionId}`, { headers: { Origin: ORIGIN } })
        .catch(() => undefined);
      await page.request
        .delete(`/api/library-items/${secondItemId}`, { headers: { Origin: ORIGIN } })
        .catch(() => undefined);
    }
  });
});
