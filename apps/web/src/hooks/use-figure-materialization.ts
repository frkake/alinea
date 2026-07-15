"use client";

import { useCallback, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { figuresMaterializeDeferred, type Problem } from "@alinea/api-client";
import { useJobEvents } from "@/hooks/useJobEvents";

export type FigureMaterializationStatus = "idle" | "pending" | "succeeded" | "error";

export interface UseFigureMaterializationInput {
  itemId: string;
  revisionId: string;
  blockId: string;
}

interface TrackingState {
  identity: string;
  jobId: string | null;
  status: FigureMaterializationStatus;
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
 * Load a deferred figure (one that was skipped past the per-document budget)
 * on demand.  Starts a budget-raising reingest job and tracks it to completion,
 * then refreshes the document/viewer so the newly materialized image appears.
 */
export function useFigureMaterialization({
  itemId,
  revisionId,
  blockId,
}: UseFigureMaterializationInput) {
  const queryClient = useQueryClient();
  const identity = JSON.stringify([itemId, revisionId, blockId]);
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
    const submittedIdentity = identity;
    submitting.current = submittedIdentity;
    setTracking({ identity, jobId: null, status: "pending", error: null });
    try {
      const response = await figuresMaterializeDeferred({
        path: { library_item_id: itemId, block_id: blockId },
        throwOnError: true,
      });
      if (identityRef.current !== submittedIdentity) return;
      const jobId = response.data.job_id ?? null;
      if (jobId === null) {
        // Already materialized — just refresh so the image shows.
        setTracking({ identity, jobId: null, status: "succeeded", error: null });
        void invalidate();
        return;
      }
      setTracking((previous) =>
        previous.identity === submittedIdentity ? { ...previous, jobId } : previous,
      );
    } catch (problem) {
      if (identityRef.current !== submittedIdentity) return;
      setTracking({
        identity: submittedIdentity,
        jobId: null,
        status: "error",
        error: problemMessage(problem, "画像の読み込みを開始できませんでした"),
      });
    } finally {
      if (submitting.current === submittedIdentity) submitting.current = null;
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [blockId, current.jobId, identity, itemId]);

  const invalidate = useCallback(async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["document", revisionId] }),
      queryClient.invalidateQueries({ queryKey: ["viewer", itemId] }),
      queryClient.invalidateQueries({ queryKey: ["figures", revisionId] }),
    ]);
  }, [itemId, queryClient, revisionId]);

  const activeContext = current.jobId ? { identity, jobId: current.jobId } : null;

  useJobEvents(current.jobId, {
    onDone: () => {
      if (!activeContext || identityRef.current !== activeContext.identity) return;
      setTracking((previous) =>
        previous.identity === activeContext.identity && previous.jobId === activeContext.jobId
          ? { ...previous, jobId: null, status: "succeeded", error: null }
          : previous,
      );
      void invalidate();
    },
    onError: (problem) => {
      if (!activeContext || identityRef.current !== activeContext.identity) return;
      setTracking((previous) =>
        previous.identity === activeContext.identity && previous.jobId === activeContext.jobId
          ? {
              ...previous,
              jobId: null,
              status: "error",
              error: problemMessage(problem, "画像の読み込みに失敗しました"),
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
