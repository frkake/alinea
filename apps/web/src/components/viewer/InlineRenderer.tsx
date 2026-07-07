"use client";

import { Fragment } from "react";
import { renderInlineMath } from "@/lib/katex-render";
import type { Inline } from "@/components/viewer/document-types";

export interface InlineRendererProps {
  inlines: Inline[];
  /** 引用 [n] クリック(参考文献展開)。M0 は任意。 */
  onCitationClick?: (refId: string) => void;
}

/** インライン列(原文)を描画(1b §5.3。数式=KaTeX、引用/参照=アクセント)。 */
export function InlineRenderer({ inlines, onCitationClick }: InlineRendererProps) {
  return (
    <>
      {inlines.map((inline, i) => {
        switch (inline.t) {
          case "text":
            return <Fragment key={i}>{inline.v}</Fragment>;
          case "emphasis":
            return <em key={i}>{inline.v}</em>;
          case "code_inline":
            return (
              <code key={i} style={{ fontFamily: "var(--pr-font-mono)", fontSize: "0.9em" }}>
                {inline.v}
              </code>
            );
          case "math_inline":
            return (
              <span
                key={i}
                // KaTeX の信頼できる自前レンダリング出力。
                dangerouslySetInnerHTML={{ __html: renderInlineMath(inline.v ?? "") }}
              />
            );
          case "citation": {
            const label = inline.v || `[${inline.ref ?? ""}]`;
            return (
              <button
                key={i}
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
          case "ref":
            return (
              <span key={i} style={{ color: "var(--pr-acc)", fontWeight: 600 }}>
                {inline.v}
              </span>
            );
          case "footnote_ref":
            return (
              <sup key={i} style={{ color: "var(--pr-acc)" }}>
                {inline.v || inline.ref}
              </sup>
            );
          case "url":
            return (
              <a key={i} href={inline.href ?? undefined} style={{ color: "var(--pr-acc)" }}>
                {inline.v}
              </a>
            );
          default:
            return <Fragment key={i}>{inline.v}</Fragment>;
        }
      })}
    </>
  );
}
