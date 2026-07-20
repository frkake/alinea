/* eslint-disable no-restricted-imports -- E2E 補助モジュールは親ディレクトリから import する(src の @/ エイリアス規約は外) */
import { expect, test } from "@playwright/test";
import { resolveRfItemId } from "../fixtures/api";
import { openViewer } from "../fixtures/viewer";

/**
 * PW-PRESENTATION(Task 30): 論文→スライド生成ツールの Web 導線。
 * デスクトップ「✦ ツール」→ 3 用途プリセットで生成開始 → SSE 進捗 → ダウンロード →
 * 再生成失敗時の旧成果物保持、およびモバイル非表示を検証する。
 *
 * === 実行の延期(Task 32 へ) ==================================================
 * 本 spec は `test.describe.skip` で登録し、実行は Task 32(E2E 実行レーン)へ延期する。
 * 理由:
 *  1. 生成 job(kind='presentation')は Task 29 の PresentationRunner が `vendor/ppt-master`
 *     submodule の専用仮想環境を実際に起動して SVG→PPTX 変換を行う。E2E 環境ではこの
 *     submodule 初期化・上流依存の同期(`pnpm ppt-master:update` 相当)が前提になるため、
 *     seed(§14 rectified-flow)だけでは成功まで走らない。
 *  2. 失敗経路(再生成失敗で旧成果物を残す)を決定的に起こすには、worker 側で
 *     provider_error / validating 失敗を注入するテスト用フックが要る(Task 32 で用意)。
 *  3. 実 LLM ルート(presentation)+ 鍵の可用性が E2E ハーネスで安定して揃う保証が
 *     まだ無い(FakeLLM でも構成 JSON→SVG 群の生成に時間がかかる)。
 *
 * したがって steps は本番導線どおりに書いておき(Task 32 で `.skip` を外すだけで通る形)、
 * ユニット(PresentationDialog/PresentationProgress/SettingsClient/ViewerShell.mobile)で
 * ロジック・分岐・モバイル非表示・失敗時の旧成果物保持を先行して担保する。
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
