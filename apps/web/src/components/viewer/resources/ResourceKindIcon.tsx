"use client";

import type { CSSProperties } from "react";
import type { ResKind } from "./types";
import { articleIconLabel } from "./format";

export interface ResourceKindIconProps {
  kind: ResKind;
  sourceLabel: string;
}

const BASE: CSSProperties = {
  width: 26,
  height: 26,
  borderRadius: 6,
  display: "inline-flex",
  alignItems: "center",
  justifyContent: "center",
  flex: "none",
  fontWeight: 700,
};

/** 種類アイコン(26×26px。plans/09-screens/5a §4.5)。 */
export function ResourceKindIcon({ kind, sourceLabel }: ResourceKindIconProps) {
  if (kind === "github") {
    return (
      <span
        style={{ ...BASE, background: "var(--pr-elev-bg, #26292E)", color: "#FFFFFF", fontSize: 9, letterSpacing: 0.3 }}
      >
        GH
      </span>
    );
  }
  if (kind === "youtube") {
    return (
      <span style={{ ...BASE, background: "var(--pr-youtube, #B3423A)", color: "#FFFFFF", fontSize: 10 }}>
        ▶
      </span>
    );
  }
  if (kind === "slides") {
    return (
      <span
        style={{
          ...BASE,
          background: "var(--pr-ann-important-count-bg, rgba(196,148,50,0.18))",
          color: "var(--pr-ann-important-chip-fg, #8A6A24)",
          fontSize: 8.5,
        }}
      >
        PDF
      </span>
    );
  }
  return (
    <span
      style={{
        ...BASE,
        background: "var(--pr-article-icon-bg, rgba(88,132,170,0.18))",
        color: "var(--pr-article-icon-fg, #4A6E8E)",
        fontSize: 11,
      }}
    >
      {articleIconLabel(sourceLabel)}
    </span>
  );
}
