/**
 * pdfjs-dist の読み込み・ワーカー設定(2a §3.1・§1.1)。SSR 不可のため呼び出し側で
 * `next/dynamic(ssr:false)` もしくはクライアントエフェクト内から呼ぶこと。
 * `new URL('pdfjs-dist/build/pdf.worker.min.mjs', import.meta.url)` は webpack5(Next.js
 * のデフォルトビルダー)の asset/resource 変換に依存する既知パターン(pdfjs-dist 公式
 * README のバンドラ向け手順)。
 */

import type * as PdfjsLib from "pdfjs-dist";

let cached: Promise<typeof PdfjsLib> | null = null;

/** pdfjs-dist をクライアント側で 1 回だけ読み込み、ワーカーを設定して返す。 */
export function loadPdfjs(): Promise<typeof PdfjsLib> {
  if (typeof window === "undefined") {
    return Promise.reject(new Error("loadPdfjs はブラウザ環境でのみ呼び出せます"));
  }
  if (!cached) {
    cached = import("pdfjs-dist").then((mod) => {
      mod.GlobalWorkerOptions.workerSrc = new URL(
        "pdfjs-dist/build/pdf.worker.min.mjs",
        import.meta.url,
      ).toString();
      return mod;
    });
  }
  return cached;
}
