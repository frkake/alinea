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
      return displayCitationLabel(inline).length;
    case "footnote_ref":
      return (inline.v || inline.ref || "").length;
    default:
      return (inline.v ?? "").length;
  }
}

function normalizeCitationText(value: string): string {
  return value
    .replace(/\s+/g, " ")
    .replace(/\s+([,.;:)])/g, "$1")
    .replace(/([([])\s+/g, "$1")
    .trim();
}

function compactCitationValue(value: string | undefined): string | null {
  const text = normalizeCitationText(value ?? "");
  if (!text) return null;

  const authorYear = text.match(
    /^([A-Z][\p{L}'’-]+(?:\s+et\s+al\.?)?)\s*[, ]*\(?((?:19|20)\d{2})(?:\s*(?:\{[^})]{1,8}\}|[a-z]))?\)?/u,
  );
  if (authorYear) {
    const author = normalizeCitationText(authorYear[1] ?? "").replace(/\bet\s+al\.?$/i, "et al.");
    const year = authorYear[2] ?? "";
    return `${author} (${year})`;
  }

  const looksExpanded = text.length > 72 || (text.match(/,/g)?.length ?? 0) >= 2;
  if (looksExpanded) {
    const rawReference = text.match(/^([A-Z][\p{L}'’-]+)\b.*?\b((?:19|20)\d{2})\b/u);
    if (rawReference) {
      return `${rawReference[1]} et al. (${rawReference[2]})`;
    }
  }

  return text;
}

function displayCitationLabel(inline: Inline): string {
  const label = compactCitationValue(inline.v);
  if (label) return label;
  const ref = inline.ref ?? "";
  const match = ref.match(/([A-Za-z][A-Za-z-]+).*?((?:19|20)\d{2})/);
  if (match) {
    const rawAuthor = match[1] ?? "";
    const year = match[2] ?? "";
    const author = `${rawAuthor[0]?.toUpperCase() ?? ""}${rawAuthor.slice(1)}`;
    return `${author} et al. (${year})`;
  }
  return "Reference";
}

function displayRefLabel(inline: Inline): string {
  if (inline.v) return inline.v;
  const ref = inline.ref ?? "";
  const kind = inline.kind ?? "";
  const number = ref.match(/(\d+(?:\.\d+)*)\D*$/)?.[1] ?? "";
  if (kind === "figure") return number ? `Fig. ${number}` : "Fig.";
  if (kind === "table") return number ? `Table ${number}` : "Table";
  if (kind === "equation") return number ? `Eq. (${number})` : "Eq.";
  if (kind === "section") return number ? `Sec. ${number}` : "Sec.";
  if (kind === "algorithm") return number ? `Algorithm ${number}` : "Algorithm";
  if (kind === "theorem") return number ? `Theorem ${number}` : "Theorem";
  return number ? `Ref. ${number}` : "Ref.";
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
        const label = displayCitationLabel(inline);
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
