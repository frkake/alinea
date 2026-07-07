// ツールバーバッジの状態(3a §5.5・Task 33 契約)。純粋関数(browser 非依存)でテスト可能。
// 色値は @yakudoku/tokens(AMBER=#C49432 / GREEN=#659471。hex 直書きしない)。
import { AMBER, GREEN } from "@yakudoku/tokens";

export interface BadgeState {
  color: string;
  text: string;
}

/** ジョブの終端状態(バッジ判定で「処理中でない」とみなす)。 */
const TERMINAL = new Set(["succeeded", "failed", "complete"]);

/**
 * 処理中ジョブあり → 琥珀ドット ● / 直近完了 → 緑 ✓ / 未読あり → 琥珀ドット ● /
 * それ以外 → バッジなし。
 */
export function badgeStateFor(
  jobs: Array<{ status: string }>,
  opts: { unread?: number; justCompleted?: boolean } = {},
): BadgeState {
  const active = jobs.some((job) => !TERMINAL.has(job.status));
  if (active) return { color: AMBER, text: "●" };
  if (opts.justCompleted) return { color: GREEN, text: "✓" };
  if ((opts.unread ?? 0) > 0) return { color: AMBER, text: "●" };
  return { color: "", text: "" };
}
