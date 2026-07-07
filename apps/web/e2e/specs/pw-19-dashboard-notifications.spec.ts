/* eslint-disable no-restricted-imports -- E2E 補助モジュールは親ディレクトリから import する(src の @/ エイリアス規約は外) */
import { expect, test } from "@playwright/test";
import { freshArxivUrl, ingestArxiv, ORIGIN, waitForJob } from "../fixtures/api";

/**
 * PW-19(plans/12 §4.3): ダッシュボード+通知。
 * 続きを読む(≤3)・すぐ読むキュー(並べ替え。dnd-kit 非導入のため上下ボタン。UpNextQueue.tsx
 * の決定)・統計(今週読了・12 週棒グラフ)・ベル→ポップオーバー・提案「変更する/そのまま」・
 * 「すべて既読にする」を検証する。
 *
 * 締切カードは M2-09(コレクション機能)まで非表示(dashboard/page.tsx の決定)のため対象外。
 * 提案通知は 3 分ハートビート実時間を待たず、`active_seconds` をクライアント指定できる
 * ハートビート API の仕様を使って即時発火させる(reading_sessions.py `ACTIVE_RULE_SECONDS`)。
 */
test.describe("PW-19 ダッシュボード・通知", () => {
  test("ダッシュボード4セクション・すぐ読むキュー並べ替え・統計", async ({ page }) => {
    // すぐ読むキュー用に 2 件 up_next へ。
    const url1 = freshArxivUrl();
    const { library_item_id: li1, job_id: job1 } = await ingestArxiv(page, url1);
    await waitForJob(page, job1);
    const url2 = freshArxivUrl();
    const { library_item_id: li2, job_id: job2 } = await ingestArxiv(page, url2);
    await waitForJob(page, job2);
    const title2 = `Mock Paper for ${url2.split("/abs/")[1]}`;

    try {
      for (const id of [li1, li2]) {
        const res = await page.request.patch(`/api/library-items/${id}`, {
          headers: { "Content-Type": "application/json", Origin: ORIGIN },
          data: { status: "up_next" },
        });
        expect(res.status(), await res.text()).toBe(200);
      }
      // 既存の他アイテムより後ろに置く(相対順序のみ検証するため厳密な先頭化は不要)。
      const queueRes = await page.request.get("/api/dashboard");
      const queueBody = (await queueRes.json()) as { up_next_queue: { id: string }[] };
      const existingIds = queueBody.up_next_queue.map((i) => i.id).filter((id) => id !== li1 && id !== li2);
      await page.request.put("/api/library-items/queue-order", {
        headers: { "Content-Type": "application/json", Origin: ORIGIN },
        data: { library_item_ids: [...existingIds, li1, li2] },
      });

      await page.goto("/dashboard");
      await expect(page.getByRole("heading", { name: "続きを読む", level: 2 })).toBeVisible();
      await expect(page.getByRole("heading", { name: "すぐ読むキュー", level: 2 })).toBeVisible();
      await expect(page.getByRole("heading", { name: "最近追加", level: 2 })).toBeVisible();
      await expect(page.getByText("今週", { exact: true })).toBeVisible();
      await expect(page.getByText("直近 12 週の読書時間")).toBeVisible();

      // 続きを読むは最大 3 件(GET /api/dashboard の continue_reading が既に上限を課す。§4.4)。
      const continueSection = page.getByRole("heading", { name: "続きを読む", level: 2 }).locator("..");
      expect(await continueSection.getByRole("link").count()).toBeLessThanOrEqual(3);

      // すぐ読むキュー: li1・li2 を末尾に置いたため、最後の「上へ移動」ボタンは li2 の行
      // (相対順序で検証。他アイテムが同時に存在しても崩れないようにする)。タイトルは
      // 「最近追加」にも同名リンクが出るため role スコープをキューの見出しに限定する。
      // UpNextQueue.tsx: <section><div><h2/><span/></div>{list}</section> なので見出しの
      // 2 階層上が <section>(リストを含む)。
      const queueSection = page.getByRole("heading", { name: "すぐ読むキュー", level: 2 }).locator("../..");
      await expect(queueSection.getByText(title2)).toBeVisible();
      await page.getByRole("button", { name: "上へ移動" }).last().click();
      await expect
        .poll(async () => {
          const r = await page.request.get("/api/dashboard");
          const body = (await r.json()) as { up_next_queue: { id: string }[] };
          const ids = body.up_next_queue.map((i) => i.id);
          return ids.indexOf(li2) >= 0 && ids.indexOf(li2) < ids.indexOf(li1);
        })
        .toBe(true);
    } finally {
      // 後片付け(自分が up_next に変更した 2 件を戻す。§14 の運用規則。他 spec/再実行時の
      // すぐ読むキュー件数・並び順の決定性に影響しない)。
      for (const id of [li1, li2]) {
        await page.request
          .patch(`/api/library-items/${id}`, {
            headers: { "Content-Type": "application/json", Origin: ORIGIN },
            data: { status: "planned" },
          })
          .catch(() => undefined);
      }
    }
  });

  test("通知: 提案(変更する/そのまま)・すべて既読にする", async ({ page }) => {
    const url = freshArxivUrl();
    const { library_item_id, job_id } = await ingestArxiv(page, url);
    await waitForJob(page, job_id);

    // read_3min 提案を即時発火(status=planned の初期値のまま。active_seconds>=180)。
    const now = new Date().toISOString();
    const hbRes = await page.request.post(`/api/library-items/${library_item_id}/reading-sessions`, {
      headers: { "Content-Type": "application/json", Origin: ORIGIN },
      data: {
        client_session_id: `e2e-pw19-${Date.now()}`,
        started_at: now,
        last_activity_at: now,
        active_seconds: 200,
      },
    });
    expect(hbRes.status(), await hbRes.text()).toBe(200);

    await page.goto("/dashboard");
    await page.getByRole("button", { name: "通知" }).click();

    const suggestionText = page.getByText(/を 3 分以上読んでいます/).first();
    await expect(suggestionText).toBeVisible();
    // suggestionText の直親(フレックス列。質問文+ボタン行+注記が兄弟)。
    const row = suggestionText.locator("..");
    await row.getByRole("button", { name: "変更する" }).click();
    await expect(row.getByText(/✓ 「読んでいる」に変更しました/)).toBeVisible();

    await page.getByRole("button", { name: "すべて既読にする" }).click();
    await expect
      .poll(async () => {
        const r = await page.request.get("/api/auth/me");
        const body = (await r.json()) as { unread_notifications: number };
        return body.unread_notifications;
      })
      .toBe(0);
  });
});
