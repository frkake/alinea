"use client";

import { useEffect, useMemo, useRef, useState, type CSSProperties, type MouseEvent, type ReactNode, type WheelEvent } from "react";
import { EmptyState } from "@/components/ui/EmptyState";
import { loadPdfjs } from "@/lib/pdfjs";
import { bboxToViewportRect, viewportToTopDownPt, type PdfViewportLike } from "./geometry";
import type { PdfAnnotationLike, PdfPageLike } from "./use-pdf-document";
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
  onPageStep?: (delta: number) => void;
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
  onPageStep,
  onPageSizeResolved,
  loading,
  error,
  onRetry,
  onResize,
}: PdfCanvasProps) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const wheelStepAtRef = useRef(0);

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

  const onWheel = (e: WheelEvent<HTMLDivElement>) => {
    if (!onPageStep || loading) return;
    if (Math.abs(e.deltaY) < Math.abs(e.deltaX) || Math.abs(e.deltaY) < 24) return;
    const el = scrollRef.current;
    if (!el) return;
    const atTop = el.scrollTop <= 2;
    const atBottom = el.scrollTop + el.clientHeight >= el.scrollHeight - 2;
    const delta = e.deltaY > 0 ? 1 : -1;
    if ((delta > 0 && !atBottom) || (delta < 0 && !atTop)) return;
    const now = Date.now();
    if (now - wheelStepAtRef.current < 450) return;
    wheelStepAtRef.current = now;
    e.preventDefault();
    onPageStep(delta);
  };

  return (
    <div
      ref={scrollRef}
      className={CANVAS_BG_CLASS}
      onWheel={onWheel}
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
  const textLayerRef = useRef<HTMLDivElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const [viewport, setViewport] = useState<PdfViewportLike | null>(null);
  const [annotations, setAnnotations] = useState<PdfAnnotationLike[]>([]);
  const renderTaskRef = useRef<{ cancel(): void } | null>(null);
  const textLayerTaskRef = useRef<{ cancel(): void } | null>(null);

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
        canvas.style.display = "block";
        const ctx = canvas.getContext("2d");
        if (!ctx) return;
        renderTaskRef.current?.cancel();
        const task = page.render({ canvasContext: ctx, viewport: renderVp });
        renderTaskRef.current = task;
        await task.promise;
        if (cancelled) return;
        setViewport(vp);

        const textLayerEl = textLayerRef.current;
        textLayerTaskRef.current?.cancel();
        textLayerTaskRef.current = null;
        if (textLayerEl && page.getTextContent) {
          textLayerEl.replaceChildren();
          const textContent = await page.getTextContent({ includeMarkedContent: true });
          if (!cancelled) {
            const pdfjs = await loadPdfjs();
            if (cancelled) return;
            const TextLayer = (pdfjs as unknown as { TextLayer?: new (params: { textContentSource: unknown; container: HTMLElement; viewport: PdfViewportLike }) => { render(): Promise<unknown>; cancel(): void } }).TextLayer;
            if (TextLayer) {
              const layer = new TextLayer({ textContentSource: textContent, container: textLayerEl, viewport: vp });
              textLayerTaskRef.current = layer;
              await layer.render();
            }
          }
        } else if (textLayerEl) {
          textLayerEl.replaceChildren();
        }

        if (page.getAnnotations) {
          const anns = await page.getAnnotations({ intent: "display" });
          if (!cancelled) setAnnotations(anns);
        } else {
          setAnnotations([]);
        }
      } catch {
        /* キャンセル・描画失敗は無視(次の描画サイクルに委ねる) */
      }
    })();
    return () => {
      cancelled = true;
      renderTaskRef.current?.cancel();
      textLayerTaskRef.current?.cancel();
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
      <div ref={textLayerRef} className="textLayer yk-pdf-text-layer" aria-hidden />
      {viewport ? <PdfAnnotationLinks viewport={viewport} annotations={annotations} /> : null}
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

function annotationHref(annotation: PdfAnnotationLike): string | null {
  const raw = annotation.url ?? annotation.unsafeUrl ?? null;
  if (!raw) return null;
  if (/^(https?:|mailto:)/i.test(raw)) return raw;
  return null;
}

function annotationRectToViewportRect(viewport: PdfViewportLike, rect: readonly number[]) {
  const [x0 = 0, y0 = 0, x1 = 0, y1 = 0] = rect;
  const p0 = viewport.convertToViewportPoint(x0, y0);
  const p1 = viewport.convertToViewportPoint(x1, y1);
  const vx0 = p0[0] ?? 0;
  const vy0 = p0[1] ?? 0;
  const vx1 = p1[0] ?? 0;
  const vy1 = p1[1] ?? 0;
  return {
    left: Math.min(vx0, vx1),
    top: Math.min(vy0, vy1),
    width: Math.abs(vx1 - vx0),
    height: Math.abs(vy1 - vy0),
  };
}

function PdfAnnotationLinks({
  viewport,
  annotations,
}: {
  viewport: PdfViewportLike;
  annotations: PdfAnnotationLike[];
}) {
  const links = useMemo(
    () =>
      annotations
        .filter((ann) => ann.subtype === "Link" && ann.rect && annotationHref(ann))
        .map((ann) => ({ ann, href: annotationHref(ann), rect: annotationRectToViewportRect(viewport, ann.rect ?? []) }))
        .filter((item) => item.href && item.rect.width > 0 && item.rect.height > 0),
    [annotations, viewport],
  );
  if (links.length === 0) return null;
  return (
    <div className="yk-pdf-link-layer" aria-label="PDF リンク">
      {links.map(({ ann, href, rect }, idx) => (
        <a
          key={`${href}-${idx}`}
          href={href ?? undefined}
          target={ann.newWindow === false ? undefined : "_blank"}
          rel={ann.newWindow === false ? undefined : "noreferrer"}
          aria-label={href ?? "PDF リンク"}
          onClick={(e) => e.stopPropagation()}
          style={{
            position: "absolute",
            left: rect.left,
            top: rect.top,
            width: rect.width,
            height: rect.height,
          }}
        />
      ))}
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
    zIndex: 3,
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
