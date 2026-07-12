"use client";

import {
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type MouseEvent,
  type PointerEvent,
  type ReactNode,
} from "react";
import { EmptyState } from "@/components/ui/EmptyState";
import { loadPdfjs } from "@/lib/pdfjs";
import type { PdfSpreadFirstPageSide } from "@/stores/pdf-view-store";
import { bboxToViewportRect, viewportToTopDownPt, type PdfViewportLike } from "./geometry";
import type { PdfAnnotationLike, PdfPageLike } from "./use-pdf-document";
import type { PdfSyncMap, SyncBlockHit } from "./sync-map";

/**
 * 見開きページ番号の組(2a §4.2.4 決定)。1 ページ目は単独右置き
 * (`[null, 1]`)、末尾が奇数個で相方が無い場合は単独左置き(`[n, null]`)。
 */
export function spreadPages(
  page: number,
  numPages: number | null,
  spread: boolean,
  firstPageSide: PdfSpreadFirstPageSide = "right",
): (number | null)[] {
  if (!spread) return [page];
  if (firstPageSide === "right" && page <= 1) return [null, 1];
  const left =
    firstPageSide === "right"
      ? page % 2 === 0
        ? page
        : page - 1
      : page % 2 === 1
        ? page
        : page - 1;
  const right = left + 1;
  if (numPages != null && right > numPages) return [left, null];
  return [left, right];
}

export interface PdfCanvasProps {
  displayPages: (number | null)[];
  /** 複数ページ/見開きを縦に並べる場合のページ行。未指定時は displayPages を 1 行として扱う。 */
  pageGroups?: (number | null)[][];
  /** 対訳表示用。同じ pageNumber の別PDFを右側に描く。 */
  comparisonGetPage?: (pageNumber: number) => Promise<PdfPageLike>;
  comparisonPageCount?: number | null;
  comparisonSyncMap?: PdfSyncMap;
  activePage?: number;
  scale: number;
  getPage: (pageNumber: number) => Promise<PdfPageLike>;
  syncMap: PdfSyncMap;
  selectedBlockId: string | null;
  onSelectBlock: (hit: SyncBlockHit | null) => void;
  onOpenInTranslation: (blockId: string) => void;
  onPageStep?: (delta: number) => void;
  onWheelZoom?: (delta: number) => boolean | undefined;
  onVisiblePageChange?: (page: number) => void;
  /** フィットモード時、実測ページ寸法(pt, scale=1)を親へ返す(ズーム%表示・再計算に使う)。 */
  onPageSizeResolved?: (sizePt: { width: number; height: number }) => void;
  /** 見開きで空きページスロットを保持するための現在ページ寸法(css px)。 */
  pageSlotSize?: { width: number; height: number };
  loading: boolean;
  error: boolean;
  onRetry: () => void;
  onResize?: (size: { width: number; height: number }) => void;
}

/**
 * dark: #14171B(`--pr-bg-canvas`)/light: #DDDAD1(plans/08 の --pr-bg-canvas とは
 * 実測が異なるため component 内定数で保持。2a §4)。
 */
const CANVAS_BG_CLASS = "alinea-pdf-canvas-bg";
export const PDF_PAGE_GAP_PX = 16;
const PDF_CANVAS_PADDING_X_PX = 16;
const PDF_CANVAS_PADDING_Y_PX = 20;

interface WheelZoomAnchor {
  scale: number;
  clientX: number;
  clientY: number;
  rootViewportX: number;
  rootViewportY: number;
  scrollLeft: number;
  scrollTop: number;
  pageEl: HTMLElement | null;
  pageXRatio: number | null;
  pageYRatio: number | null;
  pageWidth: number | null;
}

function clampUnit(value: number): number {
  return Math.min(1, Math.max(0, value));
}

function pageLayerFromWheelEvent(root: HTMLElement, e: WheelEvent): HTMLElement | null {
  const candidates: Element[] = [];
  if (e.target instanceof Element) candidates.push(e.target);
  const pointed = document.elementFromPoint?.(e.clientX, e.clientY);
  if (pointed) candidates.push(pointed);

  for (const candidate of candidates) {
    const pageEl = candidate.closest<HTMLElement>(".alinea-pdf-page-layer");
    if (pageEl && root.contains(pageEl)) return pageEl;
  }
  return null;
}

function wheelZoomAnchorFromEvent(
  root: HTMLElement,
  e: WheelEvent,
  scale: number,
): WheelZoomAnchor {
  const rootRect = root.getBoundingClientRect();
  const anchor: WheelZoomAnchor = {
    scale,
    clientX: e.clientX,
    clientY: e.clientY,
    rootViewportX: e.clientX - rootRect.left,
    rootViewportY: e.clientY - rootRect.top,
    scrollLeft: root.scrollLeft,
    scrollTop: root.scrollTop,
    pageEl: null,
    pageXRatio: null,
    pageYRatio: null,
    pageWidth: null,
  };

  const pageEl = pageLayerFromWheelEvent(root, e);
  const pageRect = pageEl?.getBoundingClientRect();
  if (pageEl && pageRect && pageRect.width > 0 && pageRect.height > 0) {
    anchor.pageEl = pageEl;
    anchor.pageXRatio = clampUnit((e.clientX - pageRect.left) / pageRect.width);
    anchor.pageYRatio = clampUnit((e.clientY - pageRect.top) / pageRect.height);
    anchor.pageWidth = pageRect.width;
  }
  return anchor;
}

function shouldWaitForPageScale(anchor: WheelZoomAnchor, nextScale: number): boolean {
  if (!anchor.pageEl || !anchor.pageWidth || anchor.scale <= 0) return false;
  const pageRect = anchor.pageEl.getBoundingClientRect();
  if (pageRect.width <= 0) return false;
  const expectedRatio = nextScale / anchor.scale;
  const actualRatio = pageRect.width / anchor.pageWidth;
  return Number.isFinite(expectedRatio) && Math.abs(actualRatio - expectedRatio) > 0.02;
}

function keepWheelZoomAnchorUnderCursor(
  root: HTMLElement,
  anchor: WheelZoomAnchor,
  nextScale: number,
) {
  if (anchor.pageEl && anchor.pageXRatio != null && anchor.pageYRatio != null) {
    const pageRect = anchor.pageEl.getBoundingClientRect();
    if (pageRect.width > 0 && pageRect.height > 0) {
      const targetX = pageRect.left + pageRect.width * anchor.pageXRatio;
      const targetY = pageRect.top + pageRect.height * anchor.pageYRatio;
      root.scrollLeft += targetX - anchor.clientX;
      root.scrollTop += targetY - anchor.clientY;
      return;
    }
  }

  const ratio = anchor.scale > 0 ? nextScale / anchor.scale : 1;
  root.scrollLeft = (anchor.scrollLeft + anchor.rootViewportX) * ratio - anchor.rootViewportX;
  root.scrollTop = (anchor.scrollTop + anchor.rootViewportY) * ratio - anchor.rootViewportY;
}

export function PdfCanvas({
  displayPages,
  pageGroups,
  comparisonGetPage,
  comparisonPageCount,
  comparisonSyncMap,
  activePage,
  scale,
  getPage,
  syncMap,
  selectedBlockId,
  onSelectBlock,
  onOpenInTranslation,
  onPageStep,
  onWheelZoom,
  onVisiblePageChange,
  onPageSizeResolved,
  pageSlotSize,
  loading,
  error,
  onRetry,
  onResize,
}: PdfCanvasProps) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const wheelStepAtRef = useRef(0);
  const wheelZoomAnchorRef = useRef<WheelZoomAnchor | null>(null);
  const wheelZoomRafRef = useRef<number | null>(null);
  const rafRef = useRef<number | null>(null);
  const suppressVisibleUntilRef = useRef(0);
  const lastVisiblePageRef = useRef<number | null>(null);
  const groupsKeyRef = useRef("");
  const groups = pageGroups ?? [displayPages];
  const groupsKey = groups.map((row) => row.map((p) => p ?? "_").join(",")).join("|");
  const comparisonSelect = useMemo(() => () => undefined, []);

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

  useEffect(() => {
    if (!activePage) return;
    const cameFromLocalScroll =
      lastVisiblePageRef.current === activePage && groupsKeyRef.current === groupsKey;
    groupsKeyRef.current = groupsKey;
    if (cameFromLocalScroll) return;
    const root = scrollRef.current;
    const pageEl = root?.querySelector<HTMLElement>(`[data-pdf-page="${activePage}"]`);
    suppressVisibleUntilRef.current = Date.now() + 450;
    if (typeof pageEl?.scrollIntoView === "function") {
      pageEl.scrollIntoView({ block: "nearest", behavior: "smooth" });
    }
  }, [activePage, groupsKey]);

  useEffect(() => {
    const root = scrollRef.current;
    if (!root || !onVisiblePageChange) return;

    const updateVisiblePage = () => {
      rafRef.current = null;
      if (Date.now() < suppressVisibleUntilRef.current) return;
      const rootRect = root.getBoundingClientRect();
      const centerY = rootRect.top + rootRect.height * 0.45;
      let best: { page: number; distance: number } | null = null;
      for (const el of Array.from(root.querySelectorAll<HTMLElement>("[data-pdf-page]"))) {
        const page = Number.parseInt(el.dataset.pdfPage ?? "", 10);
        if (!Number.isFinite(page)) continue;
        const rect = el.getBoundingClientRect();
        if (rect.bottom < rootRect.top || rect.top > rootRect.bottom) continue;
        const distance = Math.abs(rect.top + rect.height / 2 - centerY);
        if (!best || distance < best.distance) best = { page, distance };
      }
      if (best) {
        lastVisiblePageRef.current = best.page;
        onVisiblePageChange(best.page);
      }
    };

    const schedule = () => {
      if (rafRef.current != null) return;
      rafRef.current = window.requestAnimationFrame(updateVisiblePage);
    };
    root.addEventListener("scroll", schedule, { passive: true });
    return () => {
      root.removeEventListener("scroll", schedule);
      if (rafRef.current != null) window.cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    };
  }, [onVisiblePageChange, groupsKey]);

  useLayoutEffect(() => {
    const anchor = wheelZoomAnchorRef.current;
    if (!anchor || anchor.scale === scale) return;

    let cancelled = false;
    let attempts = 0;
    const adjust = () => {
      wheelZoomRafRef.current = null;
      if (cancelled) return;
      const root = scrollRef.current;
      const current = wheelZoomAnchorRef.current;
      if (!root || current !== anchor) return;
      if (attempts < 8 && shouldWaitForPageScale(anchor, scale)) {
        attempts += 1;
        wheelZoomRafRef.current = window.requestAnimationFrame(adjust);
        return;
      }

      keepWheelZoomAnchorUnderCursor(root, anchor, scale);
      wheelZoomAnchorRef.current = null;
    };

    if (wheelZoomRafRef.current != null) {
      window.cancelAnimationFrame(wheelZoomRafRef.current);
    }
    wheelZoomRafRef.current = window.requestAnimationFrame(adjust);
    return () => {
      cancelled = true;
      if (wheelZoomRafRef.current != null) {
        window.cancelAnimationFrame(wheelZoomRafRef.current);
        wheelZoomRafRef.current = null;
      }
    };
  }, [scale]);

  useEffect(() => {
    const root = scrollRef.current;
    if (!root) return;

    const onWheel = (e: WheelEvent) => {
      if (e.ctrlKey || e.metaKey) {
        e.preventDefault();
        if (!loading && onWheelZoom && e.deltaY !== 0) {
          const anchor = wheelZoomAnchorFromEvent(root, e, scale);
          wheelZoomAnchorRef.current = anchor;
          const changed = onWheelZoom(e.deltaY < 0 ? 1 : -1);
          if (changed === false && wheelZoomAnchorRef.current === anchor) {
            wheelZoomAnchorRef.current = null;
          }
        }
        return;
      }
      if (!onPageStep || loading) return;
      if (Math.abs(e.deltaY) < Math.abs(e.deltaX) || Math.abs(e.deltaY) < 24) return;
      const atTop = root.scrollTop <= 2;
      const atBottom = root.scrollTop + root.clientHeight >= root.scrollHeight - 2;
      const delta = e.deltaY > 0 ? 1 : -1;
      if ((delta > 0 && !atBottom) || (delta < 0 && !atTop)) return;
      const now = Date.now();
      if (now - wheelStepAtRef.current < 450) return;
      wheelStepAtRef.current = now;
      e.preventDefault();
      onPageStep(delta);
    };

    root.addEventListener("wheel", onWheel, { passive: false });
    return () => root.removeEventListener("wheel", onWheel);
  }, [loading, onPageStep, onWheelZoom, scale]);

  if (error) {
    return (
      <div className={CANVAS_BG_CLASS} style={{ flex: 1, display: "grid", placeItems: "center" }}>
        <EmptyState
          title="PDF を読み込めませんでした"
          action={{ label: "再試行", onClick: onRetry }}
        />
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
        padding: `${PDF_CANVAS_PADDING_Y_PX}px ${PDF_CANVAS_PADDING_X_PX}px`,
      }}
    >
      {loading ? (
        <LoadingPagePlaceholder />
      ) : (
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            gap: 18,
            alignItems: "center",
            width: "max-content",
            minWidth: "100%",
            margin: "0 auto",
          }}
        >
          {groups.map((row, rowIndex) => (
            <div
              key={`${row.map((p) => p ?? "_").join(",")}-${rowIndex}`}
              style={{
                display: "flex",
                gap: PDF_PAGE_GAP_PX,
                justifyContent: "center",
                alignItems: "flex-start",
                width: "max-content",
              }}
            >
              {row.map((p, idx) => {
                if (p == null) {
                  return (
                    <div
                      key={`empty-${rowIndex}-${idx}`}
                      aria-hidden
                      style={{
                        width: pageSlotSize?.width ?? 0,
                        height: pageSlotSize?.height ?? 0,
                        flex: "none",
                      }}
                    />
                  );
                }
                const comparisonPageAvailable =
                  comparisonGetPage && (comparisonPageCount == null || p <= comparisonPageCount);
                return (
                  <div
                    key={`slot-${p}`}
                    style={{
                      display: "flex",
                      gap: comparisonGetPage ? PDF_PAGE_GAP_PX : 0,
                      alignItems: "flex-start",
                    }}
                  >
                    <PdfPageLayer
                      key={`source-${p}`}
                      pageNumber={p}
                      scale={scale}
                      getPage={getPage}
                      syncMap={syncMap}
                      selectedBlockId={selectedBlockId}
                      onSelectBlock={onSelectBlock}
                      onOpenInTranslation={onOpenInTranslation}
                      onPageSizeResolved={onPageSizeResolved}
                    />
                    {comparisonGetPage ? (
                      comparisonPageAvailable ? (
                        <PdfPageLayer
                          key={`comparison-${p}`}
                          pageNumber={p}
                          scale={scale}
                          getPage={comparisonGetPage}
                          syncMap={comparisonSyncMap ?? syncMap}
                          selectedBlockId={null}
                          onSelectBlock={comparisonSelect}
                          onOpenInTranslation={() => undefined}
                          trackVisible={false}
                        />
                      ) : (
                        <div
                          key={`comparison-empty-${p}`}
                          aria-hidden
                          style={{
                            width: pageSlotSize?.width ?? 0,
                            height: pageSlotSize?.height ?? 0,
                            flex: "none",
                          }}
                        />
                      )
                    ) : null}
                  </div>
                );
              })}
            </div>
          ))}
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
  trackVisible?: boolean;
}

interface PdfAnnotationLinkItem {
  ann: PdfAnnotationLike;
  href: string;
  rect: { left: number; top: number; width: number; height: number };
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
  trackVisible = true,
}: PdfPageLayerProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const textLayerRef = useRef<HTMLDivElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const pointerRef = useRef<{ x: number; y: number; dragging: boolean }>({
    x: 0,
    y: 0,
    dragging: false,
  });
  const [viewport, setViewport] = useState<PdfViewportLike | null>(null);
  const [annotations, setAnnotations] = useState<PdfAnnotationLike[]>([]);
  const renderTaskRef = useRef<{ cancel(): void } | null>(null);
  const textLayerTaskRef = useRef<{ cancel(): void } | null>(null);
  const annotationLinks = useMemo(
    () => (viewport ? buildAnnotationLinkItems(viewport, annotations) : []),
    [annotations, viewport],
  );

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
        const cssWidth = vp.width;
        const cssHeight = vp.height;
        const wrap = wrapRef.current;
        if (wrap) {
          wrap.style.width = `${cssWidth}px`;
          wrap.style.height = `${cssHeight}px`;
          wrap.style.setProperty("--scale-factor", String(scale));
        }
        canvas.width = Math.ceil(renderVp.width);
        canvas.height = Math.ceil(renderVp.height);
        canvas.style.width = `${cssWidth}px`;
        canvas.style.height = `${cssHeight}px`;
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
          textLayerEl.style.setProperty("--scale-factor", String(scale));
          const textContent = await page.getTextContent({ includeMarkedContent: true });
          if (!cancelled) {
            const pdfjs = await loadPdfjs();
            if (cancelled) return;
            const TextLayer = (
              pdfjs as unknown as {
                TextLayer?: new (params: {
                  textContentSource: unknown;
                  container: HTMLElement;
                  viewport: PdfViewportLike;
                }) => { render(): Promise<unknown>; cancel(): void };
              }
            ).TextLayer;
            if (TextLayer) {
              const layer = new TextLayer({
                textContentSource: textContent,
                container: textLayerEl,
                viewport: vp,
              });
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

  const onDragStart = (e: MouseEvent<HTMLDivElement> | PointerEvent<HTMLDivElement>) => {
    if (typeof e.button === "number" && e.button !== 0) return;
    pointerRef.current = { x: e.clientX, y: e.clientY, dragging: false };
  };

  const onDragMove = (e: MouseEvent<HTMLDivElement> | PointerEvent<HTMLDivElement>) => {
    const state = pointerRef.current;
    if (state.dragging) return;
    if (Math.abs(e.clientX - state.x) > 4 || Math.abs(e.clientY - state.y) > 4) {
      state.dragging = true;
    }
  };

  const onCanvasClick = (e: MouseEvent<HTMLDivElement>) => {
    if (pointerRef.current.dragging) {
      pointerRef.current.dragging = false;
      return;
    }
    const selectedText = window.getSelection()?.toString().trim();
    if (selectedText) return;
    if (!viewport || !wrapRef.current) return;
    const rect = wrapRef.current.getBoundingClientRect();
    const cssX = e.clientX - rect.left;
    const cssY = e.clientY - rect.top;
    const link = annotationLinks.find((item) => pointInRect(item.rect, cssX, cssY));
    if (link) {
      openAnnotationHref(link.href, link.ann);
      return;
    }
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
      className="alinea-pdf-page-layer"
      data-pdf-page={trackVisible ? pageNumber : undefined}
      onPointerDown={onDragStart}
      onPointerMove={onDragMove}
      onMouseDown={onDragStart}
      onMouseMove={onDragMove}
      onClick={onCanvasClick}
      style={{
        position: "relative",
        flex: "none",
        background: "var(--pr-bg-card)",
        boxShadow: "0 6px 28px rgba(28,30,34,0.22)",
        cursor: viewport ? "text" : "default",
      }}
    >
      <canvas ref={canvasRef} />
      <div ref={textLayerRef} className="textLayer alinea-pdf-text-layer" aria-hidden />
      {annotationLinks.length > 0 ? <PdfAnnotationLinks links={annotationLinks} /> : null}
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

function buildAnnotationLinkItems(
  viewport: PdfViewportLike,
  annotations: PdfAnnotationLike[],
): PdfAnnotationLinkItem[] {
  return annotations
    .filter((ann) => ann.subtype === "Link" && ann.rect && annotationHref(ann))
    .map((ann) => ({
      ann,
      href: annotationHref(ann) ?? "",
      rect: annotationRectToViewportRect(viewport, ann.rect ?? []),
    }))
    .filter((item) => item.href && item.rect.width > 0 && item.rect.height > 0);
}

function pointInRect(rect: PdfAnnotationLinkItem["rect"], x: number, y: number): boolean {
  return (
    x >= rect.left && x <= rect.left + rect.width && y >= rect.top && y <= rect.top + rect.height
  );
}

function openAnnotationHref(href: string, annotation: PdfAnnotationLike) {
  if (annotation.newWindow === false) {
    window.location.assign(href);
    return;
  }
  window.open(href, "_blank", "noopener,noreferrer");
}

function PdfAnnotationLinks({ links }: { links: PdfAnnotationLinkItem[] }) {
  if (links.length === 0) return null;
  return (
    <div className="alinea-pdf-link-layer" aria-label="PDF リンク">
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
