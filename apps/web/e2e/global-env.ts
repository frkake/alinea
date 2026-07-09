import { spawn, spawnSync } from "node:child_process";
import { existsSync, mkdirSync, openSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { NO_PROXY, repoRoot, workerEnv, workerLogDir, workerPidFile } from "./_env";

/**
 * E2E グローバル前処理(plans/12 §4.1)。webServer(mock/api/web)とは別に、
 * (1) Rectified Flow シードを --reset で投入し(§14・全 spec の共通データ源)、
 * (2) arq BulkWorker / InteractiveWorker を spawn する(取り込みジョブは alinea:bulk キュー・
 *     plans/01 §4。E2E では両ワーカーを起動する)。PID は teardown 用に書き出す。
 *
 * 決定: 実 PostgreSQL / Redis(docker compose)は起動済み前提(CI の e2e ジョブが
 * `docker compose up -d --wait db redis minio ... mailpit` を先に実行する)。
 */
async function globalSetup(): Promise<void> {
  mkdirSync(workerLogDir, { recursive: true });

  // 1) シード投入(--reset で本 seed 由来データのみ入れ替え。決定的にするため毎回)。
  const seed = spawnSync(
    "uv",
    [
      "run",
      "--no-sync",
      "python",
      "-m",
      "alinea_api.seed",
      "--sample",
      "rectified-flow",
      "--reset",
    ],
    { cwd: repoRoot, env: workerEnv, stdio: "inherit" },
  );
  if (seed.status !== 0) {
    throw new Error(`seed failed (exit ${seed.status ?? "signal"})`);
  }

  // 1b) レート制限キーをクリア(auth/email/request は 5/600s。連続実行・リトライで
  //     枯渇するとメールリンク認証が落ちるため、実行ごとにリセットする。テスト専用)。
  const clearRl = spawnSync(
    "uv",
    [
      "run",
      "--no-sync",
      "python",
      "-c",
      "import os,redis;r=redis.from_url(os.environ.get('REDIS_URL','redis://localhost:6379/0'));[r.delete(k) for k in r.scan_iter('rl:*')]",
    ],
    { cwd: repoRoot, env: workerEnv, stdio: "inherit" },
  );
  if (clearRl.status !== 0) {
    console.warn("[e2e] failed to clear rate-limit keys (auth may be flaky under repeated runs)");
  }

  // 2) ワーカー spawn(detached。teardown で PID を kill)。
  const workers = ["BulkWorker", "InteractiveWorker"] as const;
  const pids: number[] = [];
  for (const name of workers) {
    const logPath = join(workerLogDir, `${name}.log`);
    const out = openSync(logPath, "a");
    const child = spawn("uv", ["run", "--no-sync", "arq", `alinea_worker.main.${name}`], {
      cwd: repoRoot,
      env: { ...workerEnv, NO_PROXY, no_proxy: NO_PROXY },
      detached: true,
      stdio: ["ignore", out, out],
    });
    child.unref();
    if (child.pid) pids.push(child.pid);
  }
  writeFileSync(workerPidFile, JSON.stringify(pids), "utf-8");
  console.log(`[e2e] seeded + spawned workers: ${pids.join(", ")}`);

  if (!existsSync(join(repoRoot, ".env"))) {
    console.warn("[e2e] .env not found at repo root; relying on default DATABASE_URL/REDIS_URL");
  }
}

export default globalSetup;
