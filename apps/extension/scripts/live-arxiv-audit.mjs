/* global URLSearchParams, console, document, getComputedStyle, process, setTimeout, window */

import { mkdtempSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "@playwright/test";

const __dirname = dirname(fileURLToPath(import.meta.url));
const extensionRoot = resolve(__dirname, "..");
const extensionDist = resolve(extensionRoot, ".output", "chrome-mv3");
const appOrigin = process.env.ALINEA_LIVE_APP_ORIGIN ?? "http://localhost:3000";
const mailpitOrigin = process.env.ALINEA_LIVE_MAILPIT_ORIGIN ?? "http://localhost:8025";
const email = process.env.ALINEA_LIVE_EMAIL ?? "live-arxiv-audit@alinea.test";
const outputDir = resolve(
  process.cwd(),
  process.env.ALINEA_LIVE_OUTPUT_DIR ?? "../../artifacts/live-arxiv-audit",
);
const ingestTimeoutMs = Number(process.env.ALINEA_LIVE_INGEST_TIMEOUT_MS ?? 2_700_000);
const articleTimeoutMs = Number(process.env.ALINEA_LIVE_ARTICLE_TIMEOUT_MS ?? 900_000);
const selectedLeafCount = Math.max(1, Number(process.env.ALINEA_LIVE_SELECTED_LEAVES ?? 2));
const rawIds = process.env.ALINEA_LIVE_ARXIV_IDS ?? "";
const arxivIds = rawIds
  .split(/[\s,]+/)
  .map((value) => value.trim())
  .filter(Boolean);
const reingestIds = new Set(
  (process.env.ALINEA_LIVE_REINGEST_IDS ?? "")
    .split(/[\s,]+/)
    .map((value) => value.trim())
    .filter(Boolean),
);
const expectedCount = Number(process.env.ALINEA_LIVE_EXPECT_COUNT ?? 0);

if (arxivIds.length === 0) {
  throw new Error("ALINEA_LIVE_ARXIV_IDS must contain at least one arXiv identifier");
}
if (new Set(arxivIds).size !== arxivIds.length) {
  throw new Error("ALINEA_LIVE_ARXIV_IDS contains duplicates");
}
if (arxivIds.some((value) => !/^\d{4}\.\d{4,5}(?:v\d+)?$/.test(value))) {
  throw new Error("ALINEA_LIVE_ARXIV_IDS contains an invalid identifier");
}
if (expectedCount > 0 && arxivIds.length !== expectedCount) {
  throw new Error(`expected ${expectedCount} papers, received ${arxivIds.length}`);
}
if (!readFileSync(resolve(extensionDist, "manifest.json"), "utf8")) {
  throw new Error(`extension build not found: ${extensionDist}`);
}

mkdirSync(outputDir, { recursive: true });
mkdirSync(resolve(outputDir, "screenshots"), { recursive: true });
mkdirSync(resolve(outputDir, "pdfs"), { recursive: true });

const report = {
  started_at: new Date().toISOString(),
  app_origin: appOrigin,
  email,
  requested_ids: arxivIds,
  results: [],
};

function persistReport() {
  writeFileSync(resolve(outputDir, "report.json"), `${JSON.stringify(report, null, 2)}\n`, "utf8");
}

function sleep(ms) {
  return new Promise((resolvePromise) => setTimeout(resolvePromise, ms));
}

async function captureScreenshot(page, path) {
  try {
    await page.screenshot({ path, timeout: 120_000 });
  } catch (error) {
    console.warn(`[live-audit] screenshot skipped: ${path}`, error);
  }
}

async function extensionIdOf(context) {
  let [worker] = context.serviceWorkers();
  if (!worker) worker = await context.waitForEvent("serviceworker");
  return worker.url().split("/")[2] ?? "";
}

function popupUrl(extensionId, arxivId) {
  const query = new URLSearchParams({
    tab_url: `https://arxiv.org/abs/${arxivId}`,
    tab_title: `arXiv:${arxivId}`,
  });
  return `chrome-extension://${extensionId}/popup.html?${query.toString()}`;
}

async function magicLink(request, address) {
  for (let attempt = 0; attempt < 60; attempt += 1) {
    const list = await request.get(`${mailpitOrigin}/api/v1/messages?limit=100`);
    if (list.ok()) {
      const payload = await list.json();
      const message = (payload.messages ?? []).find((candidate) =>
        (candidate.To ?? []).some(
          (recipient) => recipient.Address?.toLowerCase() === address.toLowerCase(),
        ),
      );
      if (message) {
        const detail = await request.get(`${mailpitOrigin}/api/v1/message/${message.ID}`);
        const body = await detail.json();
        const material = `${body.Text ?? ""}\n${body.HTML ?? ""}`.replaceAll("&amp;", "&");
        const match = material.match(
          /https?:\/\/[^\s"'<>]+\/api\/auth\/email\/verify\?token=[^\s"'<>]+/,
        );
        if (match) return match[0];
      }
    }
    await sleep(500);
  }
  throw new Error(`login link for ${address} was not delivered`);
}

async function loginThroughFrontend(context) {
  const page = await context.newPage();
  await page.goto(`${appOrigin}/login`);
  if (/\/dashboard/.test(page.url())) {
    await page.close();
    return;
  }
  await page.locator("#login-email").fill(email);
  await page.getByRole("button", { name: "ログインリンクを送信" }).click();
  await page.getByText("ログインリンクを送信しました").waitFor({ timeout: 30_000 });
  const link = await magicLink(context.request, email);
  await page.goto(link);
  await page.waitForURL(/\/dashboard$/, { timeout: 30_000 });
  await page.close();
}

async function enableLongPaperSelection(context) {
  const page = await context.newPage();
  await page.goto(`${appOrigin}/settings`);
  const navigation = page.getByRole("navigation", { name: "設定カテゴリ" });
  await navigation.getByRole("button", { name: "翻訳" }).click();
  const toggle = page.getByRole("switch", {
    name: "30 ページ超の論文はセクション選択を提案",
  });
  await toggle.waitFor();
  if ((await toggle.getAttribute("aria-checked")) !== "true") {
    await toggle.click();
    await page.waitForFunction(
      () =>
        document
          .querySelector('[role="switch"][aria-label="30 ページ超の論文はセクション選択を提案"]')
          ?.getAttribute("aria-checked") === "true",
    );
  }
  await page.reload();
  await page.getByRole("switch", { name: "30 ページ超の論文はセクション選択を提案" }).waitFor();
  await page.close();
}

async function getJson(request, path) {
  for (let attempt = 0; attempt < 8; attempt += 1) {
    const response = await request.get(`${appOrigin}${path}`);
    if (response.ok()) return response.json();
    if (response.status() === 429 && attempt < 7) {
      const retryAfter = Number(response.headers()["retry-after"] ?? "");
      const delayMs =
        Number.isFinite(retryAfter) && retryAfter > 0
          ? Math.min(60_000, retryAfter * 1_000)
          : Math.min(30_000, 1_000 * 2 ** attempt);
      await sleep(delayMs);
      continue;
    }
    throw new Error(`${path} returned ${response.status()}: ${await response.text()}`);
  }
  throw new Error(`${path} remained rate limited`);
}

async function waitForJob(request, jobId, timeoutMs, { allowWaitingInput = false } = {}) {
  const deadline = Date.now() + timeoutMs;
  let latest = null;
  while (Date.now() < deadline) {
    latest = await getJson(request, `/api/jobs/${jobId}`);
    if (["succeeded", "failed", "canceled"].includes(latest.status)) return latest;
    if (allowWaitingInput && latest.status === "waiting_input") return latest;
    await sleep(3_000);
  }
  throw new Error(
    `job ${jobId} timed out at ${latest?.status ?? "unknown"}/${latest?.stage ?? "unknown"}`,
  );
}

async function waitForExtensionPopupState(popup, url, arxivId) {
  const existing = popup
    .locator(".ext-header-title")
    .filter({ hasText: "既にライブラリにあります" });
  const saveForm = popup.locator(".ext-bib-title");
  const retry = popup.getByRole("button", { name: "再試行", exact: true });
  let recoveries = 0;
  let lastText = "";

  for (let attempt = 0; attempt < 3; attempt += 1) {
    if (attempt === 0) await popup.goto(url);
    else await popup.reload({ waitUntil: "domcontentloaded" });
    const deadline = Date.now() + 45_000;
    let clickedRetry = false;
    while (Date.now() < deadline) {
      if (await existing.isVisible().catch(() => false)) {
        return { state: "existing", recoveries };
      }
      if (await saveForm.isVisible().catch(() => false)) {
        return { state: "save", recoveries };
      }
      if (!clickedRetry && (await retry.isVisible().catch(() => false))) {
        await retry.click();
        clickedRetry = true;
        recoveries += 1;
        await sleep(1_500);
        continue;
      }
      lastText = (
        (await popup
          .locator("body")
          .innerText()
          .catch(() => "")) ?? ""
      )
        .replace(/\s+/g, " ")
        .trim();
      await sleep(500);
    }
    await captureScreenshot(
      popup,
      resolve(outputDir, "screenshots", `${arxivId}-popup-recovery-${attempt + 1}.png`),
    );
    recoveries += 1;
  }
  throw new Error(`extension popup did not become ready: ${lastText.slice(0, 300)}`);
}

async function saveFromExtension(context, extensionId, arxivId) {
  const popup = await context.newPage();
  const popupReady = await waitForExtensionPopupState(
    popup,
    popupUrl(extensionId, arxivId),
    arxivId,
  );
  const popupState = popupReady.state;
  if (popupState === "existing") {
    const check = await getJson(
      context.request,
      `/api/ingest/check?url=${encodeURIComponent(`https://arxiv.org/abs/${arxivId}`)}`,
    );
    await popup.close();
    if (!check.saved?.library_item_id) throw new Error("duplicate paper has no library item");
    const pipelineStage = check.saved.pipeline?.stage ?? null;
    const activeJobId =
      pipelineStage != null && !["complete", "failed", "canceled"].includes(pipelineStage)
        ? (check.saved.pipeline?.job_id ?? null)
        : null;
    return {
      title: check.bib?.title ?? arxivId,
      libraryItemId: check.saved.library_item_id,
      jobId: activeJobId,
      duplicate: true,
      popupRecoveryCount: popupReady.recoveries,
    };
  }

  const title = (await popup.locator(".ext-bib-title").textContent())?.trim() ?? arxivId;
  const quality = popup.locator(".ext-quality");
  const qualityPreview = (await quality.count())
    ? ((await quality.textContent())?.trim() ?? null)
    : null;
  const responsePromise = popup.waitForResponse(
    (response) =>
      response.request().method() === "POST" && response.url().includes("/api/ingest/arxiv"),
    { timeout: 120_000 },
  );
  await popup.getByRole("button", { name: /保存/ }).click();
  const response = await responsePromise;
  const responseText = await response.text();
  if (response.status() !== 202) {
    throw new Error(`extension save returned ${response.status()}: ${responseText}`);
  }
  const accepted = JSON.parse(responseText);
  await popup.locator(".ext-header-title").filter({ hasText: "保存しました" }).waitFor();
  await popup.close();
  return {
    title,
    qualityPreview,
    libraryItemId: accepted.library_item_id,
    jobId: accepted.job_id,
    duplicate: false,
    popupRecoveryCount: popupReady.recoveries,
  };
}

async function chooseSectionsThroughViewer(page, itemId, result) {
  await page.goto(`${appOrigin}/papers/${itemId}?mode=translation`);
  const dialog = page.getByRole("dialog");
  await dialog.waitFor({ timeout: 60_000 });
  await captureScreenshot(
    page,
    resolve(outputDir, "screenshots", `${result.arxiv_id}-section-selection.png`),
  );
  const clear = dialog.getByRole("button", { name: "すべて解除" });
  if (await clear.isVisible()) await clear.click();
  const leaves = dialog.locator('label:only-child input[type="checkbox"]');
  const leafCount = await leaves.count();
  if (leafCount === 0) throw new Error("section selection dialog has no leaf checkbox");
  for (let index = 0; index < Math.min(selectedLeafCount, leafCount); index += 1) {
    await leaves.nth(index).check();
  }
  result.selected_sections = await leaves.evaluateAll((inputs) =>
    inputs
      .filter((input) => input.checked)
      .map((input) => input.getAttribute("aria-label") ?? "")
      .filter(Boolean),
  );
  const submit = dialog.getByRole("button", { name: "選択したセクションを翻訳" });
  await submit.click();
  await dialog.waitFor({ state: "hidden", timeout: 60_000 });
}

async function reingestThroughViewer(page, itemId, result) {
  await page.goto(`${appOrigin}/papers/${itemId}?mode=translation`);
  await page.locator("[data-block-id]").first().waitFor({ timeout: 120_000 });
  const infoTab = page.getByRole("tab", { name: /^情報/ });
  await infoTab.click();
  const reingest = page.getByRole("button", { name: "再取り込み", exact: true });
  await reingest.waitFor({ timeout: 60_000 });
  await reingest.click();
  const dialog = page.getByRole("dialog");
  await dialog.waitFor();
  const responsePromise = page.waitForResponse(
    (response) => response.request().method() === "POST" && response.url().includes("/reingest"),
    { timeout: 120_000 },
  );
  await dialog.getByRole("button", { name: "再取り込み", exact: true }).click();
  const response = await responsePromise;
  if (response.status() !== 202) {
    throw new Error(`frontend reingest returned ${response.status()}: ${await response.text()}`);
  }
  const accepted = JSON.parse(await response.text());
  result.reingest_job_id = accepted.job_id;
  let final = await waitForJob(page.context().request, accepted.job_id, ingestTimeoutMs, {
    allowWaitingInput: true,
  });
  if (final.status === "waiting_input") {
    result.reingest_section_selection_required = true;
    await chooseSectionsThroughViewer(page, itemId, result);
    final = await waitForJob(page.context().request, accepted.job_id, ingestTimeoutMs);
  }
  result.reingest = final;
  if (final.status !== "succeeded") {
    throw new Error(`reingest finished as ${final.status}: ${final.error ?? "unknown error"}`);
  }
}

async function visibleTextWithoutMath(page, rootSelector) {
  return page.locator(rootSelector).evaluate((root) => {
    const copy = root.cloneNode(true);
    for (const node of copy.querySelectorAll(
      ".katex, .katex-display, code, pre, [data-latex], [aria-hidden='true']",
    )) {
      node.remove();
    }
    return copy.textContent ?? "";
  });
}

function latexLeaks(text) {
  const pattern =
    /\\(?:begin|end|section|subsection|subsubsection|paragraph|textbf|textit|emph|cite|ref|label|includegraphics|caption|item|documentclass|usepackage)\b/g;
  return [...new Set(text.match(pattern) ?? [])].slice(0, 20);
}

async function auditLayout(page, rootSelector = "main") {
  return page.locator(rootSelector).evaluate((root) => {
    const documentOverflow = document.documentElement.scrollWidth - window.innerWidth;
    const offenders = [];
    if (documentOverflow > 3) {
      offenders.push({
        tag: "document",
        id: null,
        overflow_px: Math.round(documentOverflow),
        text: "document-wide horizontal overflow",
      });
    }
    const isInsideHorizontalScroller = (element) => {
      let ancestor = element.parentElement;
      while (ancestor && root.contains(ancestor)) {
        const style = getComputedStyle(ancestor);
        const rect = ancestor.getBoundingClientRect();
        if (
          ["auto", "scroll"].includes(style.overflowX) &&
          ancestor.scrollWidth - ancestor.clientWidth > 3 &&
          rect.left >= -3 &&
          rect.right <= window.innerWidth + 3
        ) {
          return true;
        }
        ancestor = ancestor.parentElement;
      }
      return false;
    };
    const nodes = root.querySelectorAll(
      "[data-block-id], [data-article-block], table, img, svg, h1, h2, h3, p",
    );
    for (const element of nodes) {
      const rect = element.getBoundingClientRect();
      const style = getComputedStyle(element);
      const overflow = element.scrollWidth - element.clientWidth;
      const permitsScroll = ["auto", "scroll"].includes(style.overflowX);
      if (overflow > 3 && !permitsScroll && style.overflowX !== "hidden") {
        offenders.push({
          tag: element.tagName.toLowerCase(),
          id: element.getAttribute("data-block-id") ?? element.getAttribute("data-article-block"),
          overflow_px: Math.round(overflow),
          text: (element.textContent ?? "").trim().slice(0, 120),
        });
      }
      if (
        rect.width > 0 &&
        (rect.left < -3 || rect.right > window.innerWidth + 3) &&
        !isInsideHorizontalScroller(element)
      ) {
        offenders.push({
          tag: element.tagName.toLowerCase(),
          id: element.getAttribute("data-block-id") ?? element.getAttribute("data-article-block"),
          viewport_left: Math.round(rect.left),
          viewport_right: Math.round(rect.right),
          text: (element.textContent ?? "").trim().slice(0, 120),
        });
      }
      if (offenders.length >= 50) break;
    }
    return { document_overflow_px: Math.max(0, Math.round(documentOverflow)), offenders };
  });
}

async function auditTranslationViewer(page, itemId, result) {
  await page.goto(`${appOrigin}/papers/${itemId}?mode=translation`);
  const viewerShell = page.getByRole("radiogroup", { name: "表示モード" });
  const reload = page.getByRole("button", { name: "再読み込み", exact: true });
  result.viewer_reload_count = 0;
  for (let attempt = 0; attempt < 3; attempt += 1) {
    const state = await Promise.race([
      viewerShell.waitFor({ timeout: 120_000 }).then(() => "ready"),
      reload.waitFor({ state: "visible", timeout: 120_000 }).then(() => "reload"),
    ]);
    if (state === "ready") break;
    result.viewer_reload_count += 1;
    await reload.click();
  }
  await viewerShell.waitFor({ timeout: 120_000 });
  await page.locator("[data-block-id]").first().waitFor({ timeout: 120_000 });
  const blocks = page.locator("main [data-block-id]");
  const blockCount = await blocks.count();
  await settleImages(page, "main");
  const text = await visibleTextWithoutMath(page, "main");
  const images = await page.locator("main img").evaluateAll((elements) =>
    elements.map((image) => ({
      src: image.currentSrc || image.getAttribute("src"),
      complete: image.complete,
      natural_width: image.naturalWidth,
      natural_height: image.naturalHeight,
    })),
  );
  const tables = await page.locator("main table").evaluateAll((elements) =>
    elements.map((table) => ({
      rows: table.rows.length,
      cells: [...table.rows].reduce((total, row) => total + row.cells.length, 0),
      width: Math.round(table.getBoundingClientRect().width),
    })),
  );
  const mediaBlocks = await page
    .locator("main [data-block-type='figure'], main [data-block-type='table']")
    .evaluateAll((elements) =>
      elements.map((element) => ({
        id: element.getAttribute("data-block-id"),
        type: element.getAttribute("data-block-type"),
        images: element.querySelectorAll("img").length,
        tables: element.querySelectorAll("table").length,
      })),
    );
  result.translation = {
    block_count: blockCount,
    japanese_characters: (text.match(/[\u3040-\u30ff\u3400-\u9fff]/g) ?? []).length,
    latex_leaks: latexLeaks(text),
    images,
    tables,
    media_blocks: mediaBlocks,
    layout: await auditLayout(page),
  };
  await captureScreenshot(
    page,
    resolve(outputDir, "screenshots", `${result.arxiv_id}-translation-desktop.png`),
  );
  await page.setViewportSize({ width: 390, height: 844 });
  await sleep(300);
  result.translation.mobile_layout = await auditLayout(page);
  await captureScreenshot(
    page,
    resolve(outputDir, "screenshots", `${result.arxiv_id}-translation-mobile.png`),
  );
  await page.setViewportSize({ width: 1440, height: 900 });
}

async function settleImages(page, rootSelector) {
  const images = page.locator(`${rootSelector} img`);
  for (let index = 0; index < (await images.count()); index += 1) {
    await images.nth(index).scrollIntoViewIfNeeded();
  }
  await page.waitForFunction(
    (selector) =>
      [...document.querySelectorAll(`${selector} img`)].every((image) => image.complete),
    rootSelector,
    { timeout: 120_000 },
  );
}

async function generateAndAuditArticle(page, itemId, result) {
  await page.goto(`${appOrigin}/papers/${itemId}?mode=article`);
  await page.getByRole("radiogroup", { name: "表示モード" }).waitFor({ timeout: 120_000 });
  const generate = page.getByRole("button", { name: /記事を生成/ });
  const disclaimer = page.getByText("訳文・メモ・チャット履歴から自動構成", { exact: false });
  const articleState = await Promise.race([
    disclaimer.waitFor({ timeout: 120_000 }).then(() => "ready"),
    generate.waitFor({ state: "visible", timeout: 120_000 }).then(() => "generate"),
  ]);
  if (articleState === "generate") {
    const beginner = page.getByRole("radio", { name: "初学者向け", exact: true });
    if (await beginner.isVisible().catch(() => false)) await beginner.click();
    await generate.click();
  }
  await disclaimer.waitFor({ timeout: articleTimeoutMs });
  const articleBlocks = page.locator("[data-article-block]");
  for (let index = 0; index < (await articleBlocks.count()); index += 1) {
    await articleBlocks.nth(index).scrollIntoViewIfNeeded();
  }
  await settleImages(page, "main");
  const text = await visibleTextWithoutMath(page, "main");
  result.article = {
    block_count: await page.locator("[data-article-block]").count(),
    japanese_characters: (text.match(/[\u3040-\u30ff\u3400-\u9fff]/g) ?? []).length,
    latex_leaks: latexLeaks(text),
    images: await page.locator("main img").evaluateAll((elements) =>
      elements.map((image) => ({
        src: image.currentSrc || image.getAttribute("src"),
        complete: image.complete,
        natural_width: image.naturalWidth,
        natural_height: image.naturalHeight,
      })),
    ),
    layout: await auditLayout(page),
  };
  await captureScreenshot(
    page,
    resolve(outputDir, "screenshots", `${result.arxiv_id}-article.png`),
  );
}

async function downloadAndAuditJapanesePdf(page, itemId, result) {
  await page.goto(`${appOrigin}/papers/${itemId}?mode=pdf`);
  const group = page.getByRole("group", { name: "PDF種別" });
  await group.waitFor({ timeout: 120_000 });
  const japanese = group.getByRole("button", { name: "日本語", exact: true });
  const deadline = Date.now() + 120_000;
  let enabledSince = null;
  while (Date.now() < deadline) {
    if (await japanese.isDisabled()) {
      enabledSince = null;
    } else {
      enabledSince ??= Date.now();
      if (Date.now() - enabledSince >= 2_000) break;
    }
    await sleep(500);
  }
  if (enabledSince === null || (await japanese.isDisabled())) {
    throw new Error("Japanese PDF button remained disabled");
  }
  await japanese.click({ timeout: 120_000 });
  await page.locator("canvas").first().waitFor({ timeout: 120_000 });
  const link = page.getByRole("link", { name: /日本語PDF/ });
  if (!(await link.isVisible().catch(() => false))) {
    const openSidebar = page.getByRole("button", { name: "PDFサイドバーを開く" });
    if (await openSidebar.isVisible().catch(() => false)) await openSidebar.click();
  }
  await link.waitFor();
  const downloadPromise = page.waitForEvent("download", { timeout: 120_000 });
  await link.click();
  const download = await downloadPromise;
  const pdfPath = resolve(outputDir, "pdfs", `${result.arxiv_id}-ja.pdf`);
  await download.saveAs(pdfPath);
  result.japanese_pdf = {
    downloaded: true,
    suggested_filename: download.suggestedFilename(),
    canvas_count: await page.locator("canvas").count(),
    path: pdfPath,
    layout: await auditLayout(page),
  };
  await captureScreenshot(page, resolve(outputDir, "screenshots", `${result.arxiv_id}-pdf-ja.png`));
}

const context = await chromium.launchPersistentContext(
  mkdtempSync(resolve(tmpdir(), "alinea-live-arxiv-")),
  {
    headless: false,
    args: [
      "--headless=new",
      "--no-sandbox",
      "--proxy-server=direct://",
      "--proxy-bypass-list=<-loopback>",
      `--disable-extensions-except=${extensionDist}`,
      `--load-extension=${extensionDist}`,
    ],
    viewport: { width: 1440, height: 900 },
    locale: "ja-JP",
    timezoneId: "Asia/Tokyo",
    acceptDownloads: true,
  },
);
context.setDefaultTimeout(30_000);
let interrupted = false;
process.once("SIGINT", () => {
  interrupted = true;
  void context.close();
});

try {
  console.log(`[live-audit] login: ${email}`);
  await loginThroughFrontend(context);
  console.log("[live-audit] enabling long-paper section selection");
  await enableLongPaperSelection(context);
  const extensionId = await extensionIdOf(context);
  const page = await context.newPage();
  const browserErrors = [];
  page.on("pageerror", (error) => browserErrors.push(String(error)));

  for (const arxivId of arxivIds) {
    if (interrupted) break;
    console.log(`[live-audit] ${arxivId}: starting`);
    const result = {
      arxiv_id: arxivId,
      url: `https://arxiv.org/abs/${arxivId}`,
      started_at: new Date().toISOString(),
      success: false,
      errors: [],
    };
    report.results.push(result);
    persistReport();
    try {
      const saved = await saveFromExtension(context, extensionId, arxivId);
      Object.assign(result, {
        title: saved.title,
        quality_preview: saved.qualityPreview ?? null,
        library_item_id: saved.libraryItemId,
        job_id: saved.jobId,
        duplicate: saved.duplicate,
        popup_recovery_count: saved.popupRecoveryCount,
      });
      persistReport();
      console.log(`[live-audit] ${arxivId}: saved from extension (job=${saved.jobId ?? "none"})`);
      if (saved.jobId) {
        let final = await waitForJob(context.request, saved.jobId, ingestTimeoutMs, {
          allowWaitingInput: true,
        });
        if (final.status === "waiting_input") {
          result.section_selection_required = true;
          console.log(`[live-audit] ${arxivId}: selecting sections in viewer`);
          await chooseSectionsThroughViewer(page, saved.libraryItemId, result);
          final = await waitForJob(context.request, saved.jobId, ingestTimeoutMs);
        } else {
          result.section_selection_required = false;
        }
        result.ingest = final;
        persistReport();
        if (final.status !== "succeeded") {
          throw new Error(`ingest finished as ${final.status}: ${final.error ?? "unknown error"}`);
        }
      }
      console.log(`[live-audit] ${arxivId}: ingest complete`);

      if (reingestIds.has(arxivId)) {
        console.log(`[live-audit] ${arxivId}: reingesting through viewer`);
        await reingestThroughViewer(page, saved.libraryItemId, result);
        persistReport();
        console.log(`[live-audit] ${arxivId}: frontend reingest complete`);
      }

      const viewer = await getJson(
        context.request,
        `/api/library-items/${saved.libraryItemId}/viewer`,
      );
      result.revision = viewer.revision;
      result.info = viewer.info;
      result.translation_state = viewer.translation;
      await auditTranslationViewer(page, saved.libraryItemId, result);
      persistReport();
      console.log(`[live-audit] ${arxivId}: translation/layout audited`);
      await generateAndAuditArticle(page, saved.libraryItemId, result);
      persistReport();
      console.log(`[live-audit] ${arxivId}: article audited`);
      await downloadAndAuditJapanesePdf(page, saved.libraryItemId, result);
      persistReport();
      console.log(`[live-audit] ${arxivId}: Japanese PDF audited`);

      const brokenImages = [
        ...(result.translation?.images ?? []),
        ...(result.article?.images ?? []),
      ].filter((image) => !image.complete || image.natural_width <= 0 || image.natural_height <= 0);
      const layoutOffenders = [
        ...(result.translation?.layout?.offenders ?? []),
        ...(result.translation?.mobile_layout?.offenders ?? []),
        ...(result.article?.layout?.offenders ?? []),
        ...(result.japanese_pdf?.layout?.offenders ?? []),
      ];
      const leaks = [
        ...(result.translation?.latex_leaks ?? []),
        ...(result.article?.latex_leaks ?? []),
      ];
      const emptyMediaBlocks = (result.translation?.media_blocks ?? []).filter(
        (block) => block.images === 0 && block.tables === 0,
      );
      const invalidTables = (result.translation?.tables ?? []).filter(
        (table) => table.rows <= 0 || table.cells <= 0 || table.width <= 0,
      );
      if ((result.translation?.japanese_characters ?? 0) === 0) {
        throw new Error("translation view contains no Japanese text");
      }
      if ((result.article?.japanese_characters ?? 0) === 0) {
        throw new Error("article contains no Japanese text");
      }
      if (brokenImages.length > 0)
        throw new Error(`${brokenImages.length} images failed to render`);
      if (emptyMediaBlocks.length > 0) {
        throw new Error(
          `${emptyMediaBlocks.length} figure/table blocks have no rendered visual content`,
        );
      }
      if (invalidTables.length > 0) {
        throw new Error(`${invalidTables.length} HTML tables have no visible cell grid`);
      }
      if (layoutOffenders.length > 0) {
        throw new Error(`${layoutOffenders.length} possible layout overflow(s)`);
      }
      if (leaks.length > 0) throw new Error(`visible raw LaTeX commands: ${leaks.join(", ")}`);
      result.success = true;
      console.log(`[live-audit] ${arxivId}: PASS`);
    } catch (error) {
      result.errors.push(error instanceof Error ? (error.stack ?? error.message) : String(error));
      console.error(`[live-audit] ${arxivId}: FAIL`, error);
      await captureScreenshot(page, resolve(outputDir, "screenshots", `${arxivId}-failure.png`));
    } finally {
      result.browser_errors = browserErrors.splice(0);
      result.finished_at = new Date().toISOString();
      persistReport();
    }
  }
  await page.close();
} finally {
  report.finished_at = new Date().toISOString();
  report.summary = {
    total: report.results.length,
    passed: report.results.filter((result) => result.success).length,
    failed: report.results.filter((result) => !result.success).length,
  };
  persistReport();
  await context.close();
}

if (report.summary.failed > 0) process.exitCode = 1;
