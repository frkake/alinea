"use client";

import type { CSSProperties } from "react";

export interface SectionHeadingProps {
  /** セクション番号(例 "1")。null=番号なし(アブストラクト等)。 */
  number: string | null;
  /** 日本語見出し。null のときは原題のみ本文扱い。 */
  titleJa: string | null;
  /** 原題(英語)。見出しに併記する(1b §4.5-4)。 */
  titleEn: string;
  /** アブストラクト等の小見出しラベル形式(12px muted。1b §4.5-1)。 */
  variant?: "heading" | "label";
}

const enStyle: CSSProperties = {
  fontFamily: "var(--pr-font-en)",
  fontStyle: "italic",
};

/** セクション見出し(19px/700)+ 原題併記(1b §4.5-4)。variant='label' はアブストラクト等の 12px ラベル。 */
export function SectionHeading({ number, titleJa, titleEn, variant = "heading" }: SectionHeadingProps) {
  const ja = titleJa ?? titleEn;

  if (variant === "label") {
    // 「アブストラクト — Abstract」(1b §4.5-1)
    return (
      <div
        style={{
          fontFamily: "var(--pr-font-ui)",
          fontSize: 12,
          color: "var(--pr-text-muted)",
          marginBottom: 8,
        }}
      >
        {ja}
        <span style={{ ...enStyle, color: "var(--pr-text-muted)" }}> — {titleEn}</span>
      </div>
    );
  }

  return (
    <h2
      style={{
        fontFamily: "var(--pr-font-ui)",
        fontSize: 19,
        fontWeight: 700,
        margin: "30px 0 14px",
        color: "var(--pr-text)",
      }}
    >
      {number ? `${number} ` : ""}
      {ja}
      <span style={{ ...enStyle, color: "var(--pr-text-icon)", fontWeight: 400, fontSize: 14 }}>
        {" "}
        — {titleEn}
      </span>
    </h2>
  );
}
