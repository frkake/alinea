"use client";

import type { CSSProperties } from "react";

/**
 * ブロックホバーツールバー(1h §3.2 `BlockHoverToolbar`。plans/13 §4.2 M2-07 の
 * 成果物名は `ArticleBlockHover`)。ブロック右上に重なるダーク浮動バー(§4.7)。
 * `locked`(出典ブロック)は呼び出し側が描画自体を抑止する(§4.11)。
 */
export interface ArticleBlockHoverProps {
  visible: boolean;
  /** 書き直しジョブ進行中(§5.5)。true の間は単一表示「✦ 書き直し中…」で操作不能にする。 */
  rewriting: boolean;
  onRewriteClick: () => void;
  onRegenerate: () => void;
  onShowEvidence: () => void;
  hasEvidence: boolean;
}

const itemStyle: CSSProperties = {
  fontSize: 10.5,
  color: "var(--pr-elev-fg)",
  padding: "0 6px",
  border: "none",
  background: "transparent",
  cursor: "pointer",
  fontFamily: "inherit",
  whiteSpace: "nowrap",
};

export function ArticleBlockHover({
  visible,
  rewriting,
  onRewriteClick,
  onRegenerate,
  onShowEvidence,
  hasEvidence,
}: ArticleBlockHoverProps) {
  if (!visible) return null;
  return (
    <div
      role="toolbar"
      aria-label="ブロック操作"
      style={{
        position: "absolute",
        top: -14,
        right: 0,
        display: "flex",
        alignItems: "center",
        gap: 2,
        background: "var(--pr-elev-bg)",
        borderRadius: 7,
        padding: "4px 6px",
        boxShadow: "0 8px 22px rgba(20,22,26,0.30)",
        zIndex: 1,
      }}
    >
      {rewriting ? (
        <span style={itemStyle}>✦ 書き直し中…</span>
      ) : (
        <>
          <button type="button" style={itemStyle} onClick={onRewriteClick}>
            ✦ 書き直し指示
          </button>
          <button type="button" style={itemStyle} onClick={onRegenerate}>
            再生成
          </button>
          {hasEvidence ? (
            <button type="button" style={itemStyle} onClick={onShowEvidence}>
              根拠を表示
            </button>
          ) : null}
        </>
      )}
    </div>
  );
}
