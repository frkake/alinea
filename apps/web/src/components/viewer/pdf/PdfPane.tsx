"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { viewerGetDocument } from "@yakudoku/api-client";
import { useViewerStore } from "@/stores/viewer-store";
import { usePdfViewStore } from "@/stores/pdf-view-store";
import type { DocumentResponse } from "@/components/viewer/document-types";
import { PdfToolbar } from "./PdfToolbar";
import { PdfCanvas, spreadPages } from "./PdfCanvas";
import { buildPdfSyncMap } from "./sync-map";
import { computeFitScale } from "./geometry";
import { usePdfDocumentContext } from "./use-pdf-document";

export interface PdfPaneProps {
  itemId: string;
  revisionId: string;
  /** URL `?page=` があれば採用済みの値、無ければ 1(2a §5.10 優先順②④)。 */
  initialPage: number;
  /** `viewer.last_position.mode==='pdf'` の場合の block_id(§5.10 優先順③)。 */
  lastPositionBlockId?: string | null;
  onOpenInTranslation: (blockId: string) => void;
}

function isEditableTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  const tag = target.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || target.isContentEditable;
}

/** PDF モード本文ペイン(2a §3.1・§5)。ツールバー+キャンバスを統括する。 */
export function PdfPane({
  itemId,
  revisionId,
  initialPage,
  lastPositionBlockId = null,
  onOpenInTranslation,
}: PdfPaneProps) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const pdf = usePdfDocumentContext();
  // 最初の render 時点で URL に page が付いていたか(優先順②)。以降は自分自身の
  // replace で常に付くようになるため、判定は初回のみキャプチャする(lazy init)。
  const [urlPageProvidedInitially] = useState(() => searchParams.get("page") != null);

  const page = usePdfViewStore((s) => s.page);
  const zoom = usePdfViewStore((s) => s.zoom);
  const fitMode = usePdfViewStore((s) => s.fitMode);
  const spread = usePdfViewStore((s) => s.spread);
  const selectedBlockId = usePdfViewStore((s) => s.selectedBlockId);
  const resetForItem = usePdfViewStore((s) => s.resetForItem);
  const setPage = usePdfViewStore((s) => s.setPage);
  const zoomIn = usePdfViewStore((s) => s.zoomIn);
  const zoomOut = usePdfViewStore((s) => s.zoomOut);
  const setFitMode = usePdfViewStore((s) => s.setFitMode);
  const toggleSpread = usePdfViewStore((s) => s.toggleSpread);
  const selectBlock = usePdfViewStore((s) => s.selectBlock);

  const setCurrentBlock = useViewerStore((s) => s.setCurrentBlock);
  const pendingScroll = useViewerStore((s) => s.pendingScrollTarget);
  const consumeScroll = useViewerStore((s) => s.consumeScroll);

  useEffect(() => {
    resetForItem(itemId, initialPage);
  }, [itemId, initialPage, resetForItem]);

  const docQuery = useQuery({
    queryKey: ["document", revisionId],
    queryFn: async () =>
      (await viewerGetDocument({ path: { revision_id: revisionId }, throwOnError: true }))
        .data as DocumentResponse,
    staleTime: Infinity,
  });

  const syncMap = useMemo(() => buildPdfSyncMap(docQuery.data), [docQuery.data]);

  // 初期ページの優先順(2a §5.10): ①pendingScrollTarget ②URL page ③last_position
  // ④1。②④は呼び出し側が initialPage に解決済み(同期的に分かるため)。①③は document
  // (syncMap)が無ければ解決できないので、到着後に一度だけ再解決する。
  const consumedPendingRef = useRef(false);
  useEffect(() => {
    if (consumedPendingRef.current) return;
    if (!docQuery.data) return;
    if (pendingScroll) {
      consumedPendingRef.current = true;
      if (pendingScroll.kind === "block") {
        const target = syncMap.pageForBlock(pendingScroll.blockId);
        if (target != null) {
          setPage(target);
          selectBlock(pendingScroll.blockId);
        }
      } else {
        const target = syncMap.firstPageOfSection(pendingScroll.sectionId);
        if (target != null) setPage(target);
      }
      consumeScroll();
    } else if (!urlPageProvidedInitially && lastPositionBlockId) {
      consumedPendingRef.current = true;
      const target = syncMap.pageForBlock(lastPositionBlockId);
      if (target != null) setPage(target);
    }
  }, [
    docQuery.data,
    pendingScroll,
    syncMap,
    setPage,
    selectBlock,
    consumeScroll,
    urlPageProvidedInitially,
    lastPositionBlockId,
  ]);

  // URL の ?page= と同期(§1.1・§5.5 決定)。ページ変更のたびに replace(履歴を汚さない)。
  useEffect(() => {
    const urlPage = searchParams.get("page");
    if (urlPage === String(page)) return;
    const params = new URLSearchParams(searchParams);
    params.set("mode", "pdf");
    params.set("page", String(page));
    router.replace(`/papers/${itemId}?${params.toString()}`, { scroll: false });
    // eslint-disable-next-line react-hooks/exhaustive-deps -- page 変化のみで発火する
  }, [page, itemId]);

  // ページ変更の副作用(§5.5): 先頭ブロックを currentBlockId に(読書位置保存は shell 側)。
  useEffect(() => {
    if (!docQuery.data) return;
    const blockId = syncMap.firstBlockOnPage(page);
    if (!blockId) return;
    setCurrentBlock(blockId, syncMap.sectionForBlock(blockId) ?? "");
  }, [page, docQuery.data, syncMap, setCurrentBlock]);

  const [containerSize, setContainerSize] = useState({ width: 866, height: 800 });
  const [pageSizePt, setPageSizePt] = useState({ width: 612, height: 792 });

  const resolvedScale = fitMode
    ? computeFitScale(fitMode, containerSize.width, containerSize.height, pageSizePt.width, pageSizePt.height)
    : zoom;

  // フィット中は zoom フィールドを鏡映しておく(手動ズームへ移る時に現在の見た目から継続する)。
  useEffect(() => {
    if (fitMode) usePdfViewStore.setState({ zoom: resolvedScale });
  }, [fitMode, resolvedScale]);

  const displayPages = spreadPages(page, pdf.numPages, spread);
  const sync = syncMap.pageToSection(page);

  const handleOpenInTranslation = () => {
    const target = selectedBlockId ?? syncMap.firstBlockOnPage(page);
    if (target) onOpenInTranslation(target);
  };

  const stepPage = (direction: number) => {
    const step = spread ? 2 : 1;
    const pageCount = pdf.numPages;
    const next = direction > 0 ? page + step : page - step;
    setPage(Math.max(1, Math.min(pageCount ?? next, next)));
  };

  // 2a 固有キー(§5.5): ←/→/Home/End/j/k はページ移動、Esc は bbox 選択解除。
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey || e.isComposing) return;
      if (isEditableTarget(e.target)) return;
      const pageCount = pdf.numPages;
      const step = spread ? 2 : 1;
      switch (e.key) {
        case "ArrowLeft":
        case "PageUp":
        case "k":
          e.preventDefault();
          setPage(Math.max(1, page - step));
          break;
        case "ArrowRight":
        case "PageDown":
        case " ":
        case "j":
          e.preventDefault();
          if (e.key === " " && e.shiftKey) {
            setPage(Math.max(1, page - step));
          } else {
            setPage(Math.min(pageCount ?? page, page + step));
          }
          break;
        case "Home":
          e.preventDefault();
          setPage(1);
          break;
        case "End":
          if (pageCount) {
            e.preventDefault();
            setPage(pageCount);
          }
          break;
        case "Escape": {
          // shell の優先順(viewer-shell §10): 選択メニュー・論文内検索が開いていれば
          // それらを閉じるのが shell 側の別リスナーの責務。ここでは両方閉じている
          // 場合のみ bbox 選択を解除する(§5.5 決定)。
          const vs = useViewerStore.getState();
          if (selectedBlockId && !vs.selection && !vs.searchOpen) selectBlock(null);
          break;
        }
        default:
          break;
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [page, spread, pdf.numPages, selectedBlockId, setPage, selectBlock]);

  return (
    <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column", overflow: "hidden" }}>
      <PdfToolbar
        page={page}
        pageCount={pdf.numPages}
        zoomPct={pdf.loading ? null : Math.round(resolvedScale * 100)}
        fitMode={fitMode}
        spread={spread}
        syncDisplay={sync?.display ?? null}
        loading={pdf.loading || !docQuery.data}
        onPageChange={setPage}
        onZoomIn={zoomIn}
        onZoomOut={zoomOut}
        onFitModeChange={setFitMode}
        onToggleSpread={toggleSpread}
        onOpenInTranslation={handleOpenInTranslation}
      />
      <PdfCanvas
        displayPages={displayPages}
        scale={resolvedScale}
        getPage={pdf.getPage}
        syncMap={syncMap}
        selectedBlockId={selectedBlockId}
        onSelectBlock={(hit) => selectBlock(hit?.blockId ?? null)}
        onOpenInTranslation={onOpenInTranslation}
        onPageStep={stepPage}
        onPageSizeResolved={setPageSizePt}
        onResize={setContainerSize}
        loading={pdf.loading || !docQuery.data}
        error={pdf.error}
        onRetry={pdf.retry}
      />
    </div>
  );
}
