"use client";

import type { TranslationStyle } from "@/stores/viewer-store";

const STYLE_LABELS: Record<TranslationStyle, string> = {
  natural: "自然訳",
  literal: "直訳",
  easy: "やさしい訳",
};

export interface TranslationColumnHeaderProps {
  style: TranslationStyle;
  /** 「段落対応 ⇄」トグル状態(1a §5.2)。ON=左右段落ペアのホバー対応強調。 */
  pairSync: boolean;
  onTogglePairSync: () => void;
}

const labelStyle = {
  fontSize: 10.5,
  fontWeight: 600,
  letterSpacing: "0.4px",
  color: "var(--pr-text-muted)",
} as const;

/** 対訳カラム見出し行(1a §4.4)。原文/訳文の見出し+「段落対応 ⇄」トグル。 */
export function TranslationColumnHeader({
  style,
  pairSync,
  onTogglePairSync,
}: TranslationColumnHeaderProps) {
  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "1fr 1fr",
        columnGap: 34,
        paddingBottom: 8,
        borderBottom: "1px solid var(--pr-border-soft)",
      }}
    >
      <div style={labelStyle}>原文 — ENGLISH</div>
      <div style={{ ...labelStyle, display: "flex", alignItems: "center", gap: 6 }}>
        <span>訳文 — {STYLE_LABELS[style]}</span>
        <span style={{ color: "var(--pr-acc)" }}>✦ AI翻訳</span>
        <button
          type="button"
          aria-pressed={pairSync}
          aria-label="段落対応"
          onClick={onTogglePairSync}
          style={{
            marginLeft: "auto",
            border: "none",
            background: "transparent",
            cursor: "pointer",
            fontFamily: "inherit",
            fontSize: 10.5,
            fontWeight: 600,
            letterSpacing: "0.4px",
            color: "var(--pr-text-faint)",
            opacity: pairSync ? 1 : 0.55,
          }}
        >
          段落対応 ⇄
        </button>
      </div>
    </div>
  );
}
