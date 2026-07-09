/* eslint-disable no-restricted-imports -- E2E 補助モジュールは親ディレクトリから import する(src の @/ エイリアス規約は外) */
import { spawnSync } from "node:child_process";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { expect, test } from "@playwright/test";
import { ORIGIN, resolveRfItemId, waitForJob } from "../fixtures/api";
import { openViewer } from "../fixtures/viewer";

const __dirname = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(__dirname, "..", "..", "..", "..");
const NO_PROXY = "localhost,127.0.0.1";

interface PromotionSeed {
  paper_id: string;
  library_item_id: string;
  notification_id: string;
  old_revision_id: string;
  arxiv_id: string;
  annotation_moved_id: string;
  annotation_lost_id: string;
}

/**
 * PW-11 の昇格提案の前提データを作る(worker cron `check_quality_promotions` は毎日
 * 07:30 JST のため E2E で待つのは非現実的。cron が行う通知 INSERT だけを本番と同一関数
 * (`fire_status_suggestion`)で直接発火する。それ以外は全て実配線)。
 */
function seedPromotion(userEmail: string): PromotionSeed {
  const result = spawnSync(
    "uv",
    ["run", "--no-sync", "python", "apps/web/e2e/scripts/seed_promotion.py", userEmail],
    { cwd: repoRoot, env: { ...process.env, NO_PROXY, no_proxy: NO_PROXY }, encoding: "utf-8" },
  );
  if (result.status !== 0) {
    throw new Error(`seed_promotion.py failed: ${result.stderr || result.stdout}`);
  }
  return JSON.parse(result.stdout.trim()) as PromotionSeed;
}

/**
 * PW-11(plans/12 §4.3): リビジョン昇格 + 参考文献展開。
 *
 * 「参考文献展開→「+ この論文も取り込む」/「ライブラリに有り ✓」」は §14 シードの参考文献 1
 * (論文自身。ライブラリに有り)+参考文献 2(arXiv 2006.11239。M1-24 追補で url を付与し
 * 取り込み可能にした)で検証する。
 *
 * 「B 論文に昇格提案通知→「変更する」→新リビジョン適用」は発見した実装ギャップ
 * (routers/notifications.py の promote_revision + apply が resolved 消化のみで adopt-revision
 * 相当の処理を呼んでいなかった)を M1-07/M1-22 followup として接続し、fixme を解除した。
 * 新リビジョン切替+リアンカー自体(`POST /api/library-items/{id}/adopt-revision`)の細部
 * (block_id 引き継ぎ・quote 探索・未配置)は PY-ANN-02 相当(`test_adopt_revision.py`)が
 * pytest で担保している。本テストは「通知→変更する→実 worker が reingest→adopt-revision と
 * 同一処理を自動実行」までの配線を実 worker + モック arXiv サーバ経由で検証する。
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

  test("B 論文に昇格提案通知→「変更する」→新リビジョン適用・注釈追従・未配置", async ({ page }) => {
    const seed = seedPromotion("dev@alinea.test");

    try {
      await page.goto("/dashboard");
      await page.getByRole("button", { name: "通知" }).click();

      const suggestionText = page.getByText(/の高品質版.*が利用可能です/).first();
      await expect(suggestionText).toBeVisible();
      const row = suggestionText.locator("..");

      const [actionResponse] = await Promise.all([
        page.waitForResponse(
          (r) => /\/api\/notifications\/.+\/action$/.test(r.url()) && r.status() === 200,
        ),
        row.getByRole("button", { name: "変更する" }).click(),
      ]);
      await expect(row.getByText(/✓ 「適用」に変更しました/)).toBeVisible();

      const { job_id: jobId } = (await actionResponse.json()) as { job_id: string | null };
      expect(jobId).not.toBeNull();
      const finalState = await waitForJob(page, String(jobId));
      expect(finalState.status).toBe("succeeded");

      // adopt-revision と同一の内部処理(papers.latest_revision_id 切替)が自動実行された。
      await expect
        .poll(async () => {
          const r = await page.request.get(`/api/library-items/${seed.library_item_id}`);
          const body = (await r.json()) as { quality_level: string };
          return body.quality_level;
        })
        .toBe("A");

      // リアンカー(§4.5 パス 2: quote 探索)。追従した注釈は新リビジョンを指す。
      const annRes = await page.request.get(
        `/api/library-items/${seed.library_item_id}/annotations`,
      );
      expect(annRes.status(), await annRes.text()).toBe(200);
      const annBody = (await annRes.json()) as {
        items: { id: string; placed: boolean; anchor: { revision_id: string } }[];
      };
      const moved = annBody.items.find((a) => a.id === seed.annotation_moved_id);
      expect(moved?.placed).toBe(true); // quote 探索でリアンカーに追従(§4.5 パス 2)
      expect(moved?.anchor.revision_id).not.toBe(seed.old_revision_id);
      const lost = annBody.items.find((a) => a.id === seed.annotation_lost_id);
      expect(lost?.placed).toBe(false); // 未配置(消えない。P3)
    } finally {
      // 後片付け(自分が作った E2E 専用の論文/ライブラリ項目のみ削除)。
      await page.request
        .delete(`/api/library-items/${seed.library_item_id}`, { headers: { Origin: ORIGIN } })
        .catch(() => undefined);
    }
  });
});
