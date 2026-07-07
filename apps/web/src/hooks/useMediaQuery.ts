"use client";

import { useEffect, useState } from "react";

/**
 * モバイル縮退の判定基準幅(mobile.md §2 決定)。
 * 本来は packages/tokens に置く値だが、本タスクは tokens 編集禁止(読み取り専用)のため
 * web 側ローカル定数として保持する(deviations 参照)。
 */
export const MOBILE_MAX_WIDTH = 767;

/**
 * `matchMedia` + `change` 購読によるメディアクエリ判定(mobile.md §2)。
 * SSR ではデスクトップとして描画し、ハイドレーション後に反映する
 * (初回フレームのちらつきは許容。mobile.md §6-3 の決定)。
 */
export function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(false);

  useEffect(() => {
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") return;
    const mql = window.matchMedia(query);
    setMatches(mql.matches);
    const onChange = (e: MediaQueryListEvent) => setMatches(e.matches);
    mql.addEventListener("change", onChange);
    return () => mql.removeEventListener("change", onChange);
  }, [query]);

  return matches;
}

/** モバイル縮退レイアウトの判定(< 768px)。mobile.md §2 の唯一の JS 判定源。 */
export function useIsMobile(): boolean {
  return useMediaQuery(`(max-width: ${MOBILE_MAX_WIDTH}px)`);
}
