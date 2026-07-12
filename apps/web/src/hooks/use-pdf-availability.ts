"use client";

import { useQuery } from "@tanstack/react-query";
import type { PdfDocumentMode } from "@/stores/pdf-view-store";
import type {
  PdfAssetIdentity,
  PdfTranslationStyle,
} from "@/components/viewer/pdf/use-pdf-document";

type PdfFetchVariant = Exclude<PdfDocumentMode, "bilingual">;

// 404 の間だけ間隔を空けて再チェックする(取り込み進行中に原本 PDF が後から生えるケースを
// 拾う。§4.4「原文 PDF unreachable until full parse completes」)。15s×20 ≈ 5 分で打ち切り、
// 取得できなかった論文を黙って延々ポーリングし続けない(P3)。呼び出し側(ViewerShell)は
// SSE job.updated 受信時にも invalidateQueries で早期に叩き直す(§4.4 のもう一方の経路)。
const POLL_INTERVAL_MS = 15_000;
const MAX_POLL_ATTEMPTS = 20;

/**
 * PDF アセットの有無を軽量に判定する(2a §5.3: ヘッダの「PDF」セグメント disabled 判定用)。
 * `GET /api/papers/{paper_id}/pdf` はステータス確認後すぐ本文を破棄する(全量ダウンロードは
 * PDF モード表示時のみ行う — §2.1 の決定と両立させるため)。
 * 判定不能(ネットワーク失敗等)は「利用可能」に倒す(fail-open。誤って恒久的に無効化しない)。
 * 原文(source)も生成物(translated)と同様、404 の間は取り込み進行中を疑って再チェックする
 * (以前は source を一度 404 判定すると再読み込みまで固定されたままだった)。
 */
/** 戻り値: true=利用可能, false=404(アセット無し), null=判定中/未着手。 */
export function usePdfAvailability(
  paperId: string | null,
  variant: PdfFetchVariant = "source",
  style: PdfTranslationStyle = "natural",
  identity?: PdfAssetIdentity,
): boolean | null {
  const query = useQuery({
    queryKey: [
      "pdf-available",
      paperId ?? "",
      variant,
      style,
      identity?.revisionId ?? "",
      variant === "translated" ? (identity?.translationSetId ?? "") : "source",
    ],
    queryFn: async () => {
      if (!paperId) return true;
      try {
        const params = variant === "source" ? "" : `?variant=${variant}&style=${style}`;
        const res = await fetch(`/api/papers/${paperId}/pdf${params}`, {
          credentials: "include",
          cache: "no-store",
        });
        try {
          await res.body?.cancel();
        } catch {
          /* ボディ破棄失敗は無視 */
        }
        return res.status !== 404;
      } catch {
        return true;
      }
    },
    enabled: Boolean(paperId),
    staleTime: Infinity,
    refetchInterval: (query) =>
      query.state.data === false && query.state.dataUpdateCount < MAX_POLL_ATTEMPTS
        ? POLL_INTERVAL_MS
        : false,
  });
  return query.data ?? null;
}
