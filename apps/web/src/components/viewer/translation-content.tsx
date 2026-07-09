"use client";

import type { TranslationUnitItem } from "@alinea/api-client";
import { InlineRenderer } from "@/components/viewer/InlineRenderer";
import { renderHighlightedText, type PlacedHighlight } from "@/components/viewer/highlight-render";
import type { Inline } from "@/components/viewer/document-types";

export interface TranslationInlineContentProps {
  unit: TranslationUnitItem | null;
  highlights?: PlacedHighlight[];
  searchQuery?: string | null;
  onAnnotationClick?: (annotationId: string) => void;
  onCitationClick?: (refId: string) => void;
  onRefClick?: (ref: string, kind?: string | null) => void;
}

function isInlineLike(value: unknown): value is Inline {
  if (value == null || typeof value !== "object") return false;
  const t = (value as { t?: unknown }).t;
  return typeof t === "string";
}

export function translationInlines(unit: TranslationUnitItem | null): Inline[] | null {
  const content = unit?.content_ja;
  if (!Array.isArray(content)) return null;
  return content.every(isInlineLike) ? (content as Inline[]) : null;
}

export function hasTranslatedText(unit: TranslationUnitItem | null): boolean {
  return unit != null && unit.text_ja != null;
}

export function TranslationInlineContent({
  unit,
  highlights = [],
  searchQuery = null,
  onAnnotationClick,
  onCitationClick,
  onRefClick,
}: TranslationInlineContentProps) {
  const inlines = translationInlines(unit);
  if (inlines && inlines.length > 0) {
    return (
      <InlineRenderer
        inlines={inlines}
        onCitationClick={onCitationClick}
        onRefClick={onRefClick}
        highlights={highlights}
        searchQuery={searchQuery}
        onAnnotationClick={onAnnotationClick}
      />
    );
  }
  return <>{renderHighlightedText(unit?.text_ja ?? "", highlights, searchQuery, onAnnotationClick)}</>;
}
