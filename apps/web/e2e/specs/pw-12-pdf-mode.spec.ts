/* eslint-disable no-restricted-imports -- E2E 補助モジュールは親ディレクトリから import する(src の @/ エイリアス規約は外) */
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { expect, test } from "@playwright/test";
import { waitForJob } from "../fixtures/api";
import { openViewer } from "../fixtures/viewer";

const __dirname = dirname(fileURLToPath(import.meta.url));

/**
 * PW-12(plans/12 §4.3): PDF モード。
 *
 * §14 シード(Rectified Flow)は arxiv_html 由来(quality A)で PDF の SourceAsset を持たない
 * ため PDF モードのラジオが disabled になる(usePdfAvailability が 404 を検出)。本 spec は
 * M1-18(`POST /api/ingest/pdf`)で quality B の PDF アイテムを都度取り込んでから検証する
 * (`../fixtures/sample.pdf`: 2 ページの合成 PDF)。
 *
 * bbox 選択→「≒ §2.2 ¶2 — 訳文で見る →」は実文書構造(pdfplumber の bbox→段落マッピング)
 * に依存し、合成 PDF では意味のある節番号を再現できないため test.fixme(PW-05 の図表参照
 * ポップと同じ「コンテンツ依存」理由。plans/13 §1.5 の運用規則に倣う)。
 */
test.describe("PW-12 PDFモード", () => {
  test("同期表示・ページ送り・ズーム・見開き・訳文で開く", async ({ page }) => {
    const pdfPath = join(__dirname, "..", "fixtures", "sample.pdf");
    // pdf_sha256 の一意制約(PY-ING-04)に決定的に引っかからないよう、%%EOF 後(パーサが無視
    // する範囲。pymupdf/pdfplumber で確認済み)にランダムなコメント行を追記して実行ごとに
    // 内容(=sha256)を変える。
    const nonce = `\n% e2e-pw12-${Date.now()}-${Math.random().toString(36).slice(2, 8)}\n`;
    const pdfBytes = Buffer.concat([readFileSync(pdfPath), Buffer.from(nonce)]);

    const res = await page.request.post("/api/ingest/pdf", {
      multipart: {
        file: {
          name: "sample.pdf",
          mimeType: "application/pdf",
          buffer: pdfBytes,
        },
        meta: JSON.stringify({ status: "planned", title_guess: "PW-12 Synthetic PDF" }),
      },
      headers: {
        Origin: "http://localhost:3000",
        "Idempotency-Key": `e2e-pw12-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
      },
    });
    expect(res.status(), await res.text()).toBe(202);
    const { job_id, library_item_id } = (await res.json()) as {
      job_id: string;
      library_item_id: string;
    };
    const final = await waitForJob(page, job_id);
    expect(final.status).toBe("succeeded");

    await openViewer(page, library_item_id, "pdf");

    const pdfRadio = page.getByRole("radio", { name: "PDF", exact: true });
    await expect(pdfRadio).toBeEnabled();
    await expect(pdfRadio).toBeChecked();

    // ツールバー: ページ送り・ページ番号・ズーム・見開き・同期表示・訳文で開く。
    await expect(page.getByRole("textbox", { name: "ページ番号" })).toHaveValue("1");
    await expect(page.getByText(/^\/\s*2$/)).toBeVisible();

    await page.getByRole("button", { name: "次のページ" }).click();
    await expect(page).toHaveURL(/page=2/);
    await expect(page.getByRole("textbox", { name: "ページ番号" })).toHaveValue("2");

    await page.getByRole("button", { name: "前のページ" }).click();
    await expect(page.getByRole("textbox", { name: "ページ番号" })).toHaveValue("1");

    const zoomBefore = await page.getByText(/^\d+%$/).textContent();
    await page.getByRole("button", { name: "拡大" }).click();
    await expect(page.getByText(/^\d+%$/)).not.toHaveText(zoomBefore ?? "");

    await page.getByRole("button", { name: "見開き" }).click();
    await expect(page.getByRole("button", { name: "見開き" })).toHaveAttribute("aria-pressed", "true");
    await page.getByRole("button", { name: "見開き" }).click();

    await expect(page.getByText("同期:")).toBeVisible();

    await page.getByRole("button", { name: /訳文で開く/ }).click();
    await expect(page).toHaveURL(/mode=translation/);
  });

  test.fixme(
    "bbox 選択→「≒ §2.2 ¶2 — 訳文で見る →」で対応段落へ(合成 PDF では意味のある節構造を" +
      "再現できないためコンテンツ依存。plans/13 §1.5 と同じ運用)",
    async () => {},
  );
});
