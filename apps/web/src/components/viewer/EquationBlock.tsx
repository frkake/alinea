"use client";

import { useMemo, type CSSProperties } from "react";
import { AiMark } from "@/components/ui/AIBadge";
import { useToast } from "@/components/ui/Toast";
import { renderBlockMath } from "@/lib/katex-render";

export interface EquationBlockProps {
  /** ブロック数式の LaTeX(Block.latex)。 */
  latex: string;
  /** 数式番号(Block.number。例 "(1)")。右寄せ表示。 */
  number?: string | null;
  /** ✦この式を説明 = panel=chat に数式を渡す(docs/04)。 */
  onExplain?: (latex: string) => void;
}

const actionStyle: CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: 4,
  border: "1px solid var(--pr-border-control)",
  background: "var(--pr-bg-card)",
  borderRadius: 6,
  padding: "0 8px",
  height: 22,
  fontFamily: "var(--pr-font-ui)",
  fontSize: 10.5,
  color: "var(--pr-text-mid)",
  cursor: "pointer",
  boxShadow: "var(--pr-shadow-float)",
};

/**
 * ブロック数式(1b / docs/04 §3)。KaTeX でレンダリングし、
 * ホバーで「✦この式を説明」「LaTeXをコピー」アクションを出す。
 * KaTeX 出力は自前レンダリング結果(信頼できる HTML)なので dangerouslySetInnerHTML で描画。
 */
export function EquationBlock({ latex, number, onExplain }: EquationBlockProps) {
  const toast = useToast();
  const html = useMemo(() => renderBlockMath(latex), [latex]);

  const copyLatex = () => {
    void navigator.clipboard?.writeText(latex).then(
      () => toast({ kind: "success", message: "LaTeXをコピーしました" }),
      () => toast({ kind: "error", message: "コピーできませんでした" }),
    );
  };

  return (
    <div
      className="yk-equation"
      style={{
        position: "relative",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        margin: "18px 0",
        padding: "4px 0",
      }}
    >
      <div
        // KaTeX の自前レンダリング HTML(信頼できる出力)。
        dangerouslySetInnerHTML={{ __html: html }}
        style={{ overflowX: "auto", maxWidth: "100%" }}
      />
      {number ? (
        <span
          style={{
            position: "absolute",
            right: 0,
            fontFamily: "var(--pr-font-ui)",
            fontSize: 12,
            color: "var(--pr-text-muted)",
          }}
        >
          {number}
        </span>
      ) : null}
      <div
        className="yk-equation-actions"
        style={{
          position: "absolute",
          top: -6,
          right: 0,
          display: "flex",
          gap: 6,
        }}
      >
        <button
          type="button"
          style={{ ...actionStyle, color: "var(--pr-acc)" }}
          onClick={() => onExplain?.(latex)}
        >
          <AiMark />
          この式を説明
        </button>
        <button type="button" style={actionStyle} onClick={copyLatex}>
          LaTeXをコピー
        </button>
      </div>
    </div>
  );
}
