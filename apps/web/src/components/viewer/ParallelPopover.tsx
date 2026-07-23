"use client";

import type { CSSProperties } from "react";
import { Keycap } from "@/components/ui/Keycap";
import { InlineRenderer } from "@/components/viewer/InlineRenderer";
import { SOURCE_TEXT_ATTR } from "@/components/viewer/text-offset";
import type { Inline } from "@/components/viewer/document-types";

export interface ParallelPopoverProps {
  /** 位置表示「¶2 / 1 Introduction」(セクション内段落序数 + 見出し)。 */
  displayLabel: string;
  /** 原文インライン列(citation/ref 含む)。 */
  sourceInlines: Inline[];
  onClose: () => void;
  /** M0: 指示なし再翻訳(1b §5.3)。 */
  onRetranslate?: () => void;
  onCitationClick?: (refId: string) => void;
  onRefClick?: (ref: string, kind?: string | null) => void;
  /** 再翻訳ジョブ実行中(フッタを非活性化)。 */
  retranslating?: boolean;
  /** 再翻訳エラーメッセージ(Popover 内に表示)。 */
  retranslationError?: string;
  /** モバイル縮退(mobile.md §4.4)。再翻訳フッタ(操作系)を非描画にする。 */
  isMobile?: boolean;
}

const footerLink: CSSProperties = {
  border: "none",
  background: "transparent",
  cursor: "pointer",
  padding: 0,
  fontFamily: "var(--pr-font-ui)",
  fontSize: 10.5,
  color: "var(--pr-text-icon)",
};

/** 対訳ポップ(1b §4.5-6 / §5.3)。段落直下にインライン展開。 */
export function ParallelPopover({
  displayLabel,
  sourceInlines,
  onClose,
  onRetranslate,
  onCitationClick,
  onRefClick,
  retranslating = false,
  retranslationError,
  isMobile = false,
}: ParallelPopoverProps) {
  return (
    <div
      role="dialog"
      aria-label="対訳"
      style={{
        border: "1px solid var(--pr-border-card)",
        borderLeft: "3px solid var(--pr-acc)",
        background: "var(--pr-bg-card)",
        borderRadius: 8,
        padding: "14px 18px",
        margin: "0 0 22px",
        position: "relative",
      }}
    >
      <div
        style={{
          fontFamily: "var(--pr-font-ui)",
          fontSize: 10.5,
          color: "var(--pr-text-muted)",
          marginBottom: 6,
          display: "flex",
          gap: 8,
          alignItems: "center",
        }}
      >
        <span>原文</span>
        <span style={{ color: "var(--pr-text-faint)" }}>{displayLabel}</span>
        <button
          type="button"
          onClick={onClose}
          style={{ ...footerLink, marginLeft: "auto", color: "var(--pr-text-muted)" }}
        >
          閉じる ×
        </button>
      </div>
      <div
        {...{ [SOURCE_TEXT_ATTR]: "" }}
        style={{
          fontFamily: "var(--pr-font-en)",
          fontSize: 14.5,
          lineHeight: 1.8,
          color: "var(--pr-text-en)",
        }}
      >
        <InlineRenderer inlines={sourceInlines} onCitationClick={onCitationClick} onRefClick={onRefClick} />
      </div>
      {isMobile ? null : (
        <div
          style={{
            fontFamily: "var(--pr-font-ui)",
            display: "flex",
            gap: 14,
            alignItems: "center",
            fontSize: 10.5,
            color: "var(--pr-text-icon)",
            marginTop: 10,
            paddingTop: 8,
            borderTop: "1px solid var(--pr-border-hair)",
            flexWrap: "wrap",
          }}
        >
          <span style={{ color: "var(--pr-acc)", fontWeight: 600 }}>訳がおかしい?</span>
          <button
            type="button"
            onClick={onRetranslate}
            disabled={retranslating}
            style={{ ...footerLink, opacity: retranslating ? 0.5 : 1 }}
          >
            再翻訳
          </button>
          {retranslating ? <span>再翻訳中…</span> : null}
          {retranslationError ? (
            <span style={{ color: "var(--pr-warn, #e05c5c)", fontSize: 10.5 }}>
              {retranslationError}
            </span>
          ) : null}
          <span style={{ marginLeft: "auto", display: "inline-flex", alignItems: "center", gap: 5 }}>
            <Keycap mono>t</Keycap> で開閉
          </span>
        </div>
      )}
    </div>
  );
}
