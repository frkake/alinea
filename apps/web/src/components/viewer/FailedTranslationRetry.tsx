"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import type { TranslationUnitItem } from "@yakudoku/api-client";
import { useToast } from "@/components/ui/Toast";

const RETRYABLE_FAILURE_FLAGS = new Set(["placeholder_mismatch", "provider_refusal", "context_overflow"]);

export function isRetryableFailedUnit(unit: TranslationUnitItem | null | undefined): boolean {
  return Boolean(unit?.quality_flags?.some((flag) => RETRYABLE_FAILURE_FLAGS.has(flag)));
}

export function useFailedTranslationRetry({
  itemId,
  revisionId,
  translationSetId,
  unitMap,
}: {
  itemId: string;
  revisionId: string;
  translationSetId: string | null;
  unitMap: Map<string, TranslationUnitItem>;
}) {
  const toast = useToast();
  const qc = useQueryClient();
  const [retrying, setRetrying] = useState(false);
  const autoRetriedKeys = useRef<Set<string>>(new Set());

  const failedBlockIds = useMemo(
    () =>
      Array.from(unitMap.values())
        .filter(isRetryableFailedUnit)
        .map((unit) => unit.block_id)
        .sort(),
    [unitMap],
  );
  const failedKey = failedBlockIds.join(",");
  const failedCount = failedBlockIds.length;

  const retryFailed = useCallback(
    async (manual = true) => {
      if (!translationSetId || failedCount === 0) {
        if (manual) toast({ kind: "info", message: "再試行が必要な失敗はありません" });
        return;
      }
      setRetrying(true);
      try {
        const response = await fetch(`/api/translation-sets/${translationSetId}/retry-failed`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({}),
        });
        if (!response.ok) throw new Error(`retry failed: ${response.status}`);
        const data = (await response.json()) as { block_count?: number; job_ids?: string[] };
        await Promise.all([
          qc.invalidateQueries({ queryKey: ["units", revisionId] }),
          qc.invalidateQueries({ queryKey: ["viewer", itemId] }),
        ]);
        if (manual) {
          const count = data.block_count ?? failedCount;
          toast({ kind: "info", message: `${count}件の失敗翻訳を再試行しています` });
        }
      } catch {
        if (manual) toast({ kind: "error", message: "失敗翻訳を再試行できませんでした" });
      } finally {
        setRetrying(false);
      }
    },
    [failedCount, itemId, qc, revisionId, toast, translationSetId],
  );

  useEffect(() => {
    if (!translationSetId || failedCount === 0 || !failedKey) return;
    const key = `${translationSetId}:${failedKey}`;
    if (autoRetriedKeys.current.has(key)) return;
    autoRetriedKeys.current.add(key);
    void retryFailed(false);
  }, [failedCount, failedKey, retryFailed, translationSetId]);

  return { failedCount, retrying, retryFailed };
}

export function FailedTranslationRetryBanner({
  failedCount,
  retrying,
  onRetry,
}: {
  failedCount: number;
  retrying: boolean;
  onRetry: () => void;
}) {
  if (failedCount <= 0) return null;
  return (
    <div
      style={{
        margin: "0 0 18px",
        padding: "10px 12px",
        border: "1px solid var(--pr-warn-border, var(--pr-border-card))",
        borderRadius: 6,
        background: "var(--pr-warn-bg, var(--pr-bg-muted))",
        color: "var(--pr-text-body)",
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        gap: 12,
        fontFamily: "var(--pr-font-ui)",
        fontSize: 12,
      }}
    >
      <span>{retrying ? "失敗した翻訳を再試行しています" : `${failedCount}件の翻訳に失敗しています`}</span>
      <button
        type="button"
        onClick={onRetry}
        disabled={retrying}
        style={{ border: "none", background: "transparent", color: "var(--pr-acc)", cursor: "pointer" }}
      >
        失敗分を再試行
      </button>
    </div>
  );
}
