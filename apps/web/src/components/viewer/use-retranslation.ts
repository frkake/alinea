"use client";

import { useCallback, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { translationsRetranslate, type Problem } from "@alinea/api-client";
import { useJobEvents } from "@/hooks/useJobEvents";

export type RetranslationStatus = "idle" | "pending" | "succeeded" | "error";

export interface UseRetranslationResult {
  mutate: (instruction?: string) => void;
  isPending: boolean;
  error: string | null;
}

function problemMessage(problem: unknown, fallback: string): string {
  if (problem != null && typeof problem === "object") {
    const value = problem as Partial<Problem>;
    if (typeof value.detail === "string" && value.detail) return value.detail;
    if (typeof value.title === "string" && value.title) return value.title;
  }
  return fallback;
}

/**
 * 単一ユニットの再翻訳フック(Task 6)。
 * POST /api/translation-units/{unit_id}/retranslate → job_id を SSE で待機 → units query を invalidate。
 * 実行中は isPending=true でボタンを disabled にする。
 */
export function useRetranslation(
  unitId: string | null | undefined,
  queryKey: readonly unknown[],
): UseRetranslationResult {
  const queryClient = useQueryClient();
  const [jobId, setJobId] = useState<string | null>(null);
  const [status, setStatus] = useState<RetranslationStatus>("idle");
  const [error, setError] = useState<string | null>(null);
  const submitting = useRef(false);

  const mutate = useCallback(
    async (instruction?: string) => {
      if (!unitId || submitting.current) return;
      submitting.current = true;
      setStatus("pending");
      setError(null);
      try {
        const response = await translationsRetranslate({
          path: { unit_id: unitId },
          body: { instruction: instruction ?? null },
          throwOnError: true,
        });
        setJobId(response.data.job_id);
      } catch (problem) {
        setStatus("error");
        setError(problemMessage(problem, "再翻訳を開始できませんでした"));
        submitting.current = false;
      }
    },
    [unitId],
  );

  useJobEvents(jobId, {
    onProgress: () => {
      setStatus("pending");
    },
    onDone: async () => {
      setJobId(null);
      setStatus("succeeded");
      submitting.current = false;
      await queryClient.invalidateQueries({ queryKey: queryKey as unknown[] });
    },
    onError: (problem) => {
      setJobId(null);
      setStatus("error");
      setError(problemMessage(problem, "再翻訳に失敗しました"));
      submitting.current = false;
    },
  });

  return {
    mutate,
    isPending: status === "pending",
    error,
  };
}
