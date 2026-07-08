"use client";

import type { TranslationUnitItem } from "@yakudoku/api-client";
import { InlineRenderer } from "@/components/viewer/InlineRenderer";
import { ParallelPopover } from "@/components/viewer/ParallelPopover";
import { type PlacedHighlight } from "@/components/viewer/highlight-render";
import { SKIP_OFFSET_ATTR, SOURCE_TEXT_ATTR } from "@/components/viewer/text-offset";
import { TranslationInlineContent, hasTranslatedText } from "@/components/viewer/translation-content";
import type { DocBlock } from "@/components/viewer/document-types";

// PlacedHighlight は BilingualPane・SourcePane(InlineRenderer 経由)とも共有するため
// components/viewer/highlight-render.tsx に定義を移した(M1 統合ポリッシュ)。既存の
// import 経路(`@/components/viewer/TranslatedParagraph` からの型 import)を壊さないよう
// 再エクスポートする。
export type { PlacedHighlight };

/** text_ja が null で返る翻訳失敗系フラグ(plans/06 §12。1b §5.9)。 */
const FAILURE_FLAGS = new Set(["placeholder_mismatch", "provider_refusal", "untranslated"]);

export interface TranslatedParagraphProps {
  block: DocBlock;
  /** null=未翻訳(原文フォールバック)。 */
  unit: TranslationUnitItem | null;
  /** 対訳ポップの表示ラベル「¶2 / 1 Introduction」。 */
  parallelLabel: string;
  popOpen: boolean;
  onTogglePop: () => void;
  onRetranslate?: () => void;
  onCitationClick?: (refId: string) => void;
  onRefClick?: (ref: string, kind?: string | null) => void;
  /** この段落に配置された注釈ハイライト(start 昇順。1b §4.5-5)。 */
  highlights?: PlacedHighlight[];
  /** 本文の丸数字チップクリック → 注釈タブの該当カードへ(1b §5.7)。 */
  onAnnotationClick?: (annotationId: string) => void;
  /** 検索ヒット遷移の `?hl=`(plans/11 §7。遷移先ブロックのみ一発マーク)。 */
  searchHighlight?: string | null;
  /**
   * モバイル縮退(mobile.md §4.4)。ホバー用「対」ボタンを非描画にし、段落タップで
   * 対訳ポップを開閉する。対訳ポップ内の再翻訳フッタも非描画にする(決定)。
   */
  isMobile?: boolean;
}

/** 訳文段落(1b §4.5-5)。ホバーで「対」ボタン、開くと対訳ポップ。未訳は原文+理由(P3)。 */
export function TranslatedParagraph({
  block,
  unit,
  parallelLabel,
  popOpen,
  onTogglePop,
  onRetranslate,
  onCitationClick,
  onRefClick,
  highlights = [],
  onAnnotationClick,
  searchHighlight = null,
  isMobile = false,
}: TranslatedParagraphProps) {
  const inlines = block.inlines ?? [];
  const hasTranslation = hasTranslatedText(unit);
  const failed =
    !hasTranslation && (unit?.quality_flags ?? []).some((f) => FAILURE_FLAGS.has(f));

  return (
    <div className="yk-paragraph" data-block-id={block.id} style={{ position: "relative" }}>
      {isMobile ? null : (
        <button
          type="button"
          className="yk-parallel-toggle"
          aria-label="対訳を表示"
          aria-pressed={popOpen}
          onClick={onTogglePop}
          {...{ [SKIP_OFFSET_ATTR]: "" }}
          style={{
            position: "absolute",
            left: -42,
            top: 6,
            width: 26,
            height: 26,
            borderRadius: 6,
            border: "1px solid var(--pr-border-control)",
            background: "var(--pr-bg-card)",
            color: "var(--pr-acc)",
            fontSize: 11,
            fontWeight: 600,
            boxShadow: "var(--pr-shadow-float)",
            cursor: "pointer",
          }}
        >
          対
        </button>
      )}

      <p
        role={isMobile ? "button" : undefined}
        tabIndex={isMobile ? 0 : undefined}
        aria-pressed={isMobile ? popOpen : undefined}
        onClick={
          isMobile
            ? (e) => {
                // 丸数字チップ(注釈タブへのジャンプ)のタップはポップ開閉と競合させない。
                const target = e.target as HTMLElement;
                if (target.closest(`[${SKIP_OFFSET_ATTR}]`)) return;
                onTogglePop();
              }
            : undefined
        }
        onKeyDown={
          isMobile
            ? (e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  onTogglePop();
                }
              }
            : undefined
        }
        style={{
          // 設定 4f の本文サイズ(既定 16.5px)。CSS 変数が未設定の間は既定値を維持する(§5.6)。
          fontSize: "var(--pr-content-font-size-px, 16.5px)",
          lineHeight: 2.15,
          color: "var(--pr-text-body)",
          margin: `0 0 ${popOpen ? 8 : 22}px`,
          cursor: isMobile ? "pointer" : undefined,
        }}
      >
        {hasTranslation ? (
          <TranslationInlineContent
            unit={unit}
            highlights={highlights}
            searchQuery={searchHighlight}
            onAnnotationClick={onAnnotationClick}
            onCitationClick={onCitationClick}
            onRefClick={onRefClick}
          />
        ) : (
          <>
            <span
              {...{ [SOURCE_TEXT_ATTR]: "" }}
              style={{ fontFamily: "var(--pr-font-en)", color: "var(--pr-text-en)" }}
            >
              <InlineRenderer inlines={inlines} onCitationClick={onCitationClick} onRefClick={onRefClick} />
            </span>{" "}
            {failed ? (
              isMobile ? (
                <span
                  style={{ fontSize: 10.5, fontFamily: "var(--pr-font-ui)", color: "var(--pr-warn)" }}
                >
                  この段落の翻訳に失敗しました
                </span>
              ) : (
                <button
                  type="button"
                  onClick={onRetranslate}
                  {...{ [SKIP_OFFSET_ATTR]: "" }}
                  style={{
                    border: "none",
                    background: "transparent",
                    cursor: "pointer",
                    font: "inherit",
                    fontSize: 10.5,
                    fontFamily: "var(--pr-font-ui)",
                    color: "var(--pr-warn)",
                  }}
                >
                  この段落の翻訳に失敗しました · 再翻訳
                </button>
              )
            ) : (
              <span
                style={{
                  fontSize: 10.5,
                  fontFamily: "var(--pr-font-ui)",
                  color: "var(--pr-text-muted)",
                }}
              >
                翻訳中…
              </span>
            )}
          </>
        )}
      </p>

      {popOpen ? (
        <ParallelPopover
          displayLabel={parallelLabel}
          sourceInlines={inlines}
          onClose={onTogglePop}
          onRetranslate={onRetranslate}
          onCitationClick={onCitationClick}
          onRefClick={onRefClick}
          isMobile={isMobile}
        />
      ) : null}
    </div>
  );
}
