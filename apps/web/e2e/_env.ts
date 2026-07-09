import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));

/** モノレポのルート(uv / seed / mock を実行する cwd)。 */
export const repoRoot = resolve(__dirname, "..", "..", "..");

/** globalSetup が起動した arq ワーカー PID を書き出すファイル(teardown が読む)。 */
export const workerPidFile = resolve(__dirname, ".pids.json");

/** ワーカーの起動ログ出力先。 */
export const workerLogDir = resolve(__dirname, ".logs");

/** ローカル接続はプロキシを迂回する(企業プロキシ環境。MEMORY / plans/00)。 */
export const NO_PROXY = "localhost,127.0.0.1";

export const MOCK_BASE = "http://localhost:8090";

/**
 * arq ワーカーが読む環境変数。取り込み翻訳・要約は決定的 FakeLLM(§8.1)を使い、arXiv 取得は
 * モックサーバへ向ける。DATABASE_URL / REDIS_URL は repoRoot の .env から読む。
 */
export const workerEnv: NodeJS.ProcessEnv = {
  ...process.env,
  NO_PROXY,
  no_proxy: NO_PROXY,
  ALINEA_FAKE_LLM: "1",
  ALINEA_ARXIV_BASE_URL: `${MOCK_BASE}/arxiv`,
};
