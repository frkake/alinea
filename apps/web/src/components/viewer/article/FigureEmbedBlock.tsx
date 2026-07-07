"use client";

import type { FigureContentOut, FigureLinkCardOut } from "@yakudoku/api-client";
import { renderMarkdownLite } from "@/components/vocab/markdown-lite";
import type { AnchorRef } from "@/components/viewer/article/types";

/** 図表埋め込みブロック(1h §4.9)。転載可の実データ画像+出典+ライセンスバッジ。 */
export function FigureEmbedBlock({ figure }: { figure: FigureContentOut }) {
  return (
    <div
      style={{
        border: "1px solid var(--pr-border-card)",
        borderRadius: 10,
        background: "var(--pr-bg-card)",
        overflow: "hidden",
      }}
    >
      {figure.image_url ? (
        <img
          src={figure.image_url}
          alt={figure.caption_ja}
          style={{
            display: "block",
            margin: "14px 16px 0",
            width: "calc(100% - 32px)",
            height: "auto",
            borderRadius: 6,
            border: "1px solid var(--pr-border-thumb)",
          }}
        />
      ) : (
        <div
          style={{
            margin: "14px 16px 0",
            height: 150,
            borderRadius: 6,
            border: "1px solid var(--pr-border-thumb)",
            background: "var(--pr-bg-thumb)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            color: "var(--pr-text-thumb)",
            fontSize: 11,
          }}
        >
          図(原論文の画像)
        </div>
      )}
      <div style={{ padding: "10px 16px 12px", display: "flex", flexDirection: "column", gap: 5 }}>
        <div
          style={{
            fontFamily: "var(--pr-jp, 'Noto Serif JP'), serif",
            fontSize: 12,
            lineHeight: 1.75,
            color: "var(--pr-text-body)",
          }}
        >
          {figure.caption_ja}
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 7, fontSize: 9.5, color: "var(--pr-text-muted)" }}>
          <span>{renderMarkdownLite(figure.credit)}</span>
          <span
            style={{
              display: "inline-flex",
              alignItems: "center",
              height: 15,
              padding: "0 6px",
              borderRadius: 3,
              background: "var(--pr-src-note-bg)",
              color: "var(--pr-src-note-fg)",
              fontWeight: 700,
            }}
          >
            {figure.license_badge}
          </span>
          <span>クレジット自動付記</span>
        </div>
      </div>
    </div>
  );
}

/** 転載不可時の代替リンクカード(1h §4.9 決定。デザイン未描画)。 */
export function FigureLinkCardBlock({
  card,
  anchor,
  onJumpToAnchor,
}: {
  card: FigureLinkCardOut;
  anchor: AnchorRef | null;
  onJumpToAnchor: (anchor: AnchorRef) => void;
}) {
  return (
    <div
      style={{
        border: "1px solid var(--pr-border-card)",
        borderRadius: 10,
        background: "var(--pr-bg-card)",
        padding: "14px 16px",
        display: "flex",
        flexDirection: "column",
        gap: 6,
      }}
    >
      <div style={{ fontSize: 12, color: "var(--pr-text-sub)", lineHeight: 1.7 }}>{card.message}</div>
      <button
        type="button"
        disabled={!anchor}
        onClick={() => anchor && onJumpToAnchor(anchor)}
        style={{
          alignSelf: "flex-start",
          border: "none",
          background: "transparent",
          padding: 0,
          cursor: anchor ? "pointer" : "default",
          fontFamily: "inherit",
          fontSize: 11,
          color: "var(--pr-a)",
          fontWeight: 600,
        }}
      >
        原文で{card.figure_display}を見る →
      </button>
    </div>
  );
}
