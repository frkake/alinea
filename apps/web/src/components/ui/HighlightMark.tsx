import type { CSSProperties, ReactNode } from "react";

/** 注釈ハイライトの色種別(plans/03 §8.1 AnnColor)。 */
export type HighlightColor = "important" | "question" | "idea" | "term";

/**
 * オフセット計算除外マーカー(1b §5.5 の Anchor 構築。`text-offset.ts` が参照)。
 * 丸数字チップは選択メニュー操作後に DOM へ差し込まれる装飾要素であり、
 * 本文の実テキストではないため文字オフセット計算から除外する。
 */
export const SKIP_OFFSET_ATTR = "data-alinea-skip-offset";

export interface HighlightMarkProps {
  color: HighlightColor;
  /** 文書順 1 始まり連番(placed 注釈のみ。無ければ丸数字チップを描かない)。 */
  annotationNumber?: number;
  /** 注釈タブ該当カードへジャンプ(丸数字チップクリック時)。 */
  onClickAnnotation?: () => void;
  children: ReactNode;
}

const markStyle = (color: HighlightColor): CSSProperties => ({
  background: `var(--pr-ann-${color}-bg)`,
  borderRadius: 2,
  padding: "0 1px",
});

const chipStyle = (color: HighlightColor): CSSProperties => ({
  display: "inline-flex",
  alignItems: "center",
  justifyContent: "center",
  width: 14,
  height: 14,
  borderRadius: "50%",
  background: `var(--pr-ann-${color}-chip-bg)`,
  color: `var(--pr-ann-${color}-chip-fg)`,
  fontSize: 9,
  fontWeight: 700,
  verticalAlign: 4,
  marginLeft: 2,
  border: "none",
  cursor: "pointer",
  fontFamily: "var(--pr-font-ui)",
  lineHeight: 1,
});

/**
 * 本文ハイライトの `<mark>`(plans/08 §5.17)。4 色(重要/疑問/アイデア/用語)+
 * placed 注釈の丸数字チップ(クリックで注釈タブの該当カードへ)。
 */
export function HighlightMark({
  color,
  annotationNumber,
  onClickAnnotation,
  children,
}: HighlightMarkProps) {
  return (
    <>
      <mark className={`alinea-highlight alinea-highlight-${color}`} style={markStyle(color)}>
        {children}
      </mark>
      {annotationNumber != null ? (
        <button
          type="button"
          aria-label={`注釈 ${annotationNumber} を表示`}
          style={chipStyle(color)}
          onClick={onClickAnnotation}
          {...{ [SKIP_OFFSET_ATTR]: "" }}
        >
          {annotationNumber}
        </button>
      ) : null}
    </>
  );
}
