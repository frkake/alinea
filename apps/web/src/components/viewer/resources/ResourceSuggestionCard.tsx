"use client";

import { stripScheme } from "./format";
import type { ResourceSuggestion } from "./types";

export interface ResourceSuggestionCardProps {
  suggestion: ResourceSuggestion;
  onAccept: () => void;
  onDismiss: () => void;
  /** accept/dismiss の実行中(両ボタン disabled)。 */
  pending: boolean;
}

/** 公式実装 自動検出カード(破線。plans/09-screens/5a §4.5-a・docs/12 §5)。 */
export function ResourceSuggestionCard({
  suggestion,
  onAccept,
  onDismiss,
  pending,
}: ResourceSuggestionCardProps) {
  return (
    <div
      style={{
        border: "1px dashed var(--pr-border-dashed-suggest, #CBC7BA)",
        borderRadius: 8,
        padding: "10px 12px",
        display: "flex",
        flexDirection: "column",
        gap: 7,
        background: "var(--pr-bg-app, #FBFAF7)",
      }}
    >
      <div style={{ fontSize: 11, lineHeight: 1.65, color: "var(--pr-text-mid)" }}>
        <span style={{ color: "var(--pr-acc)", fontWeight: 700 }}>✦ 公式実装を検出しました</span>
        {" — arXiv ページのリンクから"}
        <br />
        <span style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 10.5, color: "var(--pr-text-sub)" }}>
          {stripScheme(suggestion.url)}
        </span>
      </div>
      <div style={{ display: "flex", gap: 6 }}>
        <button
          type="button"
          onClick={onAccept}
          disabled={pending}
          style={{
            display: "inline-flex",
            alignItems: "center",
            height: 22,
            padding: "0 11px",
            borderRadius: 5,
            border: "none",
            background: "var(--pr-acc)",
            color: "#FFFFFF",
            fontSize: 10.5,
            fontWeight: 600,
            fontFamily: "inherit",
            cursor: pending ? "default" : "pointer",
            opacity: pending ? 0.5 : 1,
          }}
        >
          + 追加
        </button>
        <button
          type="button"
          onClick={onDismiss}
          disabled={pending}
          style={{
            height: 22,
            padding: "0 10px",
            border: "none",
            background: "transparent",
            color: "var(--pr-text-muted)",
            fontSize: 10.5,
            fontFamily: "inherit",
            cursor: pending ? "default" : "pointer",
            opacity: pending ? 0.5 : 1,
          }}
        >
          無視
        </button>
      </div>
    </div>
  );
}
