import { existsSync, readFileSync, rmSync } from "node:fs";
import { workerPidFile } from "./_env";

/** globalSetup が spawn した arq ワーカーを停止する(PID を控えて SIGTERM)。 */
async function globalTeardown(): Promise<void> {
  if (!existsSync(workerPidFile)) return;
  let pids: number[] = [];
  try {
    pids = JSON.parse(readFileSync(workerPidFile, "utf-8")) as number[];
  } catch {
    pids = [];
  }
  for (const pid of pids) {
    try {
      process.kill(pid, "SIGTERM");
    } catch {
      /* 既に終了済み */
    }
  }
  try {
    rmSync(workerPidFile);
  } catch {
    /* noop */
  }
  console.log(`[e2e] stopped workers: ${pids.join(", ")}`);
}

export default globalTeardown;
