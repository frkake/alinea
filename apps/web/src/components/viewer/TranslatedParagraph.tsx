"use client";

import type { ReactNode } from "react";
import type { TranslationUnitItem } from "@yakudoku/api-client";
import { HighlightMark, type HighlightColor } from "@/components/ui/HighlightMark";
import { InlineRenderer } from "@/components/viewer/InlineRenderer";
import { ParallelPopover } from "@/components/viewer/ParallelPopover";
import { SKIP_OFFSET_ATTR, SOURCE_TEXT_ATTR } from "@/components/viewer/text-offset";
import type { DocBlock } from "@/components/viewer/document-types";

/** text_ja が null で返る翻訳失敗系フラグ(plans/06 §12。1b §5.9)。 */
const FAILURE_FLAGS = new Set(["placeholder_mismatch", "provider_refusal", "untranslated"]);

/** 訳文段落に配置するハイライト範囲(1b §4.5-5 / §5.6)。`start`/`end` は `text_ja` の文字オフセット。 */
export interface PlacedHighlight {
  id: string;
  start: number;
  end: number;
  color: HighlightColor;
  /** 文書順連番(丸数字チップ。1b §5.6)。 */
  number: number;
}

interface TextSpan {
  start: number;
  end: number;
  render: (slice: string, key: number) => ReactNode;
}

function annotationSpans(
  highlights: PlacedHighlight[],
  onAnnotationClick?: (annotationId: string) => void,
): TextSpan[] {
  return highlights.map((h) => ({
    start: h.start,
    end: h.end,
    render: (slice, key) => (
      <HighlightMark
        key={key}
        color={h.color}
        annotationNumber={h.number}
        onClickAnnotation={() => onAnnotationClick?.(h.id)}
      >
        {slice}
      </HighlightMark>
    ),
  }));
}

/** `query` を空白分割した各語の全出現(大小無視)。既存 span と重なる箇所は除く(plans/11 §7)。 */
function searchSpans(text: string, query: string | null | undefined, existing: TextSpan[]): TextSpan[] {
  if (!query) return [];
  const lower = text.toLowerCase();
  const words = query.split(/\s+/).map((w) => w.trim().toLowerCase()).filter(Boolean);
  const spans: TextSpan[] = [];
  for (const word of words) {
    let from = 0;
    while (from <= lower.length) {
      const idx = lower.indexOf(word, from);
      if (idx === -1) break;
      const end = idx + word.length;
      const overlaps = existing.some((s) => idx < s.end && end > s.start);
      if (!overlaps) {
        spans.push({
          start: idx,
          end,
          render: (slice, key) => (
            <mark key={key} className="yk-search-hit">
              {slice}
            </mark>
          ),
        });
      }
      from = idx + Math.max(word.length, 1);
    }
  }
  return spans;
}

/**
 * `text` を注釈ハイライト範囲(`highlights`)+検索ヒット語(`searchQuery`。plans/11 §7 の `hl`)
 * で分割し、`HighlightMark` / `<mark class="yk-search-hit">` を差し込む。重なりは注釈側を優先する。
 */
function renderHighlightedText(
  text: string,
  highlights: PlacedHighlight[],
  searchQuery: string | null | undefined,
  onAnnotationClick?: (annotationId: string) => void,
): ReactNode {
  const annSpans = annotationSpans(highlights, onAnnotationClick);
  const hlSpans = searchSpans(text, searchQuery, annSpans);
  const spans = [...annSpans, ...hlSpans].sort((a, b) => a.start - b.start);
  if (spans.length === 0) return text;

  const nodes: ReactNode[] = [];
  let cursor = 0;
  let key = 0;
  for (const s of spans) {
    const start = Math.max(cursor, Math.min(s.start, text.length));
    const end = Math.max(start, Math.min(s.end, text.length));
    if (start >= end) continue;
    if (start > cursor) nodes.push(text.slice(cursor, start));
    nodes.push(s.render(text.slice(start, end), key++));
    cursor = end;
  }
  if (cursor < text.length) nodes.push(text.slice(cursor));
  return nodes;
}

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
  /** この段落に配置された注釈ハイライト(start 昇順。1b §4.5-5)。 */
  highlights?: PlacedHighlight[];
  /** 本文の丸数字チップクリック → 注釈タブの該当カードへ(1b §5.7)。 */
  onAnnotationClick?: (annotationId: string) => void;
  /** 検索ヒット遷移の `?hl=`(plans/11 §7。遷移先ブロックのみ一発マーク)。 */
  searchHighlight?: string | null;
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
  highlights = [],
  onAnnotationClick,
  searchHighlight = null,
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

      <p
        style={{
          fontSize: 16.5,
          lineHeight: 2.15,
          color: "var(--pr-text-body)",
          margin: `0 0 ${popOpen ? 8 : 22}px`,
        }}
      >
        {hasTranslation ? (
          renderHighlightedText(unit?.text_ja ?? "", highlights, searchHighlight, onAnnotationClick)
        ) : (
          <>
            <span
              {...{ [SOURCE_TEXT_ATTR]: "" }}
              style={{ fontFamily: "var(--pr-font-en)", color: "var(--pr-text-en)" }}
            >
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
