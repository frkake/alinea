"use client";

import { useEffect, useRef } from "react";
import { libraryItemsSavePosition } from "@alinea/api-client";
import { useViewerStore } from "@/stores/viewer-store";
import type { ViewerMode } from "@/components/viewer/ViewerShell";

const DEBOUNCE_MS = 5_000;

/**
 * 読書位置の自動保存(viewer-shell §8.1)。
 * `currentBlockId` の変化を 5,000ms デバウンスで `PUT …/position` へ送る。
 * `pagehide` 時は `navigator.sendBeacon` で即時送信(離脱でも取りこぼさない)。
 * M0 は位置保存のみ(読書時間計測 useReadingSession は対象外)。
 */
export function useReadingPosition(params: {
  itemId: string;
  revisionId: string;
  mode: ViewerMode;
}): void {
  const { itemId, revisionId, mode } = params;
  const currentBlockId = useViewerStore((s) => s.currentBlockId);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  // pagehide ハンドラが常に最新値を読めるよう ref に保持。
  const latest = useRef({ blockId: currentBlockId, revisionId, mode });
  latest.current = { blockId: currentBlockId, revisionId, mode };

  useEffect(() => {
    if (!currentBlockId) return;
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => {
      void libraryItemsSavePosition({
        path: { item_id: itemId },
        body: { revision_id: revisionId, block_id: currentBlockId, mode },
      });
    }, DEBOUNCE_MS);
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, [currentBlockId, itemId, revisionId, mode]);

  useEffect(() => {
    const onPageHide = () => {
      const { blockId, revisionId: rev, mode: m } = latest.current;
      if (!blockId) return;
      void fetch(`/api/library-items/${itemId}/position`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        keepalive: true,
        body: JSON.stringify({ revision_id: rev, block_id: blockId, mode: m }),
      }).catch(() => undefined);
    };
    window.addEventListener("pagehide", onPageHide);
    return () => {
      window.removeEventListener("pagehide", onPageHide);
    };
  }, [itemId]);
}
