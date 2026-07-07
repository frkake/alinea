"use client";

import type { TranslationStyle } from "@/components/settings/types";

/** 既定の翻訳スタイル(4f §4.4.1)。ラジオカード 2 枚。 */
export interface TranslationStyleRowProps {
  value: TranslationStyle;
  onChange: (next: TranslationStyle) => void;
}

const CARDS: ReadonlyArray<{
  value: TranslationStyle;
  title: string;
  description: string;
}> = [
  {
    value: "natural",
    title: "自然訳(既定)",
    description: "こなれた学術日本語。取り込み時に自動生成",
  },
  {
    value: "literal",
    title: "直訳",
    description: "原文の語順・構文を写像。文単位で対応を追える。初回切替時にオンデマンド生成",
  },
];

export function TranslationStyleRow({ value, onChange }: TranslationStyleRowProps) {
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 10,
        padding: "14px 18px",
        borderBottom: "1px solid var(--pr-border-hair)",
      }}
    >
      <span style={{ fontSize: 12, fontWeight: 600 }}>既定の翻訳スタイル</span>
      <div role="radiogroup" aria-label="既定の翻訳スタイル" style={{ display: "flex", gap: 10 }}>
        {CARDS.map((card) => {
          const selected = card.value === value;
          const mark = selected ? "●" : "○";
          return (
            <button
              key={card.value}
              type="button"
              role="radio"
              aria-checked={selected}
              onClick={() => {
                if (!selected) onChange(card.value);
              }}
              style={{
                flex: 1,
                textAlign: "left",
                display: "flex",
                flexDirection: "column",
                gap: 3,
                padding: "11px 13px",
                borderRadius: 8,
                border: selected ? "1px solid var(--pr-acc)" : "1px solid var(--pr-border-control)",
                boxShadow: selected ? "inset 0 0 0 0.5px var(--pr-acc)" : undefined,
                background: selected ? "var(--pr-acc-s)" : "var(--pr-bg-card)",
                cursor: "pointer",
                fontFamily: "inherit",
              }}
            >
              <span
                style={{
                  fontSize: 12,
                  fontWeight: selected ? 700 : 600,
                  color: selected ? "var(--pr-acc)" : "var(--pr-text-mid)",
                }}
              >
                {mark} {card.title}
              </span>
              <span
                style={{
                  fontSize: 10.5,
                  color: selected ? "var(--pr-text-sub)" : "var(--pr-text-muted)",
                  lineHeight: 1.6,
                }}
              >
                {card.description}
              </span>
            </button>
          );
        })}
      </div>
      <span style={{ fontSize: 10, color: "var(--pr-text-muted)" }}>
        切替は表示の切替であり、原文表示とは独立です。文体は「だ・である」調に固定
      </span>
    </div>
  );
}
