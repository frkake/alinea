import { spawn, spawnSync } from "node:child_process";
import { mkdirSync, openSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { NO_PROXY, repoRoot, workerEnv, workerLogDir, workerPidFile } from "./_env";

/**
 * 拡張 E2E のグローバル前処理:
 * (1) WXT_E2E=1 で拡張をビルド(popup.html?tab_url= フックと API base を有効化)、
 * (2) Rectified Flow シードを --reset で投入、(3) レート制限キーをクリア、
 * (4) BulkWorker / InteractiveWorker を spawn。DB/Redis(docker)は起動済み前提。
 */
async function globalSetup(): Promise<void> {
  mkdirSync(workerLogDir, { recursive: true });

  // 1) 拡張ビルド(WXT_E2E=1)。
  const build = spawnSync("pnpm", ["exec", "wxt", "build"], {
    cwd: join(repoRoot, "apps", "extension"),
    env: { ...workerEnv, WXT_E2E: "1" },
    stdio: "inherit",
  });
  if (build.status !== 0) throw new Error(`wxt build failed (exit ${build.status ?? "signal"})`);

  // 2) シード投入。
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
  if (seed.status !== 0) throw new Error(`seed failed (exit ${seed.status ?? "signal"})`);

  // 3) レート制限キーをクリア(auth/email/request 5/600s の枯渇回避)。
  spawnSync(
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

  // 4) ワーカー spawn。
  const pids: number[] = [];
  for (const name of ["BulkWorker", "InteractiveWorker"] as const) {
    const out = openSync(join(workerLogDir, `${name}.log`), "a");
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
  console.log(`[xt-e2e] built extension + seeded + spawned workers: ${pids.join(", ")}`);
}

export default globalSetup;
