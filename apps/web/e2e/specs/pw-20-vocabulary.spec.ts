/* eslint-disable no-restricted-imports -- E2E 補助モジュールは親ディレクトリから import する(src の @/ エイリアス規約は外) */
import { spawnSync } from "node:child_process";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { expect, test } from "@playwright/test";
import { ORIGIN, resolveRfItemId } from "../fixtures/api";
import { dragSelect, openViewer } from "../fixtures/viewer";

const __dirname = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(__dirname, "..", "..", "..", "..");
const NO_PROXY = "localhost,127.0.0.1";

interface DueVocabSeed {
  vocab_id: string;
  term: string;
}

/**
 * §14 シード(vocab.json)の "trajectory"/"coupling" は当初 due だが、このテスト(または他の
 * 実行)が一度「復習をはじめる」を評価すると SRS が進み次回以降 due でなくなる。2 連続実行
 * のいずれでも「復習をはじめる」が活性であることを保証するため、生成完了済み・today 期限の
 * VocabEntry を直接 INSERT する(seed_due_vocab.py。pw-11 の seed_promotion.py と同方針)。
 */
function seedDueVocab(userEmail: string): DueVocabSeed {
  const result = spawnSync(
    "uv",
    ["run", "--no-sync", "python", "apps/web/e2e/scripts/seed_due_vocab.py", userEmail],
    { cwd: repoRoot, env: { ...process.env, NO_PROXY, no_proxy: NO_PROXY }, encoding: "utf-8" },
  );
  if (result.status !== 0) {
    throw new Error(`seed_due_vocab.py failed: ${result.stderr || result.stdout}`);
  }
  return JSON.parse(result.stdout.trim()) as DueVocabSeed;
}

/**
 * PW-20(plans/12 §4.3・M2-17): 語彙帳。
 * ビューアで選択→「語彙に追加」(TranslationPane への配線は本レーンの followup。
 * vocab-context.ts 参照)→ 4d の AI 生成 6 セクション→編集→「原文で見る →」、および
 * 「復習をはじめる」→ 2 択評価→「次の復習」更新を検証する。
 */
test.describe("PW-20 語彙帳", () => {
  test("選択→語彙に追加→AI生成6セクション→編集→原文で見る", async ({ page }) => {
    const itemId = await resolveRfItemId(page);
    await openViewer(page, itemId, "translation");
    const dismiss = page.getByRole("button", { name: "閉じる" });
    if (await dismiss.isVisible().catch(() => false)) await dismiss.click();

    const para = page.locator(".yk-paragraph[data-block-id]").first();
    await para.scrollIntoViewIfNeeded();
    await para.hover();
    await para.getByRole("button", { name: "対訳を表示" }).click();
    const popover = page.getByRole("dialog", { name: "対訳" });
    await expect(popover).toBeVisible();
    await dragSelect(page, popover.locator("[data-yk-source-text]"));

    const menu = page.getByRole("menu", { name: "選択メニュー" });
    await expect(menu).toBeVisible();
    const addVocabItem = menu.getByRole("menuitem", { name: "語彙に追加" });
    await expect(addVocabItem).toBeEnabled();

    let vocabId = "";
    let createdByThisRun = false;
    const [addResponse] = await Promise.all([
      page.waitForResponse((res) => res.url().includes("/api/vocab") && res.request().method() === "POST"),
      addVocabItem.click(),
    ]);
    if (addResponse.status() === 201) {
      const body = (await addResponse.json()) as { entry: { id: string } };
      vocabId = body.entry.id;
      createdByThisRun = true;
    } else {
      // 2 連続実行で同一選択が重複した場合(PW-10 と同方針)。
      expect(addResponse.status()).toBe(409);
      const body = (await addResponse.json()) as { existing?: { vocab_id?: string } };
      vocabId = body.existing?.vocab_id ?? "";
    }
    expect(vocabId).not.toBe("");
    await expect(page).toHaveURL(new RegExp(`/vocab/${vocabId}$`));

    try {
      // AI 生成 6 セクション(文脈での語義 + 文脈センテンス + 解釈のしかた + 語源メモ +
      // ✦ 覚えるコツ + よく出る形・近い表現)。見出し <span> と、編集ボタン非表示時に同一
      // テキストとなる親 <div> の両方にマッチする(strict mode)ため .first() で絞る。
      await expect(page.getByText("文脈での語義").first()).toBeVisible();
      await expect(page.getByText("解釈のしかた", { exact: false }).first()).toBeVisible();
      await expect(page.getByText("語源メモ", { exact: true }).first()).toBeVisible();
      await expect(page.getByText("✦ 覚えるコツ").first()).toBeVisible();
      await expect(page.getByText("よく出る形・近い表現").first()).toBeVisible();
      const openSourceLink = page.getByText("原文で見る →");
      await expect(openSourceLink).toBeVisible();

      // 編集: 語源メモ。
      const etymologySection = page.getByText("語源メモ", { exact: true }).first().locator("xpath=../..");
      await etymologySection.hover();
      await etymologySection.getByRole("button", { name: "編集" }).click();
      await page.getByRole("textbox", { name: "語源メモを編集" }).fill("E2E で編集したメモ");
      await page.getByRole("button", { name: "保存" }).click();
      await expect(page.getByText("E2E で編集したメモ")).toBeVisible();

      // 「原文で見る →」→ ビューア(mode=source)へ遷移。
      await openSourceLink.click();
      await expect(page).toHaveURL(/\/papers\/.*mode=source/);
    } finally {
      if (vocabId && createdByThisRun) {
        await page.request.delete(`/api/vocab/${vocabId}`, { headers: { Origin: ORIGIN } }).catch(() => undefined);
      }
    }
  });

  test("復習をはじめる→2択評価→次の復習更新", async ({ page }) => {
    const due = seedDueVocab("dev@yakudoku.test");
    try {
      await page.goto(`/vocab/${due.vocab_id}`);
      await expect(page.getByText(due.term).first()).toBeVisible();

      await page.getByRole("button", { name: /復習をはじめる/ }).click();
      const modal = page.getByRole("dialog", { name: "復習" });
      await expect(modal).toBeVisible();

      // due の枚数は他 spec の副作用で変動し得るため、終了まで上限付きでループする。
      for (let i = 0; i < 20; i++) {
        if (await modal.getByText("復習が終わりました").isVisible().catch(() => false)) break;
        await modal.getByRole("button", { name: "答えを見る" }).click();
        await modal.getByRole("button", { name: "✓ 覚えた" }).click();
      }
      await expect(modal.getByText("復習が終わりました")).toBeVisible();
      await modal.getByRole("button", { name: "閉じる" }).click();
      await expect(modal).toBeHidden();

      // 段階 1→2(間隔 3 日)へ進み、まだ復習期のまま「次の復習」が表示される(docs/11 §7.1)。
      await expect(page.getByText(/次の復習/).first()).toBeVisible();
    } finally {
      await page.request.delete(`/api/vocab/${due.vocab_id}`, { headers: { Origin: ORIGIN } }).catch(() => undefined);
    }
  });
});
