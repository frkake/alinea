import { mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import {
  createExtensionContext,
  ensureLoggedIn,
  expect,
  extensionIdOf,
  popupUrl,
  test,
} from "./fixtures";

const __dirname = dirname(fileURLToPath(import.meta.url));

/**
 * 拡張 E2E(plans/12 §5.2)。ビルド済み拡張をロードした persistent context で popup.html を
 * 直接開いて各状態を検証する。XT-01(未ログイン)→ ログイン → 以降(ログイン済み)の順に直列実行する。
 */

// service worker 評価内で使う chrome 拡張 API の最小型(TS 用宣言。実体は SW ランタイム)。
declare const chrome: {
  storage: { local: { set(items: Record<string, unknown>): Promise<void> } };
  action: {
    getBadgeText(details: Record<string, never>): Promise<string>;
    getBadgeBackgroundColor(details: Record<string, never>): Promise<number[]>;
  };
  permissions: { request(p: { origins: string[] }): Promise<boolean> };
  scripting: {
    registerContentScripts(
      s: Array<{ id: string; matches: string[]; js: string[]; runAt?: string }>,
    ): Promise<void>;
    unregisterContentScripts(f?: { ids: string[] }): Promise<void>;
  };
};

const SEED_ARXIV = "https://arxiv.org/abs/2209.03003"; // シード済み(状態3 用)

function freshArxiv(): string {
  const n = (Date.now() % 90000) + 10000;
  return `https://arxiv.org/abs/2403.${String(n).padStart(5, "0")}`;
}

test.describe.serial("拡張 E2E", () => {
  test("XT-01 未ログインではログイン導線が出る", async () => {
    // 共有 context はログイン済みになるため、未ログイン検証は使い捨て context で行う。
    const ctx = await createExtensionContext();
    try {
      const id = await extensionIdOf(ctx);
      const page = await ctx.newPage();
      await page.goto(popupUrl(id, SEED_ARXIV));
      await expect(page.getByText("保存にはログインが必要です。")).toBeVisible();
      await expect(page.getByRole("button", { name: "ログイン" })).toBeVisible();
    } finally {
      await ctx.close();
    }
  });

  test("XT-02 状態1(保存前): 書誌プレビュー + 品質A見込み", async ({ extContext, extensionId }) => {
    await ensureLoggedIn(extContext);
    const url = freshArxiv();
    const id = url.split("/abs/")[1];
    const page = await extContext.newPage();
    await page.goto(popupUrl(extensionId, url));
    await expect(page.getByText(`Mock Paper for ${id}`)).toBeVisible();
    await expect(page.getByText("品質レベル A 見込み")).toBeVisible();
    await page.close();
  });

  // XT-SITE: ACL Anthology 等の対応サイト URL は保存前フォーム(site 検出バッジ)を出す。
  // 既定 skip: サーバの ingest_check がサイト landing を取得する必要があり、ローカル E2E では
  // 外部ネットワークに接続しないため(Task 15 の hard constraint)。バックエンドに ACL フィクスチャ
  // を配線した環境でのみ有効化する。
  test.skip("XT-SITE 対応サイト URL は保存前フォームを出す", async ({ extContext, extensionId }) => {
    await ensureLoggedIn(extContext);
    const page = await extContext.newPage();
    await page.goto(popupUrl(extensionId, "https://aclanthology.org/2023.acl-long.42/"));
    await expect(page.getByText("対応サイトの論文を検出")).toBeVisible();
    await page.close();
  });

  // XT-HF: Hugging Face URL は arXiv ID を解決して既存 arXiv 経路の保存前フォームを出す(Task 18)。
  // 既定 skip: ingest_check がサーバ側で HF Hub API を叩く必要があり、ローカル E2E では外部
  // ネットワークに接続しないため(Task 15/18 の hard constraint)。HF Hub API モックを配線した
  // 環境でのみ有効化する。arXiv タグの無い Model/Dataset/Space は「関連論文が見つかりません」。
  test.skip("XT-HF Hugging Face URL は関連論文を解決して保存前フォームを出す", async ({
    extContext,
    extensionId,
  }) => {
    await ensureLoggedIn(extContext);
    const page = await extContext.newPage();
    await page.goto(popupUrl(extensionId, "https://huggingface.co/papers/2307.09288"));
    // Paper URL は arXiv ID(2307.09288)に解決され、arXiv 書誌プレビューが出る。
    await expect(page.getByText("品質レベル A 見込み")).toBeVisible();
    await page.close();
  });

  test.skip("XT-HF arXiv タグが無い Model は関連論文が見つからない旨を表示する", async ({
    extContext,
    extensionId,
  }) => {
    await ensureLoggedIn(extContext);
    const page = await extContext.newPage();
    await page.goto(popupUrl(extensionId, "https://huggingface.co/some/model-without-arxiv-tag"));
    await expect(page.getByText("関連論文が見つかりません")).toBeVisible();
    await page.close();
  });

  test("XT-03/04 保存操作(Enter)→ 状態2(保存直後)進捗 + サイトで開く", async ({
    extContext,
    extensionId,
  }) => {
    await ensureLoggedIn(extContext);
    const url = freshArxiv();
    const page = await extContext.newPage();
    await page.goto(popupUrl(extensionId, url));
    await expect(page.getByText("品質レベル A 見込み")).toBeVisible();
    // Enter キーで保存(3a §5.2)。
    await page.locator(".ext-note-input").press("Enter");
    // 状態2: ヘッダ「保存しました」+ パイプライン進捗 + 「サイトで開く ↗」。
    await expect(page.locator(".ext-header-title")).toHaveText("保存しました");
    await expect(page.getByRole("button", { name: "サイトで開く ↗" })).toBeVisible();
    await page.close();
  });

  test("XT-03 保存前フォームのコレクション欄: 一覧表示→選択→保存でエントリ追加", async ({
    extContext,
    extensionId,
  }) => {
    // コレクション欄は docs/10 §2 の決定により M2 で表示解禁(plans/13 §4.2・§7)。
    await ensureLoggedIn(extContext);
    const name = `XT03 輪読会 ${Date.now()}`;
    const createRes = await extContext.request.post("http://localhost:3000/api/collections", {
      headers: { Origin: "http://localhost:3000" },
      data: { name },
    });
    expect(createRes.ok()).toBe(true);
    const created = (await createRes.json()) as { id: string };

    const url = freshArxiv();
    const page = await extContext.newPage();
    await page.goto(popupUrl(extensionId, url));
    await expect(page.getByText("品質レベル A 見込み")).toBeVisible();

    const select = page.getByRole("combobox", { name: "コレクション" });
    await expect(select).toBeVisible();
    await expect(select.getByRole("option", { name: "なし" })).toHaveCount(1);
    await expect(select.getByRole("option", { name })).toHaveCount(1);
    await select.selectOption({ label: name });

    await page.getByRole("button", { name: /保存/ }).click();
    await expect(page.locator(".ext-header-title")).toHaveText("保存しました");
    await page.close();

    // 保存(ingest)経路が collection_id を受けてエントリを追加している(plans/03 §3.2)。
    const listRes = await extContext.request.get("http://localhost:3000/api/collections");
    const { items } = (await listRes.json()) as {
      items: Array<{ id: string; item_count: number }>;
    };
    const updated = items.find((item) => item.id === created.id);
    expect(updated?.item_count).toBe(1);
  });

  test("XT-05 状態3(既にライブラリ): 重複保存 UI なし + 続きから開く/ステータス変更", async ({
    extContext,
    extensionId,
  }) => {
    await ensureLoggedIn(extContext);
    const page = await extContext.newPage();
    await page.goto(popupUrl(extensionId, SEED_ARXIV));
    await expect(page.locator(".ext-header-title")).toHaveText("既にライブラリにあります");
    await expect(page.getByRole("button", { name: "続きから開く ↗" })).toBeVisible();
    await expect(page.getByRole("button", { name: "ステータス変更 ▾" })).toBeVisible();
    // 保存前フォームの品質見込み行は出ない(重複保存 UI なし)。
    await expect(page.getByText("品質レベル A 見込み")).toHaveCount(0);
    await page.close();
  });

  test("XT-07 フッタ「直近の取り込み」が処理履歴を表示", async ({ extContext, extensionId }) => {
    await ensureLoggedIn(extContext);
    const page = await extContext.newPage();
    await page.goto(popupUrl(extensionId, SEED_ARXIV));
    const footer = page.locator(".ext-footer");
    await expect(footer.getByText("直近の取り込み")).toBeVisible();
    await expect(footer.locator(".ext-recent-row").first()).toBeVisible();
    await page.close();
  });

  test("XT-09 バッジ: アクティブジョブありで琥珀ドット(background service worker)", async ({
    extContext,
  }) => {
    let [sw] = extContext.serviceWorkers();
    if (!sw) sw = await extContext.waitForEvent("serviceworker");
    // アクティブジョブを storage に注入 → onChanged で runLoop → pollOnce → バッジ更新。
    await sw.evaluate(async () => {
      await chrome.storage.local.set({ yk_active_jobs: ["xt09-nonexistent-job"] });
    });
    await expect
      .poll(async () => sw.evaluate(() => chrome.action.getBadgeText({})), { timeout: 10_000 })
      .toBe("●");
    const color = await sw.evaluate(() => chrome.action.getBadgeBackgroundColor({}));
    // AMBER #C49432 = rgb(196,148,50)。
    expect(color).toEqual([196, 148, 50, 255]);
    // 後片付け。
    await sw.evaluate(async () => {
      await chrome.storage.local.set({ yk_active_jobs: [] });
    });
  });

  test("XT-06 状態4(一般PDF): 明示クリックのみで送信・書誌は推定", async ({
    extContext,
    extensionId,
  }) => {
    await ensureLoggedIn(extContext);
    const pdfPath = join(__dirname, "..", "..", "web", "e2e", "fixtures", "sample.pdf");
    const nonce = `\n% xt06-${Date.now()}-${Math.random().toString(36).slice(2, 8)}\n`;
    const pdfBytes = Buffer.concat([readFileSync(pdfPath), Buffer.from(nonce)]);
    const pdfUrl = "https://repo.example.edu/theses/e2e-generic-thesis.pdf";

    let fetchCount = 0;
    await extContext.route(pdfUrl, async (route) => {
      fetchCount += 1;
      await route.fulfill({ status: 200, contentType: "application/pdf", body: pdfBytes });
    });
    let ingestCount = 0;
    await extContext.route("**/api/ingest/pdf", async (route) => {
      ingestCount += 1;
      await route.continue();
    });

    const page = await extContext.newPage();
    await page.goto(popupUrl(extensionId, pdfUrl, "E2E Generic Thesis"));

    // 状態4: 書誌は推定表示 + 警告 + 明示送信ボタンのみ。private 保存の注記。
    await expect(page.getByText("書誌は推定")).toBeVisible();
    await expect(page.getByText(/このページはサーバーから取得できない可能性/)).toBeVisible();
    await expect(page.getByText("private 論文として保存され、共有されません")).toBeVisible();
    const sendButton = page.getByRole("button", { name: "このタブの PDF を送信" });
    await expect(sendButton).toBeVisible();

    // 自動送信なし(ポップアップ表示だけでは PDF 取得も ingest も走らない)。
    expect(fetchCount).toBe(0);
    expect(ingestCount).toBe(0);

    await sendButton.click();
    await expect(page.locator(".ext-header-title")).toHaveText("保存しました", { timeout: 20_000 });
    expect(fetchCount).toBe(1);
    expect(ingestCount).toBe(1);
    await page.close();
  });

  test("XT-08 「A 保存」ピル: 既定オフ(コンテントスクリプト非注入)", async ({
    extContext,
    extensionId,
  }) => {
    // 決定(followups 参照): 設定オンへの切替は chrome.permissions.request の実ユーザー
    // ジェスチャーを要求し(検証済み: Playwright のクリック/SW 経由呼び出しはいずれも
    // "must be called during a user gesture" で拒否/非許可となる)、この分岐は Playwright
    // では自動化できない。本テストは既定オフ(=abs ページに .alinea-pill が一切注入されない)
    // という不変条件のみを検証する。設定オン以降の注入・保存・非 arXiv 非注入の確認は
    // test.fixme(下記)。
    await ensureLoggedIn(extContext);
    const fixtureHtml = readFileSync(
      join(__dirname, "fixtures", "arxiv-abs-2209.03003.html"),
      "utf-8",
    );
    await extContext.route("https://arxiv.org/abs/2209.03003", async (route) => {
      await route.fulfill({ status: 200, contentType: "text/html", body: fixtureHtml });
    });
    const page = await extContext.newPage();
    await page.goto("https://arxiv.org/abs/2209.03003");
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    await page.waitForTimeout(500); // content script は runtime 登録がなければ何もしないため即時確認で十分。
    expect(await page.locator(".alinea-pill").count()).toBe(0);
    await page.close();

    // 拡張のポップアップ設定にも同じ既定値が反映されている。
    const settingsPage = await extContext.newPage();
    await settingsPage.goto(popupUrl(extensionId, SEED_ARXIV));
    await settingsPage.getByRole("button", { name: "設定" }).click();
    await expect(settingsPage.getByRole("switch")).toHaveAttribute("aria-checked", "false");
    await settingsPage.close();
  });

  // XT-08(設定オン): arXiv abs のみ注入・非 arXiv 非注入。ピル注入の登録経路(SW の
  // scripting.registerContentScripts)を直接叩いて検証する。popup の「設定オン」トグルが
  // 呼ぶ browser.permissions.request(optional host)は実ユーザージェスチャーを要し
  // Playwright では付与できない(XT-08 の docstring 参照)。そのため本テストは
  // release-env(host 権限を事前付与できる環境)専用として test.fixme で登録し、
  // 実操作+assertion を完成形で残す(権限付与手段が整えば .fixme を外すだけで通る)。
  test.fixme(
    "XT-08 設定オンで arXiv abs のみ注入・保存後「保存済み」・非arXivページに非注入",
    async ({ extContext, extensionId }) => {
      await ensureLoggedIn(extContext);

      // SW で optional host を付与し、abs 限定 matches で content script を登録する
      // (popup の setPillEnabled と同じ登録。permissions.request のみジェスチャー依存)。
      let [sw] = extContext.serviceWorkers();
      if (!sw) sw = await extContext.waitForEvent("serviceworker");
      await sw.evaluate(async () => {
        await chrome.permissions.request({ origins: ["https://arxiv.org/*"] });
        await chrome.scripting.registerContentScripts([
          {
            id: "arxiv-pill",
            matches: ["https://arxiv.org/abs/*"],
            js: ["content-scripts/arxiv-pill.js"],
            runAt: "document_idle",
          },
        ]);
        await chrome.storage.local.set({ "settings:arxivPillEnabled": true });
      });

      // arXiv abs ページ(ローカル fixture を配信)にピルが注入される。
      const absHtml = readFileSync(join(__dirname, "fixtures", "arxiv-abs-2209.03003.html"), "utf-8");
      await extContext.route("https://arxiv.org/abs/2209.03003", async (route) => {
        await route.fulfill({ status: 200, contentType: "text/html", body: absHtml });
      });
      const abs = await extContext.newPage();
      await abs.goto("https://arxiv.org/abs/2209.03003");
      const pill = abs.locator(".alinea-pill");
      await expect(pill).toBeVisible();
      await expect(pill.locator(".alinea-pill-label")).toHaveText("保存");

      // クリック保存 → 「保存済み」へ遷移する。
      await pill.click();
      await expect(pill.locator(".alinea-pill-label")).toHaveText("保存済み");
      await abs.close();

      // 非 arXiv ページには注入されない(matches が abs 限定)。
      const other = await extContext.newPage();
      await other.route("https://example.org/paper", async (route) => {
        await route.fulfill({ status: 200, contentType: "text/html", body: "<h1 class='title'>x</h1>" });
      });
      await other.goto("https://example.org/paper");
      await other.waitForTimeout(500);
      expect(await other.locator(".alinea-pill").count()).toBe(0);
      await other.close();

      // 後始末。
      await sw.evaluate(async () => {
        await chrome.scripting.unregisterContentScripts({ ids: ["arxiv-pill"] });
        await chrome.storage.local.set({ "settings:arxivPillEnabled": false });
      });
    },
  );

  test("XT-10 送信キュー永続: API停止中の保存が失敗キューに残り、コンテキスト再起動後も残り、復旧後に再試行で送信される", async () => {
    const userDataDir = mkdtempSync(join(tmpdir(), "alinea-ext-xt10-"));
    const url = freshArxiv();

    // 1) API 停止状態を模倣(ingest 系エンドポイントのみネットワークエラーにする)→保存→失敗キュー。
    const ctx1 = await createExtensionContext(userDataDir);
    try {
      await ensureLoggedIn(ctx1);
      await ctx1.route("**/api/ingest/arxiv", (route) => route.abort("connectionfailed"));
      const page = await ctx1.newPage();
      await page.goto(popupUrl(await extensionIdOf(ctx1), url));
      await expect(page.getByText("品質レベル A 見込み")).toBeVisible();
      await page.locator(".ext-note-input").press("Enter");
      await expect(page.getByText(/送信できなかった保存が 1 件あります/)).toBeVisible({
        timeout: 15_000,
      });
      await page.close();
    } finally {
      // persistent context を完全に終了しないと同一 userDataDir を再度開けない。
      await ctx1.close();
    }

    // 2) コンテキスト再起動(同一 userDataDir → chrome.storage.local が引き継がれる)。
    //    API はまだ停止状態のまま: キューが再起動後も残ることを確認する。
    const ctx2 = await createExtensionContext(userDataDir);
    try {
      await ensureLoggedIn(ctx2);
      await ctx2.route("**/api/ingest/arxiv", (route) => route.abort("connectionfailed"));
      const page2 = await ctx2.newPage();
      await page2.goto(popupUrl(await extensionIdOf(ctx2), url));
      await page2.getByText(/送信できなかった保存が 1 件あります/).click();
      await expect(page2.getByText(url)).toBeVisible().catch(async () => {
        // タイトル行はURLでなくtitleを表示するため、行の存在のみ確認する。
        await expect(page2.locator(".ext-queue-row")).toHaveCount(1);
      });
      await page2.close();
    } finally {
      await ctx2.close();
    }

    // 3) API 復旧(route を張らない)→再起動→「再試行」で送信される(background.ts の
    //    followups: chrome.alarms 自動再送は本スライスで省略されているため、復旧後の送信は
    //    ユーザーの「再試行」操作が起点。plans/12 の「自動送信」は既知のギャップ。followups 参照)。
    const ctx3 = await createExtensionContext(userDataDir);
    try {
      await ensureLoggedIn(ctx3);
      const page3 = await ctx3.newPage();
      await page3.goto(popupUrl(await extensionIdOf(ctx3), url));
      await page3.getByText(/送信できなかった保存が 1 件あります/).click();
      await page3.getByRole("button", { name: "再試行" }).first().click();
      await expect(page3.getByText(/送信できなかった保存が/)).toBeHidden({ timeout: 15_000 });
      await page3.close();
    } finally {
      await ctx3.close();
    }
  });
});
