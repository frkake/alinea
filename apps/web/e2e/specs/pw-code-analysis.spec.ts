/* eslint-disable no-restricted-imports -- E2E 補助モジュールは親ディレクトリから import する(src の @/ エイリアス規約は外) */
import { expect, test } from "@playwright/test";
import { resolveRfItemId } from "../fixtures/api";
import { openViewer } from "../fixtures/viewer";

/**
 * PW-Code-Analysis(Task 22・設計 §12): GitHub コード対応解析の設定と結果 UI。
 *
 * 実行方針(Task 22 の合意):
 *   本スペックは「実ステップ」を書き切るが、実行は Task 32 まで **意図的に遅延** する。
 *   理由は、コード対応解析の end-to-end 実行に、
 *     1. フルスタック(api + worker + Redis + Postgres)、
 *     2. GitHub archive を実ネットワーク無しで供給する **保存 tar fixture**、
 *     3. **Fake Embedding / Fake LLM provider**(§14「実通信は行わない」)
 *   が揃った検証基盤が要るため。これらは Task 32 の E2E 基盤タスクで用意する。
 *   §8.4 のモックサーバは現状 github.com/埋め込み/LLM への接続先切替を持たないので、
 *   ここで通すと実 github.com・実 LLM を叩いてしまい、決定性・費用・秘匿の前提を壊す。
 *
 * 基盤が入ったら下の `DEFER_UNTIL_TASK_32` を false にする(fixtures/api.ts に
 * seedCodeAnalysisFixture 等が追加される想定)。ステップ自体は完成形で、有効化後に
 * そのまま流れる。
 */
const DEFER_UNTIL_TASK_32 = true;

test.describe("PW-Code-Analysis GitHub コード対応解析", () => {
  test.skip(
    DEFER_UNTIL_TASK_32,
    "コード解析の E2E は保存 tar fixture + Fake provider を要する(Task 32 で有効化)",
  );

  test("設定の三モード切替と月額予算・当月費用の表示", async ({ page }) => {
    await page.goto("/settings?category=account");

    // GitHub コード対応解析セクションの三モード。
    await expect(page.getByRole("radiogroup", { name: "解析モード" })).toBeVisible();
    await page.getByRole("radio", { name: /取り込み後に自動/ }).click();
    // automatic の説明(対象範囲 + 根拠の弱い候補は対象外 + まとめて実行しない)。
    await expect(page.getByText(/根拠の弱い/)).toBeVisible();

    await page.getByRole("radio", { name: /使用しない/ }).click();

    // 月額予算のステッパー($ 表記・0.50 刻み)。
    await expect(page.getByRole("status", { name: "月額予算" })).toContainText("$");
    await page.getByRole("button", { name: "月額予算を増やす" }).click();
  });

  test("on_demand: 見積もり確認 → 開始 → 完了 → 結果 → 論文/GitHubアンカー", async ({ page }) => {
    const itemId = await resolveRfItemId(page);

    // 前提: on_demand モード(既定)。
    await page.goto("/settings?category=account");
    await page.getByRole("radio", { name: /必要なときだけ/ }).click();

    await openViewer(page, itemId, "translation");
    await page.getByRole("tab", { name: "リソース" }).click();

    // GitHub カードの「コード対応を解析」→ 見積もり確認モーダル。
    await page.getByRole("button", { name: "コード対応を解析" }).first().click();
    const estimateDialog = page.getByRole("dialog", { name: /コード対応を解析/ });
    await expect(estimateDialog).toBeVisible();
    // 対象 commit・ファイル数・token・概算費用・予算残額が出る(設計 §7)。
    await expect(estimateDialog.getByText("対象 commit")).toBeVisible();
    await expect(estimateDialog.getByText("概算費用")).toBeVisible();
    await expect(estimateDialog.getByText("当月予算の残額")).toBeVisible();

    // 開始 → 202 → queued/running を経て succeeded。
    await estimateDialog.getByRole("button", { name: "解析を開始" }).click();
    await expect(estimateDialog).toBeHidden();

    // 完了で「対応 N 件」+「結果を見る」。
    await expect(page.getByText(/対応 \d+ 件/)).toBeVisible({ timeout: 60_000 });
    await page.getByRole("button", { name: /結果を見る/ }).click();

    const resultDialog = page.getByRole("dialog", { name: /コード対応の結果/ });
    await expect(resultDialog).toBeVisible();

    // GitHub アンカーは固定 commit + #Lx-Ly を新規タブで開く。
    const codeLink = resultDialog.getByRole("link").first();
    await expect(codeLink).toHaveAttribute("target", "_blank");
    await expect(codeLink).toHaveAttribute("href", /\/blob\/[0-9a-f]{7,40}\/.+#L\d+/);

    // 論文アンカーは本文ブロックへスクロール(新規タブではない)。
    await resultDialog.getByRole("button", { name: /論文の該当箇所/ }).first().click();
    await expect(resultDialog).toBeHidden();
  });

  test("対応0件は『対応箇所を特定できませんでした』(『コードが無い』ではない)", async ({ page }) => {
    // Fake provider が対応 0 を返す固定 fixture を使う(Task 32)。
    const itemId = await resolveRfItemId(page);
    await openViewer(page, itemId, "translation");
    await page.getByRole("tab", { name: "リソース" }).click();
    await page.getByRole("button", { name: /結果を見る/ }).first().click();
    const resultDialog = page.getByRole("dialog", { name: /コード対応の結果/ });
    await expect(resultDialog.getByText("対応箇所を特定できませんでした")).toBeVisible();
    await expect(resultDialog.getByText(/コードが無い/)).toHaveCount(0);
  });

  test("off モードでも既存結果は閲覧でき、新規解析ボタンは無効", async ({ page }) => {
    await page.goto("/settings?category=account");
    await page.getByRole("radio", { name: /使用しない/ }).click();

    const itemId = await resolveRfItemId(page);
    await openViewer(page, itemId, "translation");
    await page.getByRole("tab", { name: "リソース" }).click();

    // 新規解析は無効・設定リンクを出す。既存結果があれば「結果を見る」は生きている。
    await expect(page.getByRole("button", { name: "コード対応を解析" }).first()).toBeDisabled();
    await expect(page.getByText(/設定でコード解析が無効です/)).toBeVisible();
  });
});
