import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));

/** モノレポのルート(uv / seed / mock / wxt build を実行する cwd)。 */
export const repoRoot = resolve(__dirname, "..", "..", "..");

/** 拡張ビルド出力(chromium --load-extension で読み込む)。 */
export const extensionDist = resolve(__dirname, "..", ".output", "chrome-mv3");

export const workerPidFile = resolve(__dirname, ".pids.json");
export const workerLogDir = resolve(__dirname, ".logs");

export const NO_PROXY = "localhost,127.0.0.1";
export const MOCK_BASE = "http://localhost:8090";

/** arq ワーカー用環境変数(取り込みは決定的 FakeLLM + モック arXiv)。 */
export const workerEnv: NodeJS.ProcessEnv = {
  ...process.env,
  NO_PROXY,
  no_proxy: NO_PROXY,
  ALINEA_FAKE_LLM: "1",
  ALINEA_ARXIV_BASE_URL: `${MOCK_BASE}/arxiv`,
};

/** API プロセス用環境変数(§8.4 のモック LLM 向けベース URL + BYOK 用テスト Fernet 鍵)。 */
export const apiEnv: Record<string, string> = {
  ...(process.env as Record<string, string>),
  NO_PROXY,
  no_proxy: NO_PROXY,
  ALINEA_ARXIV_BASE_URL: `${MOCK_BASE}/arxiv`,
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
  ALINEA_KEY_ENCRYPTION_SECRET: "FeSAew6Uy-pkWOhrdXpCqC5Dmpqg5fJVWo0DKJiGze8=",
};
