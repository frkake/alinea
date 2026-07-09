"use client";

import { useState, type CSSProperties } from "react";
import { renderMarkdownLite } from "@/components/vocab/markdown-lite";

export type EditableVocabSectionVariant = "plain" | "card" | "amber";

export interface EditableVocabSectionProps {
  heading: string;
  headingColor?: string;
  variant: EditableVocabSectionVariant;
  /** `generating` の間は本文の代わりに「✦ 生成中…」+ スケルトンを表示し、編集不可(4d §5.5)。 */
  state: "content" | "generating";
  /** Markdown サブセット生文字列。編集 textarea の初期値もこれ(4d §4.2.6)。 */
  text: string;
  fieldKey: "context_meaning" | "interpretation" | "etymology" | "mnemonic" | "related_expressions";
  onSave: (fieldKey: string, value: string) => void;
  saving?: boolean;
}

const secondaryButtonStyle: CSSProperties = {
  height: 24,
  padding: "0 12px",
  border: "1px solid var(--pr-border-control)",
  borderRadius: 6,
  fontSize: 11,
  color: "var(--pr-text-sub)",
  background: "#FFFFFF",
  cursor: "pointer",
  fontFamily: "inherit",
};

const primaryButtonStyle: CSSProperties = {
  height: 24,
  padding: "0 12px",
  border: "none",
  borderRadius: 6,
  fontSize: 11,
  fontWeight: 600,
  color: "#FFFFFF",
  background: "var(--pr-acc)",
  cursor: "pointer",
  fontFamily: "inherit",
};

function bodyStyleFor(variant: EditableVocabSectionVariant): CSSProperties {
  if (variant === "card") {
    return {
      fontSize: 11.5,
      lineHeight: 1.75,
      color: "#3C4046",
      background: "var(--pr-bg-hover)",
      borderRadius: 7,
      padding: "9px 11px",
    };
  }
  if (variant === "amber") {
    return {
      fontSize: 11.5,
      lineHeight: 1.75,
      color: "#3C4046",
      background: "#FFF9F0",
      border: "1px solid #EEDDB8",
      borderRadius: 7,
      padding: "9px 11px",
    };
  }
  return { fontSize: 11, lineHeight: 1.7, color: "#5B6067" };
}

/** 編集可能セクション(4d §4.2.6・§5.7)。ホバー「編集」→ textarea → キャンセル/保存。 */
export function EditableVocabSection({
  heading,
  headingColor,
  variant,
  state,
  text,
  fieldKey,
  onSave,
  saving = false,
}: EditableVocabSectionProps) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(text);
  const [hover, setHover] = useState(false);

  return (
    <div
      style={{ display: "flex", flexDirection: "column", gap: 5 }}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
    >
      <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
        <span
          style={{
            fontSize: 10.5,
            fontWeight: 700,
            color: headingColor ?? "var(--pr-text-muted)",
            letterSpacing: "0.4px",
          }}
        >
          {heading}
        </span>
        {state === "content" && hover && !editing ? (
          <button
            type="button"
            onClick={() => {
              setDraft(text);
              setEditing(true);
            }}
            style={{
              marginLeft: "auto",
              fontSize: 10,
              fontWeight: 600,
              color: "var(--pr-acc)",
              background: "none",
              border: "none",
              cursor: "pointer",
              fontFamily: "inherit",
            }}
          >
            編集
          </button>
        ) : null}
      </div>

      {state === "generating" ? (
        <GeneratingPlaceholder />
      ) : editing ? (
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <textarea
            autoFocus
            aria-label={`${heading}を編集`}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Escape") {
                e.stopPropagation();
                setEditing(false);
              }
            }}
            style={{
              width: "100%",
              boxSizing: "border-box",
              minHeight: 64,
              padding: "8px 10px",
              border: "1px solid #DDD9CF",
              borderRadius: 6,
              fontSize: variant === "plain" ? 11 : 11.5,
              fontFamily: "inherit",
              resize: "vertical",
            }}
          />
          <div style={{ display: "flex", justifyContent: "flex-end", gap: 6 }}>
            <button type="button" onClick={() => setEditing(false)} style={secondaryButtonStyle}>
              キャンセル
            </button>
            <button
              type="button"
              disabled={saving}
              onClick={() => {
                onSave(fieldKey, draft);
                setEditing(false);
              }}
              style={primaryButtonStyle}
            >
              保存
            </button>
          </div>
        </div>
      ) : (
        <div style={bodyStyleFor(variant)}>{renderMarkdownLite(text)}</div>
      )}
    </div>
  );
}

function GeneratingPlaceholder() {
  const skeleton: CSSProperties = {
    height: 11,
    borderRadius: 3,
    background: "var(--pr-bg-muted)",
    animation: "alinea-pulse 1.2s ease-in-out infinite",
  };
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      <div style={{ fontSize: 11.5, color: "var(--pr-text-muted)" }}>
        <span style={{ color: "var(--pr-acc)" }}>✦</span> 生成中…
      </div>
      <div style={{ ...skeleton, width: "100%" }} />
      <div style={{ ...skeleton, width: "70%" }} />
    </div>
  );
}
