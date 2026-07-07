/* eslint-disable no-restricted-imports -- E2E 補助モジュールは親ディレクトリから import する(src の @/ エイリアス規約は適用外) */
import { expect, test } from "@playwright/test";
import { freshArxivUrl, getJob, ingestArxiv, waitForJob } from "../fixtures/api";

/**
 * PW-03(plans/12 §4.3): 取り込み進行の可視化。拡張と同じ ingest API を直呼びして開始し、
 * パイプラインが queued→…→complete と進むこと、完了後にライブラリカードとして現れ、
 * カードから部分読書ビューアへ到達できることを検証する(readable-first)。
 *
 * 決定: モックサーバ + FakeLLM の取り込みは数秒で完了するため、UI 上での「本文翻訳中 n%」の
 * 中間状態はレースになりやすい。進捗の可視化はジョブの stage 遷移(API)で、カード表示→
 * ビューア到達は UI で検証する。中間状態の SSE 差し替え(未翻訳→翻訳済み)は PY-JOB-02 /
 * chat-stream 系ユニットの担当。
 */
test.describe("PW-03 取り込み進行の可視化", () => {
  test("ingest API 開始 → 進捗遷移 → カード表示 → ビューア到達", async ({ page }) => {
    // ライブラリを先に開いておく(認証済みコンテキスト)。
    await page.goto("/library");
    await expect(page.getByRole("heading", { name: "ライブラリ" })).toBeVisible();

    const url = freshArxivUrl();
    const { job_id, library_item_id } = await ingestArxiv(page, url);
    expect(job_id).toBeTruthy();
    expect(library_item_id).toBeTruthy();

    // 進捗遷移(データ層): queued 起点 → 進捗率が上がり → complete/succeeded に到達。
    const stages = new Set<string>();
    let maxPct = 0;
    const deadline = Date.now() + 45_000;
    let final = await getJob(page, job_id);
    while (Date.now() < deadline) {
      final = await getJob(page, job_id);
      stages.add(final.stage);
      maxPct = Math.max(maxPct, final.progress_pct);
      if (final.status === "succeeded" || final.status === "failed") break;
      await new Promise((r) => setTimeout(r, 500));
    }
    expect(final.status, `stages=${[...stages].join(",")}`).toBe("succeeded");
    expect(final.stage).toBe("complete");
    expect(maxPct).toBe(100);

    // カード表示 → クリックでビューア到達。
    await page.goto("/library");
    await page.getByRole("radio", { name: "カード", exact: true }).click();
    const card = page.getByRole("link", { name: `Mock Paper for ${url.split("/abs/")[1]}` });
    await expect(card).toBeVisible({ timeout: 15_000 });
    await card.click();

    await expect(page).toHaveURL(new RegExp(`/papers/${library_item_id}`));
    await expect(page.getByRole("radiogroup", { name: "表示モード" })).toBeVisible();
  });

  test("完了ジョブは冪等(waitForJob が succeeded を返す)", async ({ page }) => {
    const url = freshArxivUrl();
    const { job_id } = await ingestArxiv(page, url);
    const final = await waitForJob(page, job_id);
    expect(final.status).toBe("succeeded");
  });
});
