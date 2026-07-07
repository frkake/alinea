import type { CSSProperties } from "react";
import type { VocabKind } from "@/components/vocab/types";

/** 語彙種別 3 分類の日本語ラベル(docs/11 §3。固定・逐語)。 */
export const VOCAB_KIND_LABEL: Record<VocabKind, string> = {
  word: "単語",
  collocation: "コロケーション",
  idiom: "イディオム",
};

const VOCAB_KIND_COLOR: Record<VocabKind, { bg: string; fg: string }> = {
  word: { bg: "#F1EFE9", fg: "#777B81" },
  collocation: { bg: "rgba(88,132,170,0.16)", fg: "#4A6E8E" },
  idiom: { bg: "rgba(110,90,126,0.14)", fg: "#6E5A7E" },
};

export interface VocabKindBadgeProps {
  kind: VocabKind;
  /** list: h16px/9px(一覧行) / detail: h17px/9.5px(詳細ヘッダ)。4d §4.2.5・§4.2.6。 */
  size: "list" | "detail";
}

/** 語彙種別バッジ(単語=グレー/コロケーション=青系/イディオム=紫系。docs/11 §3)。 */
export function VocabKindBadge({ kind, size }: VocabKindBadgeProps) {
  const color = VOCAB_KIND_COLOR[kind];
  const style: CSSProperties = {
    display: "inline-flex",
    alignItems: "center",
    height: size === "list" ? 16 : 17,
    padding: size === "list" ? "0 6px" : "0 7px",
    borderRadius: 3,
    fontSize: size === "list" ? 9 : 9.5,
    fontWeight: 600,
    background: color.bg,
    color: color.fg,
    flex: "none",
    whiteSpace: "nowrap",
  };
  return <span style={style}>{VOCAB_KIND_LABEL[kind]}</span>;
}
