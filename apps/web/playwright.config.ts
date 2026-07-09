import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig } from "@playwright/test";

/**
 * Playwright E2E / VR 構成(plans/12 §4.1 確定値)。
 *
 * - ブラウザは Chromium のみ・1440×900・ja-JP・Asia/Tokyo(plans/00 §4.5・docs/09 §6)。
 * - webServer で モック LLM(8090)+ API(8000)+ web(3000)を起動する。
 * - arq ワーカーは待受ポートを持たないため webServer では起動できない。globalSetup
 *   (e2e/global-env.ts)が seed 投入と BulkWorker / InteractiveWorker の spawn を行い、
 *   globalTeardown(e2e/global-teardown.ts)で停止する(plans/12 §4.1 の決定に準拠)。
 * - LLM は §8.4 のモックサーバへ全 5 プロバイダのベース URL を上書きして向ける
 *   (運営キーは test-stub 固定)。取り込み翻訳・要約はワーカー側 ALINEA_FAKE_LLM=1。
 */

const __dirname = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(__dirname, "..", "..");

const MOCK_BASE = "http://localhost:8090";

/** ローカル接続はプロキシを迂回する(企業プロキシ環境。MEMORY / plans/00)。 */
const NO_PROXY = "localhost,127.0.0.1";

/**
 * API プロセスが読む環境変数。§8.4 のモック向けベース URL 群 + BYOK 暗号化鍵(テスト用固定
 * Fernet 鍵)。DATABASE_URL / REDIS_URL / S3 は repoRoot の .env から読む。
 */
const apiEnv: Record<string, string> = {
  NO_PROXY,
  no_proxy: NO_PROXY,
  ALINEA_ARXIV_BASE_URL: `${MOCK_BASE}/arxiv`,
  // 全 5 プロバイダの運営キーを test-stub にし、ベース URL をモックへ向ける(§8.4)。
  OPENAI_API_KEY: "test-stub",
  ANTHROPIC_API_KEY: "test-stub",
  GEMINI_API_KEY: "test-stub",
  DEEPSEEK_API_KEY: "test-stub",
  XAI_API_KEY: "test-stub",
  ALINEA_OPENAI_BASE_URL: `${MOCK_BASE}/openai/v1`,
  ALINEA_ANTHROPIC_BASE_URL: `${MOCK_BASE}/anthropic`,
  ALINEA_GOOGLE_BASE_URL: `${MOCK_BASE}/google`,
  ALINEA_DEEPSEEK_BASE_URL: `${MOCK_BASE}/deepseek`,
  ALINEA_XAI_BASE_URL: `${MOCK_BASE}/xai/v1`,
  // BYOK キーストア(DbKeyStore)は起動時に Fernet 鍵を要求する。テスト固定鍵(本番は .env)。
  ALINEA_KEY_ENCRYPTION_SECRET: "FeSAew6Uy-pkWOhrdXpCqC5Dmpqg5fJVWo0DKJiGze8=",
};

export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  retries: process.env.CI ? 2 : 0,
  workers: 1, // シードを共有する統合 E2E のため直列実行(書き込み衝突回避)。
  reporter: process.env.CI
    ? [
        ["list"],
        ["html", { open: "never" }],
        ["json", { outputFile: "playwright-report/report.json" }],
      ]
    : [["list"], ["html", { open: "never" }]],
  globalSetup: "./e2e/global-env.ts",
  globalTeardown: "./e2e/global-teardown.ts",
  // VR 基準画像はコミット対象(単一プラットフォーム = CI と同じ linux)。
  snapshotPathTemplate: "{testDir}/__screenshots__/{arg}{ext}",
  expect: {
    timeout: 10_000,
    toHaveScreenshot: {
      maxDiffPixelRatio: 0.001,
      threshold: 0.2,
      animations: "disabled",
      caret: "hide",
    },
  },
  use: {
    baseURL: "http://localhost:3000",
    viewport: { width: 1440, height: 900 }, // docs/09 §6 基準ビューポート
    locale: "ja-JP",
    timezoneId: "Asia/Tokyo",
    trace: "retain-on-failure",
    deviceScaleFactor: 1, // VR 決定性(1 倍固定)。
  },
  projects: [
    { name: "setup", testMatch: /global\.setup\.ts/ },
    {
      name: "e2e",
      testDir: "./e2e/specs",
      dependencies: ["setup"],
      use: { storageState: "e2e/.auth/user.json" },
    },
    {
      name: "visual",
      testDir: "./e2e/vr",
      dependencies: ["setup"],
      use: { storageState: "e2e/.auth/user.json" },
    },
  ],
  webServer: [
    {
      command: "uv run --no-sync python -m alinea_llm.testing.mock_server --port 8090",
      url: `${MOCK_BASE}/healthz`,
      cwd: repoRoot,
      timeout: 120_000,
      reuseExistingServer: !process.env.CI,
      env: { NO_PROXY, no_proxy: NO_PROXY },
    },
    {
      command: "uv run --no-sync uvicorn alinea_api.main:app --port 8000",
      url: "http://localhost:8000/api/healthz",
      cwd: repoRoot,
      timeout: 120_000,
      reuseExistingServer: !process.env.CI,
      env: apiEnv,
    },
    {
      command: "pnpm dev --port 3000",
      url: "http://localhost:3000/login",
      timeout: 120_000,
      reuseExistingServer: !process.env.CI,
      env: { API_INTERNAL_URL: "http://localhost:8000", NO_PROXY, no_proxy: NO_PROXY },
    },
  ],
});
