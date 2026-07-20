/* eslint-disable no-restricted-imports -- E2E 補助モジュールは親ディレクトリから import する(src の @/ エイリアス規約は外) */
import { expect, test } from "@playwright/test";
import { resolveRfItemId } from "../fixtures/api";
import { openViewer } from "../fixtures/viewer";

/**
 * PW-PRESENTATION(Task 30): 論文→スライド生成ツールの Web 導線。
 * デスクトップ「✦ ツール」→ 3 用途プリセットで生成開始 → SSE 進捗 → ダウンロード →
 * 再生成失敗時の旧成果物保持、およびモバイル非表示を検証する。
 *
 * === 実行環境ゲート(Task 32 で確認済み) ======================================
 * 本 spec は成功経路が `vendor/ppt-master` submodule + 専用仮想環境 `.venv-ppt-master` に
 * 依存するため、それらが用意されたリリース同等環境でのみ実行できる(既定の CI/開発
 * 環境では submodule 未初期化のため PresentationRunner は exporting 段で決定的に失敗する)。
 * したがって本 describe は `describe.skip` のまま残す(assertion は削除しない)。
 *
 * 実行を有効化する前提(release-env-gated):
 *  1. `git submodule update --init vendor/ppt-master`(承認済み commit 0c0bdaf)+
 *     `.venv-ppt-master`(python-pptx/lxml/pillow)を用意し、worker ctx に
 *     `ppt_master_adapter` を注入する。
 *  2. 再生成失敗経路(旧成果物保持)は worker 側の失敗注入フックが必要(未実装。followups)。
 *  3. LLM は E2E の FakeLLM(ALINEA_FAKE_LLM=1)で決定的に構成 JSON→SVG を返す。
 *
 * DOM 導線(下記 steps)は現行実装の accessible name に合わせてある(Task 32 で再検証)。
 * ロジック・分岐・モバイル非表示・失敗時の旧成果物保持はユニット
 * (PresentationDialog/PresentationProgress/SettingsClient/ViewerShell.mobile)で先行担保する。
 * ============================================================================
 */
test.describe.skip("PW-PRESENTATION スライド生成(実行は Task 32 へ延期)", () => {
  test("デスクトップ: 3 用途で開始→SSE 進捗→ダウンロード→再生成失敗で旧成果物保持", async ({
    page,
  }) => {
    // 生成 job(素材準備→構成→SVG 群→検証→PPTX 化→アップロード)は長い。
    test.setTimeout(180_000);
    const itemId = await resolveRfItemId(page);
    await openViewer(page, itemId, "translation");

    // 1) デスクトップ専用「✦ ツール」→「論文からスライドを生成」でダイアログを開く。
    await page.getByRole("button", { name: /ツール/ }).click();
    await page.getByRole("menuitem", { name: /論文からスライドを生成/ }).click();
    const dialog = page.getByRole("dialog", { name: /スライドを生成/ });
    await expect(dialog).toBeVisible();

    // 2) 3 用途プリセット + 聴衆 + 任意指示(≤500)を確認する。
    await expect(dialog.getByRole("radio", { name: "輪読会" })).toBeVisible();
    await expect(dialog.getByRole("radio", { name: "研究発表" })).toBeVisible();
    await expect(dialog.getByRole("radio", { name: "実装解説" })).toBeVisible();
    await expect(dialog.getByText(/0\s*\/\s*500/)).toBeVisible();

    // 用途を切り替えると聴衆の既定値が追従する(研究発表→研究者)。
    await dialog.getByRole("radio", { name: "研究発表" }).click();
    await expect(dialog.getByRole("radio", { name: "研究者" })).toHaveAttribute(
      "aria-checked",
      "true",
    );

    // 3) 生成開始 → SSE 進捗(日本語 stage)。
    await dialog.getByRole("button", { name: /生成する/ }).click();
    await expect(dialog.getByText(/スライドを生成しています|スライド構成を考えています/)).toBeVisible();

    // 4) 成功 → ダウンロード(PPTX)。
    await expect(dialog.getByRole("button", { name: /ダウンロード/ })).toBeVisible({
      timeout: 150_000,
    });
    await expect(dialog.getByText(/研究発表/)).toBeVisible();
    const [download] = await Promise.all([
      page.waitForEvent("download"),
      dialog.getByRole("button", { name: /ダウンロード/ }).click(),
    ]);
    expect(download.suggestedFilename()).toMatch(/\.pptx$/);

    // 5) 再生成が失敗しても、旧成果物のダウンロードが残る(失敗表示と同時に併存)。
    //    Task 32 では worker 側の失敗注入フックで validating/exporting を失敗させる。
    await dialog.getByRole("button", { name: /再生成/ }).click();
    await dialog.getByRole("button", { name: /生成する/ }).click();
    await expect(dialog.getByText(/失敗|エラー/)).toBeVisible({ timeout: 150_000 });
    // 失敗表示中も旧 PPTX はダウンロードできる。
    await expect(dialog.getByRole("button", { name: /ダウンロード/ })).toBeVisible();
    await expect(dialog.getByRole("button", { name: /再試行/ })).toBeVisible();
  });

  test("モバイル: 生成導線(✦ ツール)を表示しない", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    const itemId = await resolveRfItemId(page);
    await page.goto(`/papers/${itemId}`);
    await expect(page.getByRole("button", { name: "戻る" })).toBeVisible();
    // モバイル縮退ヘッダには「✦ ツール」を描画しない(設計 Non-Goal「モバイルからの生成」)。
    await expect(page.getByRole("button", { name: /ツール/ })).toHaveCount(0);
    await expect(page.getByText(/論文からスライドを生成/)).toHaveCount(0);
  });
});
