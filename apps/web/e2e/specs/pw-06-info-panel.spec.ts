/* eslint-disable no-restricted-imports -- E2E 補助モジュールは親ディレクトリから import する(src の @/ エイリアス規約は適用外) */
import { expect, test } from "@playwright/test";
import { freshArxivUrl, ingestArxiv, waitForJob } from "../fixtures/api";
import { openViewer } from "../fixtures/viewer";

/**
 * PW-06(plans/12 §4.3): 情報パネル。
 * 品質 A 定義文言(逐語)・取り込みタイムライン 3 段(タイムスタンプ付き)・処理ログ・
 * 「再取り込み」実行・ライセンスカード「CC BY 4.0 — 図表転載可」を検証する。
 *
 * §14 シード(Rectified Flow)は seed.py が直接 DB へ投入するため ingest_job を持たず、
 * タイムラインが空になる(joblog.build_timeline([]))。3 段タイムラインを見るには実パイプライン
 * (fetching → structuring → translating_body の 3 timeline=true ログ。plans/12 §14/PY-ING-02)を
 * 通した論文が必要なため、PW-03 と同じく ingest API を直呼びして新規取り込みしたアイテムを使う。
 */
test.describe("PW-06 情報パネル", () => {
  test("品質A定義・3段タイムライン・処理ログ・再取り込み・ライセンスカード", async ({ page }) => {
    const url = freshArxivUrl();
    const { job_id, library_item_id } = await ingestArxiv(page, url);
    const final = await waitForJob(page, job_id);
    expect(final.status).toBe("succeeded");

    await openViewer(page, library_item_id, "translation");
    await page.getByRole("tab", { name: "情報" }).click();

    // 品質 A 定義文言(逐語。InfoPanel.tsx QUALITY_DESCRIPTION)。
    await expect(
      page.getByText("LaTeX ソースから完全構造化。数式・相互参照・図表・脚注を保持しています。"),
    ).toBeVisible();

    // 取り込みタイムライン 3 段(ソース取得→構造化・図表抽出→全文翻訳)。各行はタイムスタンプ
    // 接頭(M/DD HH:mm or HH:mm)+ label(joblog.*_timeline_message の逐語)。
    await expect(page.getByText("品質レベルと取り込み", { exact: true })).toBeVisible();
    // .first(): リトライ・チェックポイント再開等で同一ステージのログ行が複数になることが
    // あるため(いずれのケースでも該当ステージが最低 1 行表示されることを確認すれば十分)。
    // M2-01 以降の主経路は LaTeX(取得優先 LaTeX > HTML > PDF)。モックサーバは e-print を
    // 常に返すため決定的に LaTeX 経路になる(joblog.fetch_timeline_message の逐語)。
    await expect(page.getByText(/— arXiv から LaTeX ソース取得/).first()).toBeVisible();
    await expect(page.getByText(/— 構造化・図表抽出/).first()).toBeVisible();
    await expect(page.getByText(/— 全文翻訳 完了/).first()).toBeVisible();

    // 処理ログ(Modal は明示クローズボタンを持たず Escape で閉じる。plans/08 §5.11)。
    await page.getByRole("button", { name: "処理ログ" }).click();
    const logDialog = page.getByRole("dialog", { name: "処理ログ" });
    await expect(logDialog).toBeVisible();
    await expect(logDialog.getByText(/構造化・図表抽出|arXiv から|全文翻訳/).first()).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(logDialog).toBeHidden();

    // ライセンスカード(モック arXiv Atom フィードは CC BY 4.0)。
    await expect(page.getByText("CC BY 4.0 — 図表転載可")).toBeVisible();

    // 「再取り込み」実行 → 確認モーダル → 202 受領でモーダルが閉じる。
    // 決定: 完了トースト(EventSource `/api/jobs/{id}/events`)は、ingest 系ジョブの進捗が
    // どこからも Redis pub/sub へ publish されないため(§8.4 のモック環境に限らず本番導線でも
    // 未配線。followups 参照)現状発火しない。ここではバックエンドの完了自体をジョブ API の
    // ポーリングで検証する(PW-03 と同じ方式)。
    await page.getByRole("button", { name: "再取り込み" }).click();
    const confirmDialog = page.getByRole("dialog", { name: "再取り込みしますか?" });
    await expect(confirmDialog).toBeVisible();
    const [reingestResponse] = await Promise.all([
      page.waitForResponse((r) => /\/api\/papers\/.+\/reingest$/.test(r.url()) && r.status() === 202),
      confirmDialog.getByRole("button", { name: "再取り込み", exact: true }).click(),
    ]);
    await expect(confirmDialog).toBeHidden();
    const { job_id: reingestJobId } = (await reingestResponse.json()) as { job_id: string };
    const reingestFinal = await waitForJob(page, reingestJobId);
    expect(reingestFinal.status).toBe("succeeded");
  });
});
