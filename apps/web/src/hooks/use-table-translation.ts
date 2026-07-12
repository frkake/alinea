"use client";

import { useCallback, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { translationsSectionTranslate, type Problem } from "@alinea/api-client";
import { useJobEvents } from "@/hooks/useJobEvents";

export type TableTranslationStatus = "idle" | "pending" | "succeeded" | "error";

export interface UseTableTranslationInput {
  itemId: string;
  revisionId: string;
  style: string;
  translationSetId: string | null;
  sectionId: string;
  blockId: string;
}

interface TrackingState {
  identity: string;
  jobId: string | null;
  status: TableTranslationStatus;
  error: string | null;
}

interface TableTranslationResult {
  fallback?: number;
}

function problemMessage(problem: unknown, fallback: string): string {
  if (problem != null && typeof problem === "object") {
    const value = problem as Partial<Problem>;
    if (typeof value.detail === "string" && value.detail) return value.detail;
    if (typeof value.title === "string" && value.title) return value.title;
  }
  return fallback;
}

export function useTableTranslation({
  itemId,
  revisionId,
  style,
  translationSetId,
  sectionId,
  blockId,
}: UseTableTranslationInput) {
  const queryClient = useQueryClient();
  const identity = JSON.stringify([
    itemId,
    revisionId,
    style,
    translationSetId,
    sectionId,
    blockId,
  ]);
  const identityRef = useRef(identity);
  identityRef.current = identity;
  const [tracking, setTracking] = useState<TrackingState>(() => ({
    identity,
    jobId: null,
    status: "idle",
    error: null,
  }));
  const current =
    tracking.identity === identity
      ? tracking
      : ({ identity, jobId: null, status: "idle", error: null } satisfies TrackingState);
  const submitting = useRef<string | null>(null);

  const start = useCallback(async () => {
    if (submitting.current === identity || current.jobId) return;
    if (!translationSetId) {
      setTracking({
        identity,
        jobId: null,
        status: "error",
        error: "翻訳セットを読み込めませんでした",
      });
      return;
    }
    const submittedIdentity = identity;
    submitting.current = submittedIdentity;
    setTracking({ identity, jobId: null, status: "pending", error: null });
    try {
      const response = await translationsSectionTranslate({
        path: { set_id: translationSetId, section_id: sectionId },
        body: { block_id: blockId },
        throwOnError: true,
      });
      if (identityRef.current !== submittedIdentity) return;
      setTracking((previous) =>
        previous.identity === submittedIdentity
          ? { ...previous, jobId: response.data.job_id }
          : previous,
      );
    } catch (problem) {
      if (identityRef.current !== submittedIdentity) return;
      setTracking({
        identity: submittedIdentity,
        jobId: null,
        status: "error",
        error: problemMessage(problem, "表の翻訳を開始できませんでした"),
      });
    } finally {
      if (submitting.current === submittedIdentity) submitting.current = null;
    }
  }, [blockId, current.jobId, identity, sectionId, translationSetId]);

  const activeContext = current.jobId
    ? {
        identity,
        jobId: current.jobId,
        itemId,
        revisionId,
        style,
        sectionId,
      }
    : null;

  useJobEvents<TableTranslationResult>(current.jobId, {
    onProgress: () => {
      if (!activeContext || identityRef.current !== activeContext.identity) return;
      setTracking((previous) =>
        previous.identity === activeContext.identity && previous.jobId === activeContext.jobId
          ? { ...previous, status: "pending" }
          : previous,
      );
    },
    onDone: (result) => {
      if (!activeContext || identityRef.current !== activeContext.identity) return;
      const fallback =
        result && typeof result.fallback === "number" && Number.isFinite(result.fallback)
          ? result.fallback
          : 0;
      setTracking((previous) =>
        previous.identity === activeContext.identity && previous.jobId === activeContext.jobId
          ? {
              ...previous,
              jobId: null,
              status: fallback > 0 ? "error" : "succeeded",
              error: fallback > 0 ? "表の一部を翻訳できませんでした。再試行してください" : null,
            }
          : previous,
      );
      void Promise.all([
        queryClient.invalidateQueries({
          queryKey: [
            "units",
            activeContext.revisionId,
            activeContext.style,
            activeContext.sectionId,
          ],
          exact: true,
        }),
        queryClient.invalidateQueries({
          queryKey: ["document", activeContext.revisionId],
          exact: true,
        }),
        queryClient.invalidateQueries({
          queryKey: ["viewer", activeContext.itemId],
          exact: true,
        }),
      ]);
    },
    onError: (problem) => {
      if (!activeContext || identityRef.current !== activeContext.identity) return;
      setTracking((previous) =>
        previous.identity === activeContext.identity && previous.jobId === activeContext.jobId
          ? {
              ...previous,
              jobId: null,
              status: "error",
              error: problemMessage(problem, "表の翻訳に失敗しました"),
            }
          : previous,
      );
    },
  });

  return {
    status: current.status,
    error: current.error,
    start,
    retry: start,
  } as const;
}
