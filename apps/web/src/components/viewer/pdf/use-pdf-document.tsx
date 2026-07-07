"use client";

import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { useQuery } from "@tanstack/react-query";
import { loadPdfjs } from "@/lib/pdfjs";
import type { PdfViewportLike } from "./geometry";

/** pdf.js `RenderTask` の最小インターフェース(キャンセル可能な描画)。 */
export interface PdfRenderTask {
  promise: Promise<unknown>;
  cancel(): void;
}

/** pdf.js `PDFPageProxy` の最小インターフェース(テストで差し替え可能にする)。 */
export interface PdfPageLike {
  getViewport(params: { scale: number }): PdfViewportLike;
  render(params: { canvasContext: CanvasRenderingContext2D; viewport: PdfViewportLike }): PdfRenderTask;
}

export interface UsePdfDocumentResult {
  /** バイト取得中またはパース中。 */
  loading: boolean;
  /** 取得/パースに失敗(404 含む)。 */
  error: boolean;
  /** 404(アセット無し)。segment disabled 判定に使う(2a §5.3)。 */
  notFound: boolean;
  numPages: number | null;
  /** byteLength/1048576 を小数 1 桁丸め(2a §2.1)。未解決は null。 */
  fileSizeMb: number | null;
  getPage(pageNumber: number): Promise<PdfPageLike>;
  retry(): void;
}

const qk = {
  pdfData: (paperId: string) => ["pdf-data", paperId] as const,
};

/**
 * PDF 本体の取得(fetch→ArrayBuffer)と pdfjs `PDFDocumentProxy` の生成・破棄(2a §2.1・§2.2)。
 * `enabled=false` の間はフェッチしない(PDF モード表示時のみ本体取得。§2.1 決定)。
 */
export function usePdfDocument(paperId: string | null, enabled: boolean): UsePdfDocumentResult {
  const pdfQuery = useQuery({
    queryKey: qk.pdfData(paperId ?? ""),
    queryFn: async () => {
      const res = await fetch(`/api/papers/${paperId}/pdf`, { credentials: "include" });
      if (!res.ok) {
        const err = new Error(`pdf fetch failed: ${res.status}`) as Error & { status?: number };
        err.status = res.status;
        throw err;
      }
      return res.arrayBuffer();
    },
    enabled: Boolean(paperId) && enabled,
    staleTime: Infinity,
    gcTime: 10 * 60_000,
    retry: false,
  });

  const docRef = useRef<{ getPage(n: number): Promise<PdfPageLike>; destroy(): void } | null>(null);
  const [state, setState] = useState<{ loading: boolean; error: boolean; numPages: number | null }>({
    loading: true,
    error: false,
    numPages: null,
  });

  useEffect(() => {
    let cancelled = false;
    docRef.current?.destroy();
    docRef.current = null;

    const bytes = pdfQuery.data;
    if (!bytes) {
      setState({ loading: pdfQuery.isLoading || pdfQuery.isFetching, error: pdfQuery.isError, numPages: null });
      return;
    }

    setState({ loading: true, error: false, numPages: null });
    void (async () => {
      try {
        const pdfjs = await loadPdfjs();
        // pdf.js はワーカーへ転送する際に ArrayBuffer を detach しうるため複製を渡す
        // (react-query のキャッシュ本体は保持し続けたい。2a §2.2)。
        const data = bytes.slice(0);
        const proxy = await pdfjs.getDocument({ data }).promise;
        if (cancelled) {
          void proxy.destroy();
          return;
        }
        docRef.current = proxy as unknown as { getPage(n: number): Promise<PdfPageLike>; destroy(): void };
        setState({ loading: false, error: false, numPages: proxy.numPages });
      } catch {
        if (!cancelled) setState({ loading: false, error: true, numPages: null });
      }
    })();

    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- pdfQuery.data の参照変化のみで再構築する
  }, [pdfQuery.data]);

  useEffect(
    () => () => {
      docRef.current?.destroy();
      docRef.current = null;
    },
    [],
  );

  const fileSizeMb = pdfQuery.data
    ? Math.round((pdfQuery.data.byteLength / 1_048_576) * 10) / 10
    : null;

  const getPage = useMemo(
    () =>
      async (pageNumber: number): Promise<PdfPageLike> => {
        if (!docRef.current) throw new Error("pdf document is not loaded yet");
        return docRef.current.getPage(pageNumber);
      },
    [],
  );

  const notFound = (pdfQuery.error as (Error & { status?: number }) | null)?.status === 404;

  return {
    loading: state.loading,
    error: state.error || pdfQuery.isError,
    notFound,
    numPages: state.numPages,
    fileSizeMb,
    getPage,
    retry: () => void pdfQuery.refetch(),
  };
}

const PdfDocumentContext = createContext<UsePdfDocumentResult | null>(null);

export interface PdfDocumentProviderProps {
  paperId: string;
  children: ReactNode;
}

/** PdfPane / PdfSidebar が兄弟同士で同じ pdf.js ドキュメントを共有するための Provider。 */
export function PdfDocumentProvider({ paperId, children }: PdfDocumentProviderProps) {
  const value = usePdfDocument(paperId, true);
  return <PdfDocumentContext.Provider value={value}>{children}</PdfDocumentContext.Provider>;
}

export function usePdfDocumentContext(): UsePdfDocumentResult {
  const ctx = useContext(PdfDocumentContext);
  if (!ctx) throw new Error("usePdfDocumentContext は PdfDocumentProvider の内側で使う");
  return ctx;
}
