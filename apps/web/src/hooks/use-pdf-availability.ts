"use client";

import { useQuery } from "@tanstack/react-query";
import type { PdfDocumentMode } from "@/stores/pdf-view-store";

type PdfFetchVariant = Exclude<PdfDocumentMode, "bilingual">;

/**
 * PDF アセットの有無を軽量に判定する(2a §5.3: ヘッダの「PDF」セグメント disabled 判定用)。
 * `GET /api/papers/{paper_id}/pdf` はステータス確認後すぐ本文を破棄する(全量ダウンロードは
 * PDF モード表示時のみ行う — §2.1 の決定と両立させるため)。
 * 判定不能(ネットワーク失敗等)は「利用可能」に倒す(fail-open。誤って恒久的に無効化しない)。
 */
/** 戻り値: true=利用可能, false=404(アセット無し), null=判定中/未着手。 */
export function usePdfAvailability(
  paperId: string | null,
  variant: PdfFetchVariant = "source",
): boolean | null {
  const generatedVariant = variant !== "source";
  const query = useQuery({
    queryKey: ["pdf-available", paperId ?? "", variant],
    queryFn: async () => {
      if (!paperId) return true;
      try {
        const params = variant === "source" ? "" : `?variant=${variant}`;
        const res = await fetch(`/api/papers/${paperId}/pdf${params}`, { credentials: "include" });
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
    refetchInterval: (query) => (generatedVariant && query.state.data === false ? 15_000 : false),
  });
  return query.data ?? null;
}
