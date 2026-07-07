// パイプライン表示マッピング(3a §5.4)。状態2 の進捗行とフッタ右端文言を導出する純粋関数。
import { formatCompletedAt } from "./format";

/** 進捗行の色調。呼び出し側が色/太さにマップする。 */
export type PipelineTone = "done" | "active" | "muted" | "warn";

export interface PipelineRow {
  label: string;
  tone: PipelineTone;
}

const DONE = "✓ 書誌";
const DONE_STRUCT = "✓ 構造化";

/** stage が complete/failed 以外(=処理中)か。ポーリング継続判定に使う。 */
export function isProcessingStage(stage: string): boolean {
  return stage !== "complete" && stage !== "failed";
}

/**
 * 状態2 の進捗行(3a §5.4)。stage/進捗% と失敗理由から行配列を返す。
 */
export function pipelineRows(
  stage: string,
  progressPct: number,
  failedReason?: string | null,
): PipelineRow[] {
  switch (stage) {
    case "queued":
    case "fetching":
      return [{ label: "書誌 取得中", tone: "active" }];
    case "parsing":
    case "structuring":
      return [
        { label: DONE, tone: "done" },
        { label: "構造化中", tone: "active" },
      ];
    case "translating_abstract":
    case "readable":
    case "translating_body":
      return [
        { label: DONE, tone: "done" },
        { label: DONE_STRUCT, tone: "done" },
        { label: `翻訳中 ${progressPct}%`, tone: "active" },
      ];
    case "complete":
      return [
        { label: DONE, tone: "done" },
        { label: DONE_STRUCT, tone: "done" },
        { label: "✓ 翻訳完了", tone: "done" },
      ];
    case "waiting_quota":
      return [
        { label: DONE, tone: "done" },
        { label: DONE_STRUCT, tone: "done" },
        { label: "翻訳待機中", tone: "muted" },
      ];
    case "failed":
      return [
        {
          label: `取り込み失敗 — ${failedReason ?? "原因不明"}`,
          tone: "warn",
        },
      ];
    default:
      return [{ label: "書誌 取得中", tone: "active" }];
  }
}

export interface RecentPipelineInput {
  stage: string;
  progress_pct: number;
}

/**
 * フッタ「直近の取り込み」右端の文言(3a §5.4・§5.6)。
 * complete→完了時刻の相対表記 / failed→「失敗」 / それ以外→処理中の日本語。
 */
export function footerRightText(
  pipeline: RecentPipelineInput,
  completedAt: string | null | undefined,
  now: Date = new Date(),
): string {
  switch (pipeline.stage) {
    case "complete":
      return completedAt ? formatCompletedAt(completedAt, now) : "完了";
    case "failed":
      return "失敗";
    case "queued":
    case "fetching":
      return "取得中";
    case "parsing":
    case "structuring":
      return "構造化中";
    case "waiting_quota":
      return "待機中";
    case "translating_abstract":
    case "readable":
    case "translating_body":
      return `翻訳中 ${pipeline.progress_pct}%`;
    default:
      return "取得中";
  }
}
