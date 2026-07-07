/* eslint-disable no-restricted-imports -- E2E 補助モジュールは親ディレクトリから import する(src の @/ エイリアス規約は外) */
import { expect, test } from "@playwright/test";
import { ORIGIN, resolveRfItemId } from "../fixtures/api";
import { openViewer } from "../fixtures/viewer";

/**
 * PW-11(plans/12 §4.3): リビジョン昇格 + 参考文献展開。
 *
 * 「参考文献展開→「+ この論文も取り込む」/「ライブラリに有り ✓」」は §14 シードの参考文献 1
 * (論文自身。ライブラリに有り)+参考文献 2(arXiv 2006.11239。M1-24 追補で url を付与し
 * 取り込み可能にした)で検証する。
 *
 * 「B 論文に昇格提案通知→「変更する」→新リビジョン適用」は test.fixme とする(発見した実装
 * ギャップ。理由は fixme の説明を参照。followups 参照)。新リビジョン切替+リアンカー自体
 * (`POST /api/library-items/{id}/adopt-revision`)は PY-ANN-02 相当(`test_adopt_revision.py`)
 * が pytest で担保している。
 */
test.describe("PW-11 参考文献展開・リビジョン昇格", () => {
  test("参考文献展開: ライブラリに有り✓ / +この論文も取り込む", async ({ page }) => {
    const itemId = await resolveRfItemId(page);
    await openViewer(page, itemId, "translation");

    await page.getByRole("tab", { name: "図表" }).click();

    // 参考文献 1(論文自身。arXiv 2209.03003)→ 展開 →「ライブラリに有り ✓」。
    const ref1Row = page.getByRole("button", { name: /^\[1\]/ });
    await ref1Row.click();
    const inLibraryBtn = page.getByRole("button", { name: "ライブラリに有り ✓" });
    await expect(inLibraryBtn).toBeVisible();
    await inLibraryBtn.click();
    await expect(page).toHaveURL(new RegExp(`/papers/${itemId}`));

    // 参考文献 2(Ho et al. DDPM。arXiv 2006.11239)→ 展開 →「+ この論文も取り込む」。
    // 「図表」タブは既に開いているため再クリックしない(再クリックはタブを閉じる仕様。
    // ui/SidePanelTabs.tsx の「アクティブタブ再クリックで閉じる」)。
    const ref2Row = page.getByRole("button", { name: /^\[2\]/ });
    await ref2Row.click();
    const importBtn = page.getByRole("button", { name: "+ この論文も取り込む" });
    await expect(importBtn).toBeVisible();
    const [ingestResponse] = await Promise.all([
      page.waitForResponse((r) => /\/api\/ingest\/arxiv$/.test(r.url()) && r.status() === 202),
      importBtn.click(),
    ]);
    await expect(page.getByText("✓ ライブラリに追加しました")).toBeVisible();

    // 再取り込み済みなら再展開で「ライブラリに有り ✓」に切り替わる(references 再取得)。
    await ref2Row.click();
    await ref2Row.click();
    await expect(page.getByRole("button", { name: "ライブラリに有り ✓" })).toBeVisible();

    // 後片付け(自分が作った参考文献 2 の取り込みアイテムのみ削除。再実行時に「ライブラリに
    // 有り ✓」へ固定されてしまい「+ この論文も取り込む」を再現できなくなるのを防ぐ)。
    const { library_item_id: importedId } = (await ingestResponse.json()) as {
      library_item_id: string;
    };
    const delRes = await page.request.delete(`/api/library-items/${importedId}`, {
      headers: { Origin: ORIGIN },
    });
    expect(delRes.status()).toBe(204);
  });

  test.fixme(
    "B 論文に昇格提案通知→「変更する」→新リビジョン適用(発見した実装ギャップ。followups 参照): " +
      "notifications_action の promote_revision + apply 分岐は resolved 消化のみ行い、" +
      "adopt-revision(reingest→新リビジョン取得→適用)を呼ばない(routers/notifications.py の" +
      "コメント「followup: M1-22 で adopt-revision に接続」が未対応のまま残っている)",
    async () => {},
  );
});
