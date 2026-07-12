"use client";

import type { IngestFailure } from "@alinea/api-client";

/**
 * 取り込み失敗コード → 短い日本語説明(P3 黙って壊れない)。
 * 未知コード(将来の追加分含む)はフォールバック文言に倒す。
 */
const FAILURE_CODE_LABELS: Record<string, string> = {
  figure_asset_unresolved: "図表の一部を認識できませんでした",
  document_incomplete: "本文の抽出が途中で終わっています",
  macro_expansion_limit: "LaTeX の記法が複雑すぎて展開できませんでした",
  pdf_crashed: "PDF の解析中にエラーが発生しました",
  no_text_layer: "PDF からテキストを抽出できませんでした",
  ocr_engine_unavailable: "OCR エンジンが利用できませんでした",
};

const FALLBACK_REASON = "処理中に問題が発生しました";

function failureReason(code: string | null | undefined): string {
  if (!code) return FALLBACK_REASON;
  return FAILURE_CODE_LABELS[code] ?? FALLBACK_REASON;
}

export interface IngestFailureNoticeProps {
  failure: IngestFailure;
  /** true=原本 PDF あり(PDF タブへの誘導を出す)。null=判定中は誘導を出さない。 */
  pdfAvailable: boolean | null;
  onOpenPdf: () => void;
}

/**
 * 取り込み失敗で本文が空のままの論文を開いた際の通知(2a §P3。空白のまま黙って壊れない)。
 * 原文/対訳/訳文タブの本文枠を丸ごと置き換える。PDF が保存済みならそちらへの導線を出す
 * (記事タブは別リソースのため対象外。呼び出し側で分岐)。
 */
export function IngestFailureNotice({ failure, pdfAvailable, onOpenPdf }: IngestFailureNoticeProps) {
  return (
    <div style={{ flex: 1, display: "flex", justifyContent: "center", padding: "72px 20px", overflowY: "auto" }}>
      <div
        style={{
          maxWidth: 420,
          alignSelf: "flex-start",
          padding: "18px 20px",
          border: "1px solid var(--pr-warn-border, var(--pr-border-card))",
          borderRadius: 8,
          background: "var(--pr-warn-bg, var(--pr-bg-muted))",
          display: "flex",
          flexDirection: "column",
          gap: 8,
          fontFamily: "var(--pr-font-ui)",
        }}
      >
        <div style={{ fontSize: 13, fontWeight: 700, color: "var(--pr-text)" }}>取り込みに失敗しました</div>
        <div style={{ fontSize: 11.5, color: "var(--pr-text-body)", lineHeight: 1.6 }}>
          {failureReason(failure.code)}
        </div>
        {pdfAvailable ? (
          <button
            type="button"
            onClick={onOpenPdf}
            style={{
              alignSelf: "flex-start",
              marginTop: 4,
              border: "none",
              background: "transparent",
              padding: 0,
              fontSize: 11.5,
              fontWeight: 600,
              color: "var(--pr-acc)",
              cursor: "pointer",
              fontFamily: "inherit",
            }}
          >
            PDF タブで原文を見る →
          </button>
        ) : null}
      </div>
    </div>
  );
}
