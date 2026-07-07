"use client";

import { useEffect, useRef, useState, type CSSProperties, type MouseEvent, type ReactNode } from "react";
import { EmptyState } from "@/components/ui/EmptyState";
import { bboxToViewportRect, viewportToTopDownPt, type PdfViewportLike } from "./geometry";
import type { PdfPageLike } from "./use-pdf-document";
import type { PdfSyncMap, SyncBlockHit } from "./sync-map";

/**
 * 見開きページ番号の組(2a §4.2.4 決定)。1 ページ目は単独右置き
 * (`[null, 1]`)、末尾が奇数個で相方が無い場合は単独左置き(`[n, null]`)。
 */
export function spreadPages(page: number, numPages: number | null, spread: boolean): (number | null)[] {
  if (!spread) return [page];
  if (page <= 1) return [null, 1];
  const left = page % 2 === 0 ? page : page - 1;
  const right = left + 1;
  if (numPages != null && right > numPages) return [left, null];
  return [left, right];
}

export interface PdfCanvasProps {
  displayPages: (number | null)[];
  scale: number;
  getPage: (pageNumber: number) => Promise<PdfPageLike>;
  syncMap: PdfSyncMap;
  selectedBlockId: string | null;
  onSelectBlock: (hit: SyncBlockHit | null) => void;
  onOpenInTranslation: (blockId: string) => void;
  /** フィットモード時、実測ページ寸法(pt, scale=1)を親へ返す(ズーム%表示・再計算に使う)。 */
  onPageSizeResolved?: (sizePt: { width: number; height: number }) => void;
  loading: boolean;
  error: boolean;
  onRetry: () => void;
  onResize?: (size: { width: number; height: number }) => void;
}

/**
 * dark: #14171B(`--pr-bg-canvas`)/light: #DDDAD1(plans/08 の --pr-bg-canvas とは
 * 実測が異なるため component 内定数で保持。2a §4)。
 */
const CANVAS_BG_CLASS = "yk-pdf-canvas-bg";

export function PdfCanvas({
  displayPages,
  scale,
  getPage,
  syncMap,
  selectedBlockId,
  onSelectBlock,
  onOpenInTranslation,
  onPageSizeResolved,
  loading,
  error,
  onRetry,
  onResize,
}: PdfCanvasProps) {
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el || !onResize) return;
    const ro = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (!entry) return;
      onResize({ width: entry.contentRect.width, height: entry.contentRect.height });
    });
    ro.observe(el);
    onResize({ width: el.clientWidth, height: el.clientHeight });
    return () => ro.disconnect();
  }, [onResize]);

  if (error) {
    return (
      <div
        className={CANVAS_BG_CLASS}
        style={{ flex: 1, display: "grid", placeItems: "center" }}
      >
        <EmptyState title="PDF を読み込めませんでした" action={{ label: "再試行", onClick: onRetry }} />
      </div>
    );
  }

  return (
    <div
      ref={scrollRef}
      className={CANVAS_BG_CLASS}
      style={{
        flex: 1,
        overflow: "auto",
        display: "flex",
        justifyContent: "center",
        alignItems: "flex-start",
        paddingTop: 20,
        paddingBottom: 20,
      }}
    >
      {loading ? (
        <LoadingPagePlaceholder />
      ) : (
        <div style={{ display: "flex", gap: 16 }}>
          {displayPages.map((p, idx) =>
            p == null ? (
              <div key={`empty-${idx}`} aria-hidden />
            ) : (
              <PdfPageLayer
                key={p}
                pageNumber={p}
                scale={scale}
                getPage={getPage}
                syncMap={syncMap}
                selectedBlockId={selectedBlockId}
                onSelectBlock={onSelectBlock}
                onOpenInTranslation={onOpenInTranslation}
                onPageSizeResolved={onPageSizeResolved}
              />
            ),
          )}
        </div>
      )}
    </div>
  );
}

function LoadingPagePlaceholder() {
  return (
    <div
      style={{
        width: 700,
        height: 906,
        flex: "none",
        background: "var(--pr-bg-card)",
        boxShadow: "0 6px 28px rgba(28,30,34,0.22)",
        display: "grid",
        placeItems: "center",
        color: "var(--pr-text-muted)",
        fontSize: 11.5,
      }}
    >
      PDF を読み込んでいます…
    </div>
  );
}

interface PdfPageLayerProps {
  pageNumber: number;
  scale: number;
  getPage: (pageNumber: number) => Promise<PdfPageLike>;
  syncMap: PdfSyncMap;
  selectedBlockId: string | null;
  onSelectBlock: (hit: SyncBlockHit | null) => void;
  onOpenInTranslation: (blockId: string) => void;
  onPageSizeResolved?: (sizePt: { width: number; height: number }) => void;
}

/** PDF ページ 1 枚(2a §4.2.4)。canvas 描画 + bbox オーバーレイ層。 */
function PdfPageLayer({
  pageNumber,
  scale,
  getPage,
  syncMap,
  selectedBlockId,
  onSelectBlock,
  onOpenInTranslation,
  onPageSizeResolved,
}: PdfPageLayerProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const [viewport, setViewport] = useState<PdfViewportLike | null>(null);
  const renderTaskRef = useRef<{ cancel(): void } | null>(null);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const page = await getPage(pageNumber);
        if (cancelled) return;
        const vp1 = page.getViewport({ scale: 1 });
        onPageSizeResolved?.({ width: vp1.width, height: vp1.height });
        const dpr = typeof window !== "undefined" ? window.devicePixelRatio || 1 : 1;
        const vp = page.getViewport({ scale });
        const renderVp = page.getViewport({ scale: scale * dpr });
        const canvas = canvasRef.current;
        if (!canvas) return;
        canvas.width = Math.ceil(renderVp.width);
        canvas.height = Math.ceil(renderVp.height);
        canvas.style.width = `${Math.ceil(vp.width)}px`;
        canvas.style.height = `${Math.ceil(vp.height)}px`;
        const ctx = canvas.getContext("2d");
        if (!ctx) return;
        renderTaskRef.current?.cancel();
        const task = page.render({ canvasContext: ctx, viewport: renderVp });
        renderTaskRef.current = task;
        await task.promise;
        if (!cancelled) setViewport(vp);
      } catch {
        /* キャンセル・描画失敗は無視(次の描画サイクルに委ねる) */
      }
    })();
    return () => {
      cancelled = true;
      renderTaskRef.current?.cancel();
    };
  }, [pageNumber, scale, getPage, onPageSizeResolved]);

  const onCanvasClick = (e: MouseEvent<HTMLDivElement>) => {
    if (!viewport || !wrapRef.current) return;
    const rect = wrapRef.current.getBoundingClientRect();
    const cssX = e.clientX - rect.left;
    const cssY = e.clientY - rect.top;
    const [xPt, yPt] = viewportToTopDownPt(viewport, cssX, cssY);
    const hit = syncMap.blockAtPoint(pageNumber, xPt, yPt);
    onSelectBlock(hit);
  };

  const selectedHit =
    viewport && selectedBlockId
      ? syncMap.blocksOnPage(pageNumber).find((b) => b.blockId === selectedBlockId)
      : null;
  const selectedDisplay = selectedHit ? syncMap.displayForBlock(selectedHit.blockId) : null;

  return (
    <div
      ref={wrapRef}
      data-pdf-page={pageNumber}
      onClick={onCanvasClick}
      style={{
        position: "relative",
        flex: "none",
        background: "var(--pr-bg-card)",
        boxShadow: "0 6px 28px rgba(28,30,34,0.22)",
        cursor: viewport ? "pointer" : "default",
      }}
    >
      <canvas ref={canvasRef} />
      {viewport && selectedHit && selectedDisplay ? (
        <PdfBboxHighlight viewport={viewport} bbox={selectedHit.bbox}>
          <PdfSyncChip
            display={selectedDisplay}
            onClick={(e) => {
              e.stopPropagation();
              onOpenInTranslation(selectedHit.blockId);
            }}
          />
        </PdfBboxHighlight>
      ) : null}
    </div>
  );
}

interface PdfBboxHighlightProps {
  viewport: PdfViewportLike;
  bbox: readonly [number, number, number, number];
  children?: ReactNode;
}

/** 選択 bbox のハイライト矩形(2a §4.2.4)。padding 相当 ±3px 外側拡張。 */
export function PdfBboxHighlight({ viewport, bbox, children }: PdfBboxHighlightProps) {
  const rect = bboxToViewportRect(viewport, bbox);
  const style: CSSProperties = {
    position: "absolute",
    left: rect.left - 4,
    top: rect.top - 3,
    width: rect.right - rect.left + 8,
    height: rect.bottom - rect.top + 6,
    background: "var(--pr-acc-s)",
    outline: "1.5px solid var(--pr-acc)",
    borderRadius: 2,
    pointerEvents: "none",
  };
  return (
    <div style={style} data-testid="pdf-bbox-highlight">
      {children}
    </div>
  );
}

interface PdfSyncChipProps {
  display: string;
  onClick: (e: MouseEvent) => void;
}

/** フローティングチップ「≒ §2.2 ¶2 — 訳文で見る →」(2a §4.2.4)。 */
export function PdfSyncChip({ display, onClick }: PdfSyncChipProps) {
  return (
    <button
      type="button"
      onClick={onClick}
      style={{
        position: "absolute",
        top: -23,
        right: 0,
        display: "inline-flex",
        alignItems: "center",
        gap: 5,
        height: 20,
        padding: "0 8px",
        background: "var(--pr-acc)",
        color: "#FFFFFF",
        border: "none",
        borderRadius: 4,
        fontSize: 10,
        fontWeight: 600,
        fontFamily: "var(--pr-font-ui)",
        boxShadow: "0 6px 16px rgba(28,30,34,0.25)",
        cursor: "pointer",
        pointerEvents: "auto",
      }}
    >
      ≒ {display} — 訳文で見る →
    </button>
  );
}
