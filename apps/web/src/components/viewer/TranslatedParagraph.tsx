"use client";

import type { TranslationUnitItem } from "@yakudoku/api-client";
import { InlineRenderer } from "@/components/viewer/InlineRenderer";
import { ParallelPopover } from "@/components/viewer/ParallelPopover";
import type { DocBlock } from "@/components/viewer/document-types";

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
}: TranslatedParagraphProps) {
  const inlines = block.inlines ?? [];
  const hasTranslation = unit != null && unit.text_ja != null;
  const failed =
    !hasTranslation && (unit?.quality_flags ?? []).some((f) => FAILURE_FLAGS.has(f));

  return (
    <div className="yk-paragraph" data-block-id={block.id} style={{ position: "relative" }}>
      <button
        type="button"
        className="yk-parallel-toggle"
        aria-label="対訳を表示"
        aria-pressed={popOpen}
        onClick={onTogglePop}
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

      <p
        style={{
          fontSize: 16.5,
          lineHeight: 2.15,
          color: "var(--pr-text-body)",
          margin: `0 0 ${popOpen ? 8 : 22}px`,
        }}
      >
        {hasTranslation ? (
          unit?.text_ja
        ) : (
          <>
            <span style={{ fontFamily: "var(--pr-font-en)", color: "var(--pr-text-en)" }}>
              <InlineRenderer inlines={inlines} onCitationClick={onCitationClick} />
            </span>{" "}
            {failed ? (
              <button
                type="button"
                onClick={onRetranslate}
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
        />
      ) : null}
    </div>
  );
}
