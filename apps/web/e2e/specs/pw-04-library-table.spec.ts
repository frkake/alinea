/* eslint-disable no-restricted-imports -- E2E 補助モジュールは親ディレクトリから import する(src の @/ エイリアス規約は外) */
import { expect, test } from "@playwright/test";

/**
 * PW-04(plans/12 §4.3・M1-24 分): ライブラリ テーブルビュー。
 * テーブル 10 列固定・クイックフィルタ 5 種の件数表示を検証する。
 *
 * plans/13 §7 の決定により、本 spec の「この条件を保存」(保存フィルタ)・複数選択→
 * 一括操作バーは M2-17 で解除する(LibraryTableView.tsx / QuickFilterBar.tsx の M0/M1
 * スコープコメントの通り、一括操作バー自体が未実装)。
 *
 * 属性フィルタ 5 種(ステータス/優先度/タグ/コレクション/品質等の複合ドロップダウン)は
 * plans/12 の記述にあるが、実装コード全体(`components/library/`)を検索しても対応する
 * コンポーネントが存在せず、未実装の機能ギャップである(QuickFilterBar.tsx のコメント
 * 「属性ドロップダウン…は非表示(M1)」が指す M1 でも実装されなかった)。test.fixme とし
 * followups に記載する(UI 実装は本レーンの所有範囲外)。
 */
test.describe("PW-04 ライブラリ テーブルビュー(M1 分)", () => {
  test("テーブル10列・クイックフィルタ5種の件数", async ({ page }) => {
    await page.goto("/library?view=table");
    await expect(page.getByRole("heading", { name: "ライブラリ" })).toBeVisible();
    await page.getByRole("radio", { name: "テーブル", exact: true }).click();

    const headerRow = page.getByRole("row").first();
    const columns = ["論文", "ステータス", "品質", "タグ", "優先度", "締切", "読書時間", "理解度", "追加日"];
    for (const label of columns) {
      await expect(headerRow.getByText(label, { exact: true })).toBeVisible();
    }
    // 先頭列(チェックボックス)を含め 10 列。
    await expect(headerRow.getByRole("checkbox", { name: "すべて選択" })).toBeVisible();

    const quickBar = page.getByRole("group", { name: "クイックフィルタ" });
    await expect(quickBar).toBeVisible();
    for (const label of ["すべて", "未読", "途中", "読了", "要再確認"]) {
      const chip = quickBar.getByRole("button", { name: new RegExp(`^${label}\\s*\\d*$`) });
      await expect(chip).toBeVisible();
    }

    // すべて = 未読+途中+読了+要再確認(PY-LIB-02 の恒等式の UI 側反映)。
    const counts: Record<string, number> = {};
    for (const label of ["すべて", "未読", "途中", "読了", "要再確認"]) {
      const text = await quickBar.getByRole("button", { name: new RegExp(`^${label}`) }).textContent();
      const m = /(\d+)$/.exec(text ?? "");
      counts[label] = m ? Number(m[1]) : 0;
    }
    expect(counts["すべて"]).toBe(counts["未読"] + counts["途中"] + counts["読了"] + counts["要再確認"]);
  });

  test.fixme(
    "属性フィルタ 5 種(未実装。components/library/ に対応コンポーネントが存在しない)",
    async () => {},
  );

  test.fixme("「この条件を保存」(保存フィルタ。M2-17)", async () => {});

  test.fixme("複数選択→一括操作バー 3 操作(一括操作バー自体が未実装。M2-17)", async () => {});
});
