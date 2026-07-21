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
 * bbox 選択→「≒ … — 訳文で見る →」の同期チップは、PDF ページ層(`.alinea-pdf-page-layer`)を
 * クリックして bbox→ブロックのヒットテストを起こす実操作で検証する(Task 32 で実操作化)。
 * 合成 PDF では節番号(§2.2 ¶2)の文言までは再現できないため、チップの表示文言は
 * `/訳文で見る/` で判定し、遷移先が訳文モード + block パラメータになることを assertion する。
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

  test("bbox 選択→同期チップ「訳文で見る →」で対応ブロックの訳文へ", async ({ page }) => {
    // 都度取り込んだ PDF アイテムで PDF モードを開く(§14 シードは PDF を持たない)。
    const pdfPath = join(__dirname, "..", "fixtures", "sample.pdf");
    const nonce = `\n% e2e-pw12b-${Date.now()}-${Math.random().toString(36).slice(2, 8)}\n`;
    const pdfBytes = Buffer.concat([readFileSync(pdfPath), Buffer.from(nonce)]);
    const res = await page.request.post("/api/ingest/pdf", {
      multipart: {
        file: { name: "sample.pdf", mimeType: "application/pdf", buffer: pdfBytes },
        meta: JSON.stringify({ status: "planned", title_guess: "PW-12 Sync PDF" }),
      },
      headers: {
        Origin: "http://localhost:3000",
        "Idempotency-Key": `e2e-pw12b-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
      },
    });
    expect(res.status(), await res.text()).toBe(202);
    const { job_id, library_item_id } = (await res.json()) as {
      job_id: string;
      library_item_id: string;
    };
    expect((await waitForJob(page, job_id)).status).toBe("succeeded");

    await openViewer(page, library_item_id, "pdf");
    await expect(page.getByRole("radio", { name: "PDF", exact: true })).toBeChecked();

    // PDF ページ層をクリックして bbox→ブロックのヒットテスト(同期選択)を起こす。
    const pageLayer = page.locator(".alinea-pdf-page-layer").first();
    await expect(pageLayer).toBeVisible();
    const box = await pageLayer.boundingBox();
    if (!box) throw new Error("PDF page layer has no bounding box");
    // 本文が載る上部 1/3 付近を単クリック(ドラッグ >4px はテキスト選択扱いになるため単発)。
    // sample.pdf は本文ブロックが上部 ~7-16% にしかない(合成フィクスチャ)。
    // 0.28 だと唯一のブロックより下=空白に当たり blockAtPoint が null になるため、
    // ブロック中心(≒ページ高 11%)を狙う。
    await page.mouse.click(box.x + box.width * 0.5, box.y + box.height * 0.12);

    // 同期ハイライト + チップ(≒ … — 訳文で見る →)が現れる。
    const highlight = page.getByTestId("pdf-bbox-highlight");
    await expect(highlight).toBeVisible();
    const chip = page.getByRole("button", { name: /訳文で見る/ });
    await expect(chip).toBeVisible();

    // チップで対応ブロックの訳文へ遷移する(mode=translation + block パラメータ)。
    await chip.click();
    await expect(page).toHaveURL(/mode=translation/);
    await expect(page).toHaveURL(/block=/);
  });
});
