"use client";

import { useEffect, useRef } from "react";
import { jobsGet, type JobOut, type Problem } from "@alinea/api-client";

/**
 * ジョブ進捗 SSE の共通フック(plans/09-screens/1h §2.1 #13・§2.3 決定)。
 * `GET /api/jobs/{job_id}/events` に接続し、`progress` / `done` / `error` を配線する。
 * EventSource が 3 回連続で接続失敗した場合は `GET /api/jobs/{job_id}` を 2,000ms ポーリングへ
 * 切り替え、ジョブ終端(succeeded/failed)で停止する(ポーリング中は EventSource へ戻らない)。
 */

export interface JobProgressEvent {
  job_id: string;
  status: string;
  stage?: string | null;
  progress_pct: number;
  detail?: string | null;
  readable_upto?: string | null;
}

export interface UseJobEventsOptions<TResult = unknown> {
  onProgress?: (event: JobProgressEvent) => void;
  onDone?: (result: TResult | null) => void;
  onError?: (problem: Partial<Problem>) => void;
}

const POLL_INTERVAL_MS = 2000;
const MAX_CONNECT_FAILURES = 3;

export function useJobEvents<TResult = unknown>(
  jobId: string | null | undefined,
  options: UseJobEventsOptions<TResult>,
): void {
  // 最新のコールバックだけを参照し、接続を貼り直さない(useSSE と同方針)。
  const optionsRef = useRef(options);
  optionsRef.current = options;

  useEffect(() => {
    if (!jobId) return;

    let closed = false;
    let source: EventSource | null = null;
    let failures = 0;
    let pollTimer: ReturnType<typeof setTimeout> | null = null;

    const stop = () => {
      closed = true;
      if (pollTimer) clearTimeout(pollTimer);
      source?.close();
      source = null;
    };

    const poll = () => {
      if (closed) return;
      pollTimer = setTimeout(() => {
        void jobsGet({ path: { job_id: jobId } }).then(
          (res) => {
            if (closed) return;
            const job: JobOut | undefined = res.data;
            if (!job) {
              poll();
              return;
            }
            if (job.status === "succeeded") {
              const result = (job as unknown as { result?: TResult }).result ?? null;
              stop();
              optionsRef.current.onDone?.(result);
              return;
            }
            if (job.status === "failed") {
              stop();
              optionsRef.current.onError?.((job.error as Partial<Problem> | undefined) ?? {});
              return;
            }
            optionsRef.current.onProgress?.({
              job_id: job.id,
              status: job.status,
              stage: job.stage,
              progress_pct: job.progress_pct,
              detail: job.detail,
            });
            poll();
          },
          () => {
            if (!closed) poll();
          },
        );
      }, POLL_INTERVAL_MS);
    };

    if (typeof EventSource === "undefined") {
      // SSE 非対応環境(テスト・一部ブラウザ): 即ポーリングへ(P3: 黙って壊れない)。
      poll();
      return stop;
    }

    const onProgress = (e: MessageEvent<string>) => {
      failures = 0;
      try {
        optionsRef.current.onProgress?.(JSON.parse(e.data) as JobProgressEvent);
      } catch {
        /* 破損フレームは無視(P3)。 */
      }
    };

    const onDone = (e: MessageEvent<string>) => {
      stop();
      try {
        const data = JSON.parse(e.data) as { result?: TResult };
        optionsRef.current.onDone?.(data.result ?? null);
      } catch {
        optionsRef.current.onDone?.(null);
      }
    };

    // SSE の `error` イベントはサーバー送出の意味的エラー(MessageEvent・data あり)と
    // ネイティブ接続断(data なし)の両方で同名で発火する(仕様上の既知の重複)。
    // data があれば意味的エラーとして即終端、無ければ接続断として再試行/ポーリング切替を判定する。
    const onErrorEvent = (e: Event) => {
      const msg = e as MessageEvent<string>;
      if (msg.data) {
        stop();
        try {
          optionsRef.current.onError?.(JSON.parse(msg.data) as Partial<Problem>);
        } catch {
          optionsRef.current.onError?.({});
        }
        return;
      }
      failures += 1;
      if (failures >= MAX_CONNECT_FAILURES) {
        source?.close();
        source = null;
        poll();
      }
    };

    source = new EventSource(`/api/jobs/${jobId}/events`, { withCredentials: true });
    source.addEventListener("progress", onProgress as EventListener);
    source.addEventListener("done", onDone as EventListener);
    source.addEventListener("error", onErrorEvent);

    return stop;
  }, [jobId]);
}
