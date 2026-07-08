"use client";

import { Fragment, type ReactNode } from "react";
import { renderInlineMath } from "@/lib/katex-render";
import { renderHighlightedText, type PlacedHighlight } from "@/components/viewer/highlight-render";
import type { Inline } from "@/components/viewer/document-types";

export interface InlineRendererProps {
  inlines: Inline[];
  /** 引用 [n] クリック(参考文献展開)。M0 は任意。 */
  onCitationClick?: (refId: string) => void;
  /** 図表・式・節参照クリック。ref/kind は LaTeX/PDF 由来のアンカー解決に使う。 */
  onRefClick?: (ref: string, kind?: string | null) => void;
  /**
   * 原文インライン列に配置する注釈ハイライト(M1 統合ポリッシュ: BilingualPane・SourcePane の
   * hl パリティ)。オフセットは本コンポーネントが `text` 系インラインを連結した空間
   * (`inlineOffsetLength` 参照)。`text` タイプ以外(引用・数式等)は分割せずそのまま描画する
   * (P3: 崩れるより欠けるほうが安全)。
   */
  highlights?: PlacedHighlight[];
  /** `?hl=`(plans/11 §7)の一発マーク対象クエリ。null/未指定はマークしない。 */
  searchQuery?: string | null;
  /** ハイライトの丸数字チップクリック → 注釈タブの該当カードへ(1b §5.7 と同じ配線)。 */
  onAnnotationClick?: (annotationId: string) => void;
}

/**
 * `text` 以外のインラインが `highlights`/`searchQuery` の対象になった場合の近似文字数
 * (実 DOM の `textContent` 長とは厳密には一致しない場合がある。数式は LaTeX ソース長で近似)。
 * オフセット空間は `text-offset.ts` の `textOffsetWithin` と同じ規約(祖先が InlineRenderer の
 * 直接出力のみを子に持つ場合)を前提とする。
 */
function inlineOffsetLength(inline: Inline): number {
  switch (inline.t) {
    case "emphasis":
      return (
        inline.children?.reduce((sum, child) => sum + inlineOffsetLength(child), 0) ??
        (inline.v ?? "").length
      );
    case "citation":
      return (inline.v || `[${inline.ref ?? ""}]`).length;
    case "footnote_ref":
      return (inline.v || inline.ref || "").length;
    default:
      return (inline.v ?? "").length;
  }
}

function displayRefLabel(inline: Inline): string {
  if (inline.v) return inline.v;
  const ref = inline.ref ?? "";
  const kind = inline.kind ?? "";
  const number = ref.match(/(\d+(?:\.\d+)*)$/)?.[1] ?? ref;
  if (kind === "figure") return `図${number}`;
  if (kind === "table") return `表${number}`;
  if (kind === "equation") return `式${number.startsWith("(") ? number : `(${number})`}`;
  if (kind === "section") return `§${number}`;
  return ref;
}

/** インライン列(原文)を描画(1b §5.3。数式=KaTeX、引用/参照=アクセント)。 */
export function InlineRenderer({
  inlines,
  onCitationClick,
  onRefClick,
  highlights = [],
  searchQuery = null,
  onAnnotationClick,
}: InlineRendererProps) {
  let offset = 0;

  const renderInline = (inline: Inline, key: string): ReactNode => {
    if (inline.t === "emphasis" && inline.children?.length) {
      return <em key={key}>{renderList(inline.children, key)}</em>;
    }

    if (inline.t === "emphasis") {
      const text = inline.v ?? "";
      const start = offset;
      offset += text.length;
      const end = offset;
      const local = highlights
        .filter((h) => h.start < end && h.end > start)
        .map((h) => ({
          ...h,
          start: Math.max(h.start, start) - start,
          end: Math.min(h.end, end) - start,
        }));
      return (
        <em key={key}>
          {local.length === 0 && !searchQuery
            ? text
            : renderHighlightedText(text, local, searchQuery, onAnnotationClick)}
        </em>
      );
    }

    const start = offset;
    offset += inlineOffsetLength(inline);
    const end = offset;

    if (inline.t === "text") {
      const text = inline.v ?? "";
      if (highlights.length === 0 && !searchQuery) {
        return <Fragment key={key}>{text}</Fragment>;
      }
      // グローバルオフセット → このインライン内のローカルオフセットへ変換(範囲外は捨てる)。
      const local = highlights
        .filter((h) => h.start < end && h.end > start)
        .map((h) => ({
          ...h,
          start: Math.max(h.start, start) - start,
          end: Math.min(h.end, end) - start,
        }));
      return (
        <Fragment key={key}>
          {renderHighlightedText(text, local, searchQuery, onAnnotationClick)}
        </Fragment>
      );
    }

    switch (inline.t) {
      case "code_inline":
        return (
          <code key={key} style={{ fontFamily: "var(--pr-font-mono)", fontSize: "0.9em" }}>
            {inline.v}
          </code>
        );
      case "math_inline":
        return (
          <span
            key={key}
            // KaTeX の信頼できる自前レンダリング出力。
            dangerouslySetInnerHTML={{ __html: renderInlineMath(inline.v ?? "") }}
          />
        );
      case "citation": {
        const label = inline.v || `[${inline.ref ?? ""}]`;
        return (
          <button
            key={key}
            type="button"
            onClick={() => inline.ref && onCitationClick?.(inline.ref)}
            style={{
              border: "none",
              background: "transparent",
              cursor: "pointer",
              padding: 0,
              font: "inherit",
              color: "var(--pr-acc)",
              fontWeight: 600,
            }}
          >
            {label}
          </button>
        );
      }
      case "ref": {
        const label = displayRefLabel(inline);
        if (inline.ref && onRefClick) {
          return (
            <button
              key={key}
              type="button"
              onClick={() => onRefClick(inline.ref ?? "", inline.kind)}
              style={{
                border: "none",
                background: "transparent",
                cursor: "pointer",
                padding: 0,
                font: "inherit",
                color: "var(--pr-acc)",
                fontWeight: 600,
              }}
            >
              {label}
            </button>
          );
        }
        return (
          <span key={key} style={{ color: "var(--pr-acc)", fontWeight: 600 }}>
            {label}
          </span>
        );
      }
      case "footnote_ref":
        return (
          <sup key={key} style={{ color: "var(--pr-acc)" }}>
            {inline.v || inline.ref}
          </sup>
        );
      case "url":
        return (
          <a
            key={key}
            href={inline.href ?? undefined}
            target="_blank"
            rel="noreferrer"
            style={{ color: "var(--pr-acc)" }}
          >
            {inline.v || inline.href}
          </a>
        );
      default:
        return <Fragment key={key}>{inline.v}</Fragment>;
    }
  };

  const renderList = (items: Inline[], prefix = "i"): ReactNode =>
    items.map((inline, i) => renderInline(inline, `${prefix}-${i}`));

  return <>{renderList(inlines)}</>;
}
