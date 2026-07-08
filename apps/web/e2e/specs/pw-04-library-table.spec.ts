/* eslint-disable no-restricted-imports -- E2E 補助モジュールは親ディレクトリから import する(src の @/ エイリアス規約は外) */
import { expect, test } from "@playwright/test";
import { ORIGIN, freshArxivUrl, ingestArxiv } from "../fixtures/api";

/**
 * PW-04(plans/12 §4.3・M1-24 分完全化は M2-17): ライブラリ テーブルビュー。
 * テーブル 10 列固定・クイックフィルタ 5 種の件数表示・属性フィルタ(M2-14 で実装済み)・
 * 「この条件を保存」(保存フィルタ)・複数選択→一括操作バーを検証する。
 */
test.describe("PW-04 ライブラリ テーブルビュー", () => {
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
    const count = (label: string): number => counts[label] ?? 0;
    expect(count("すべて")).toBe(count("未読") + count("途中") + count("読了") + count("要再確認"));
  });

  test("属性フィルタ5種のドロップダウン→適用でチップ化", async ({ page }) => {
    await page.goto("/library?view=table");
    await page.getByRole("radio", { name: "テーブル", exact: true }).click();

    for (const label of ["ステータス", "タグ", "コレクション", "品質", "年"]) {
      await expect(page.getByRole("button", { name: new RegExp(`^${label}\\s*▾$`) })).toBeVisible();
    }

    // 品質 A を適用→ドロップダウントリガーが removable な FilterChip「品質: A」に置き換わる。
    await page.getByRole("button", { name: /^品質\s*▾$/ }).click();
    await page.getByRole("menu", { name: "品質" }).getByRole("menuitemradio", { name: /^A\s*\d*$/ }).click();
    await expect(page.getByText("品質: A", { exact: false })).toBeVisible();

    // 解除(FilterChip 内の「を解除」ボタン)で元のドロップダウンに戻る。
    // 外側の <button>(全体)も同名を含み一致するため exact で内側の「×」に絞る。
    await page.getByRole("button", { name: "品質: A を解除", exact: true }).click();
    await expect(page.getByRole("button", { name: /^品質\s*▾$/ })).toBeVisible();
  });

  test("「この条件を保存」→サイドバーに件数付きで表示", async ({ page }) => {
    await page.goto("/library?view=table");
    await page.getByRole("radio", { name: "テーブル", exact: true }).click();

    // 品質 A を適用(canSaveFilter=true の条件を満たす)。
    await page.getByRole("button", { name: /^品質\s*▾$/ }).click();
    await page.getByRole("menu", { name: "品質" }).getByRole("menuitemradio", { name: /^A\s*\d*$/ }).click();

    const saveButton = page.getByRole("button", { name: "この条件を保存" });
    await expect(saveButton).toBeEnabled();
    await saveButton.click();
    const filterName = `PW-04 保存フィルタ ${Date.now()}`;
    await page.getByRole("textbox", { name: "フィルタ名" }).fill(filterName);
    await page.getByRole("button", { name: "保存", exact: true }).click();
    await expect(page.getByText(`保存フィルタ「${filterName}」を作成しました`)).toBeVisible();

    const navLink = page.getByRole("navigation", { name: "サイドバー" }).getByRole("link", {
      name: new RegExp(filterName),
    });
    await expect(navLink).toBeVisible();

    try {
      // 適用: サイドバーのリンクから遷移すると、保存済み条件(filter_id)がサーバ側の
      // 一覧クエリに反映される(属性フィルタ UI 自体の chip 再構築は別範囲。決定・followups)。
      await navLink.click();
      await expect(page).toHaveURL(/filter_id=/);
      await expect(page.getByRole("heading", { name: "ライブラリ" })).toBeVisible();
    } finally {
      const listRes = await page.request.get("/api/saved-filters");
      const body = (await listRes.json()) as { items: { id: string; name: string }[] };
      const mine = body.items.find((f) => f.name === filterName);
      if (mine) {
        await page.request.delete(`/api/saved-filters/${mine.id}`, { headers: { Origin: ORIGIN } }).catch(() => undefined);
      }
    }
  });

  test("複数選択→一括操作バー: ステータス変更・タグ追加", async ({ page }) => {
    // 共有シード(他 spec の副作用に依存しない)データを汚さないため、自分専用の 2 件を作る。
    const a = await ingestArxiv(page, freshArxivUrl());
    const b = await ingestArxiv(page, freshArxivUrl());
    const idA = a.library_item_id;
    const idB = b.library_item_id;

    try {
      await page.goto("/library?view=table");
      await page.getByRole("radio", { name: "テーブル", exact: true }).click();
      // 一覧取得完了を待つ(共有シードが大きい場合の読み込みに時間がかかることがある)。
      await expect(page.getByText("読み込み中…")).toHaveCount(0, { timeout: 30_000 });
      // 「追加日」列を降順ソート(2 クリックで昇順→降順)にして、自分が今作った 2 件を先頭にする。
      const addedAtHeader = page.getByRole("button", { name: "追加日" });
      await addedAtHeader.click();
      await addedAtHeader.click();

      // 行は role="row"(header が index 0。CSS grid ベースで <table> 要素は使わない)。
      const rows = page.getByRole("row");
      await expect(rows.nth(2)).toBeVisible();
      await rows.nth(1).getByRole("checkbox").check();
      await rows.nth(2).getByRole("checkbox").check();

      const bar = page.getByRole("toolbar", { name: "一括操作" });
      await expect(bar).toBeVisible();
      await expect(bar.getByText("2 件を選択中")).toBeVisible();

      // 1) ステータス変更。
      await bar.getByRole("button", { name: /ステータス変更/ }).click();
      await bar.getByRole("menu", { name: "ステータス変更" }).getByRole("menuitem", { name: "すぐ読む" }).click();
      await expect
        .poll(async () => (await (await page.request.get(`/api/library-items/${idA}`)).json()).status)
        .toBe("up_next");

      // 選択は連続操作のため維持される(LibraryTableView.tsx の決定)→続けてタグ追加。
      await expect(bar).toBeVisible();
      await bar.getByRole("button", { name: "タグ追加" }).click();
      await bar.getByRole("textbox", { name: "タグを追加" }).fill("pw04-e2e");
      await bar.getByRole("textbox", { name: "タグを追加" }).press("Enter");
      // トリガー「タグ追加」ボタンも部分一致するため exact で確定ボタンに絞る。
      await bar.getByRole("button", { name: "追加", exact: true }).click();

      // 反映確認(API)。
      await expect
        .poll(async () => {
          const res = await page.request.get(`/api/library-items/${idA}`);
          const body = (await res.json()) as { status: string; tags: string[] };
          return body.status === "up_next" && body.tags.includes("pw04-e2e");
        })
        .toBe(true);
    } finally {
      await page.request.delete(`/api/library-items/${idA}`, { headers: { Origin: ORIGIN } }).catch(() => undefined);
      await page.request.delete(`/api/library-items/${idB}`, { headers: { Origin: ORIGIN } }).catch(() => undefined);
    }
  });
});
