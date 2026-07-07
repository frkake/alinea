/* eslint-disable no-restricted-imports -- E2E 補助モジュールは親ディレクトリから import する(src の @/ エイリアス規約は外) */
import { expect, test } from "@playwright/test";
import { ORIGIN, resolveRfItemId } from "../fixtures/api";
import { dragSelect, openViewer } from "../fixtures/viewer";

/**
 * PW-10(plans/12 §4.3): 選択メニュー。4 色ハイライト・コメント・コピーの実行、注釈一覧の
 * フィルタと件数を検証する。
 *
 * 「語彙に追加」は plans/12 の記述では PW-10 に含まれるが、実装は plans/13 §1.5 の段階公開規則
 * (SelectionMenu.tsx コメント「語彙に追加は M2 まで非表示」= 語彙帳バックエンドは M2-11)に従い
 * M1 の選択メニューには存在しない。plans/12 と plans/13 の記述差は plans/13 側(work breakdown)
 * を実装の正として扱い、本 spec では test.fixme で明示する(followups 参照)。
 */
test.describe("PW-10 選択メニュー・注釈一覧", () => {
  test("4色ハイライト・コメント・コピー→注釈一覧のフィルタと件数", async ({ page, context }) => {
    // navigator.clipboard.writeText は既定で権限拒否され reject する(ヘッドレス Chromium)。
    await context.grantPermissions(["clipboard-write", "clipboard-read"]);
    const itemId = await resolveRfItemId(page);
    await openViewer(page, itemId, "translation");

    // 事前件数(§14 シードは注釈 0 件想定だが、他 spec の副作用に依存しないよう実測する)。
    const before = (await (await page.request.get(`/api/library-items/${itemId}/annotations?kind=highlight`)).json()) as {
      counts?: { all?: number };
    };
    const baseCount = before.counts?.all ?? 0;

    const dismiss = page.getByRole("button", { name: "閉じる" });
    if (await dismiss.isVisible().catch(() => false)) await dismiss.click();

    const paragraphs = page.locator(".yk-paragraph[data-block-id]");
    await expect(paragraphs.first()).toBeVisible();

    // 1) 重要(important)ハイライト。
    const p1 = paragraphs.nth(0);
    await p1.scrollIntoViewIfNeeded();
    await dragSelect(page, p1);
    const menu = page.getByRole("menu", { name: "選択メニュー" });
    await expect(menu).toBeVisible();
    await menu.getByRole("menuitem", { name: "重要でハイライト" }).click();
    await expect(menu).toBeHidden();

    // 2) 疑問(question)+コメント。
    const p2 = paragraphs.nth(1);
    await p2.scrollIntoViewIfNeeded();
    await dragSelect(page, p2);
    const menu2 = page.getByRole("menu", { name: "選択メニュー" });
    await expect(menu2).toBeVisible();
    await menu2.getByRole("menuitem", { name: "コメント" }).click();
    const commentDialog = page.getByRole("dialog", { name: "コメントを入力" });
    await expect(commentDialog).toBeVisible();
    await commentDialog.getByRole("button", { name: "疑問を選択" }).click();
    await commentDialog.getByRole("textbox", { name: "コメント本文" }).fill("この定義の根拠を確認したい");
    await commentDialog.getByRole("button", { name: "保存" }).click();

    try {
      // 3) コピー(プレーン)。
      const p3 = paragraphs.nth(2);
      await p3.scrollIntoViewIfNeeded();
      await dragSelect(page, p3);
      const menu3 = page.getByRole("menu", { name: "選択メニュー" });
      await expect(menu3).toBeVisible();
      await menu3.getByRole("menuitem", { name: "コピー" }).click();
      await menu3.getByRole("menuitem", { name: "プレーンでコピー" }).click();
      await expect(page.getByText("コピーしました")).toBeVisible();

      // 注釈タブ: 件数(すべて=事前+2・重要=事前+1・疑問=事前+1)とコメントのみフィルタ。
      await page.getByRole("tab", { name: "注釈" }).click();
      await expect(page.getByRole("button", { name: new RegExp(`^すべて\\s*${baseCount + 2}$`) })).toBeVisible();
      await expect(page.getByRole("button", { name: /^重要\s*\d+$/ })).toBeVisible();
      await expect(page.getByRole("button", { name: /^疑問\s*\d+$/ })).toBeVisible();

      await page.getByRole("button", { name: "コメントのみ" }).click();
      const cards = page.locator("[data-annotation-id]");
      await expect(cards).toHaveCount(1);
      await expect(cards.first().getByText("この定義の根拠を確認したい")).toBeVisible();
    } finally {
      // 後片付け(自分が作った 2 件の注釈のみ削除。§14 の運用規則。VR-1a/1b/1c 等の決定性に
      // 影響しない)。コメント文言・色+コメント無しで自分が作った行を一意に特定する。
      const after = (await (
        await page.request.get(`/api/library-items/${itemId}/annotations?kind=highlight`)
      ).json()) as { items?: { id: string; color?: string | null; comment?: string | null }[] };
      const mine = (after.items ?? []).filter(
        (a) =>
          a.comment === "この定義の根拠を確認したい" ||
          (a.color === "important" && a.comment == null),
      );
      for (const a of mine) {
        await page.request.delete(`/api/annotations/${a.id}`, { headers: { Origin: ORIGIN } }).catch(() => undefined);
      }
    }
  });

  test.fixme(
    "「語彙に追加」の実行(M2-11 語彙帳バックエンド完成後。plans/13 §1.5)",
    async () => {},
  );
});
