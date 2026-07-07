import { defineConfig } from "@playwright/test";
import { apiEnv, MOCK_BASE, NO_PROXY, repoRoot } from "./e2e/_env";

/**
 * 拡張 E2E / VR 構成(plans/12 §5.1)。ビルド済み拡張(.output/chrome-mv3)を
 * chromium の --load-extension で読み込む。ポップアップは chrome-extension://{id}/popup.html?
 * tab_url=… を直接開いて検証する(WXT_E2E=1 ビルド時のみ有効なフック)。
 *
 * バックエンド(api/web/mock/worker/seed)は web E2E と同一構成を globalSetup + webServer で用意する。
 * 拡張は headless では読み込めないため、fixtures.ts が `--headless=new` で persistent context を起動する。
 */
export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  retries: process.env.CI ? 2 : 0,
  workers: 1,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : [["list"]],
  globalSetup: "./e2e/global-env.ts",
  globalTeardown: "./e2e/global-teardown.ts",
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
    locale: "ja-JP",
    timezoneId: "Asia/Tokyo",
    trace: "retain-on-failure",
  },
  webServer: [
    {
      command: "uv run --no-sync python -m yakudoku_llm.testing.mock_server --port 8090",
      url: `${MOCK_BASE}/healthz`,
      cwd: repoRoot,
      timeout: 120_000,
      reuseExistingServer: !process.env.CI,
      env: { NO_PROXY, no_proxy: NO_PROXY },
    },
    {
      command: "uv run --no-sync uvicorn yakudoku_api.main:app --port 8000",
      url: "http://localhost:8000/api/healthz",
      cwd: repoRoot,
      timeout: 120_000,
      reuseExistingServer: !process.env.CI,
      env: apiEnv,
    },
    {
      // cwd は config ディレクトリ(apps/extension)のため、web アプリは filter で起動する。
      command: "pnpm --filter @yakudoku/web dev --port 3000",
      url: "http://localhost:3000/login",
      cwd: repoRoot,
      timeout: 120_000,
      reuseExistingServer: !process.env.CI,
      env: { API_INTERNAL_URL: "http://localhost:8000", NO_PROXY, no_proxy: NO_PROXY },
    },
  ],
});
