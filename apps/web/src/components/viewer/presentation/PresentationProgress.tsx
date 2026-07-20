"use client";

import { useRef, useState } from "react";
import type { Problem } from "@alinea/api-client";
import { ProgressBar } from "@/components/ui/ProgressBar";
import { useJobEvents } from "@/hooks/useJobEvents";
import { stageLabel } from "@/components/viewer/presentation/types";

/**
 * スライド生成の進捗表示(Task 30 §3)。共通 `useJobEvents` を使い、SSE 切断時は
 * 既存の polling fallback へ自動で移る(useJobEvents 内)。stage は日本語ラベルへ変換して
 * 表示し、失敗時は「直前に見えていた stage」を onError の第 2 引数として渡す
 * (失敗段階の表示に使う)。再読込後も同じ job_id を渡し続けることで active job を追跡する。
 */
export interface PresentationProgressProps {
  jobId: string;
  /** ジョブ成功。呼び出し側が最新成果物を再取得する。 */
  onDone: () => void;
  /** ジョブ失敗。problem と、失敗時点の stage(未取得なら null)を渡す。 */
  onError: (problem: Partial<Problem>, stage: string | null) => void;
}

export function PresentationProgress({ jobId, onDone, onError }: PresentationProgressProps) {
  const [progressPct, setProgressPct] = useState(0);
  const [stage, setStage] = useState<string | null>(null);
  // 最新 stage を ref にも控え、error コールバック時に「失敗した stage」として渡す。
  const stageRef = useRef<string | null>(null);

  useJobEvents(jobId, {
    onProgress: (e) => {
      setProgressPct(e.progress_pct);
      const nextStage = e.stage ?? null;
      stageRef.current = nextStage;
      setStage(nextStage);
    },
    onDone: () => onDone(),
    onError: (problem) => onError(problem, stageRef.current),
  });

  return (
    <div
      style={{ display: "flex", flexDirection: "column", gap: 10, alignItems: "center" }}
      role="status"
      aria-live="polite"
    >
      <span style={{ fontSize: 12, color: "var(--pr-a)" }}>
        ✦ {stageLabel(stage)}… {progressPct}%
      </span>
      <div style={{ width: "100%" }}>
        <ProgressBar value={progressPct} color="accent" />
      </div>
      <span style={{ fontSize: 10.5, color: "var(--pr-text-muted)" }}>
        他の画面を開いても生成は続きます
      </span>
    </div>
  );
}
