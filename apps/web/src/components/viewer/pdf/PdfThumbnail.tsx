"use client";

import { useEffect, useRef, useState, type RefObject } from "react";
import type { PdfPageLike } from "./use-pdf-document";

const THUMB_WIDTH = 112;
const THUMB_HEIGHT = 145;

export interface PdfThumbnailProps {
  pageNumber: number;
  selected: boolean;
  onClick: () => void;
  getPage: (pageNumber: number) => Promise<PdfPageLike>;
  /** サムネイルリストのスクロールコンテナ(IntersectionObserver の root)。 */
  scrollRootRef: RefObject<HTMLElement | null>;
}

/** ページサムネイル 1 枚(2a §4.2.2)。可視範囲付近のみ pdf.js でレンダリングする。 */
export function PdfThumbnail({ pageNumber, selected, onClick, getPage, scrollRootRef }: PdfThumbnailProps) {
  const wrapRef = useRef<HTMLButtonElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [visible, setVisible] = useState(false);
  const [rendered, setRendered] = useState(false);

  useEffect(() => {
    const el = wrapRef.current;
    const root = scrollRootRef.current;
    if (!el) return;
    const observer = new IntersectionObserver(
      (entries) => {
        const entry = entries[0];
        if (entry?.isIntersecting) setVisible(true);
      },
      { root, rootMargin: "400px 0px", threshold: 0 },
    );
    observer.observe(el);
    return () => observer.disconnect();
    // scrollRootRef.current はマウント後に決まるが、初回だけで十分(再生成は不要)。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!visible || rendered) return;
    let cancelled = false;
    void (async () => {
      try {
        const page = await getPage(pageNumber);
        const base = page.getViewport({ scale: 1 });
        const dpr = typeof window !== "undefined" ? window.devicePixelRatio || 1 : 1;
        const scale = (THUMB_WIDTH / base.width) * dpr;
        const viewport = page.getViewport({ scale });
        const canvas = canvasRef.current;
        if (!canvas || cancelled) return;
        canvas.width = Math.ceil(viewport.width);
        canvas.height = Math.ceil(viewport.height);
        const ctx = canvas.getContext("2d");
        if (!ctx) return;
        await page.render({ canvasContext: ctx, viewport }).promise;
        if (!cancelled) setRendered(true);
      } catch {
        /* レンダー失敗はスケルトンのまま留める */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [visible, rendered, pageNumber, getPage]);

  return (
    <div
      style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 5, flex: "none" }}
    >
      <button
        ref={wrapRef}
        type="button"
        aria-label={`ページ ${pageNumber}`}
        aria-current={selected ? "true" : undefined}
        onClick={onClick}
        style={{
          width: THUMB_WIDTH,
          height: THUMB_HEIGHT,
          boxSizing: "border-box",
          background: "var(--pr-bg-card)",
          border: selected ? "2px solid var(--pr-acc)" : "1px solid var(--pr-border-control)",
          borderRadius: 3,
          boxShadow: selected ? "0 4px 12px rgba(28,30,34,0.12)" : undefined,
          padding: 0,
          cursor: "pointer",
          display: "grid",
          placeItems: "center",
          overflow: "hidden",
        }}
      >
        <canvas
          ref={canvasRef}
          style={{ width: "100%", height: "auto", display: rendered ? "block" : "none" }}
        />
        {rendered ? null : <ThumbnailSkeleton selected={selected} />}
      </button>
      {selected ? (
        <span
          style={{
            display: "inline-flex",
            alignItems: "center",
            justifyContent: "center",
            minWidth: 20,
            height: 16,
            borderRadius: 8,
            background: "var(--pr-acc)",
            color: "#FFFFFF",
            fontSize: 10,
            fontWeight: 700,
            padding: "0 4px",
          }}
        >
          {pageNumber}
        </span>
      ) : (
        <span style={{ fontSize: 10, color: "var(--pr-text-muted)" }}>{pageNumber}</span>
      )}
    </div>
  );
}

function ThumbnailSkeleton({ selected }: { selected: boolean }) {
  const bar = (w: string, bg?: string) => (
    <div style={{ width: w, height: 3, background: bg ?? "#E8E5DC", borderRadius: 1 }} />
  );
  return (
    <div
      aria-hidden
      style={{
        width: "100%",
        height: "100%",
        padding: selected ? "11px 9px" : "12px 10px",
        display: "flex",
        flexDirection: "column",
        gap: 3,
        boxSizing: "border-box",
      }}
    >
      {bar("90%")}
      {bar(selected ? "100%" : "100%", selected ? "var(--pr-acc-s)" : undefined)}
      <div style={{ height: 26, background: "var(--pr-bg-inset)", borderRadius: 2, margin: "4px 0" }} />
      {bar("100%")}
      {bar("85%")}
      {bar(selected ? "88%" : "92%", selected ? "var(--pr-acc-m)" : undefined)}
      {bar("60%")}
    </div>
  );
}
