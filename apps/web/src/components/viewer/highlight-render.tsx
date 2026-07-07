"use client";

import type { ReactNode } from "react";
import { HighlightMark, type HighlightColor } from "@/components/ui/HighlightMark";

/**
 * 本文に配置するハイライト範囲(1b §4.5-5 / §5.6)。`start`/`end` は対象テキストの文字オフセット
 * (訳文段落なら `text_ja`、原文インライン列なら `InlineRenderer` の text 系インラインを
 * 連結した空間。M1 統合ポリッシュ: BilingualPane / SourcePane からも再利用する)。
 */
export interface PlacedHighlight {
  id: string;
  start: number;
  end: number;
  color: HighlightColor;
  /** 文書順連番(丸数字チップ。1b §5.6)。 */
  number: number;
}

export interface TextSpan {
  start: number;
  end: number;
  render: (slice: string, key: number) => ReactNode;
}

/** 配置済み注釈ハイライトを `TextSpan` に変換(丸数字チップ付き)。 */
export function annotationSpans(
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
export function searchSpans(text: string, query: string | null | undefined, existing: TextSpan[]): TextSpan[] {
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
 * TranslatedParagraph(訳文)/ InlineRenderer(対訳・原文の原文インライン列)が共有する部品。
 */
export function renderHighlightedText(
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
