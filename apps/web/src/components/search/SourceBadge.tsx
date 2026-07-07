import type { CSSProperties } from "react";
import type { SourceTone } from "@/components/search/searchNav";

/** 横断検索ヒット源バッジ(plans/08 §5.22。1e ドロップダウン・4e 全結果画面で共用)。 */
export interface SourceBadgeProps {
  tone: SourceTone;
  label: string;
  /** sm=4e ヒット行(font 9px) / md=1e ドロップダウン(font 9.5px)。高さは両者 16px。 */
  size?: "sm" | "md";
}

const TONE_VARS: Record<SourceTone, { bg: string; fg: string }> = {
  body: { bg: "var(--pr-src-body-bg)", fg: "var(--pr-src-body-fg)" },
  note: { bg: "var(--pr-src-note-bg)", fg: "var(--pr-src-note-fg)" },
  chat: { bg: "var(--pr-src-chat-bg)", fg: "var(--pr-src-chat-fg)" },
  article: { bg: "var(--pr-src-article-bg)", fg: "var(--pr-src-article-fg)" },
};

export function SourceBadge({ tone, label, size = "sm" }: SourceBadgeProps) {
  const colors = TONE_VARS[tone];
  const style: CSSProperties = {
    display: "inline-flex",
    alignItems: "center",
    height: 16,
    padding: "0 6px",
    borderRadius: 3,
    fontSize: size === "sm" ? 9 : 9.5,
    fontWeight: 700,
    flex: "none",
    background: colors.bg,
    color: colors.fg,
    whiteSpace: "nowrap",
  };
  return <span style={style}>{label}</span>;
}
