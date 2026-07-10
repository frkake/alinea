"use client";

import { useState } from "react";
import type { FigureContentOut, FigureLinkCardOut } from "@alinea/api-client";
import { renderMarkdownLite } from "@/components/vocab/markdown-lite";
import type { AnchorRef } from "@/components/viewer/article/types";

function SourceTable({ rows }: { rows: string[][] }) {
  return (
    <div
      style={{
        margin: "14px 16px 0",
        overflowX: "auto",
        border: "1px solid var(--pr-border-thumb)",
        borderRadius: 6,
      }}
    >
      <table
        style={{
          width: "max-content",
          minWidth: "100%",
          borderCollapse: "collapse",
          fontFamily: "var(--pr-font-ui)",
          fontSize: 11,
          lineHeight: 1.5,
          color: "var(--pr-text-body)",
        }}
      >
        <tbody>
          {rows.map((row, rowIndex) => (
            <tr key={rowIndex} style={{ background: rowIndex < 2 ? "var(--pr-bg-app)" : "transparent" }}>
              {row.map((cell, cellIndex) => {
                const Cell = rowIndex < 2 ? "th" : "td";
                return (
                  <Cell
                    key={cellIndex}
                    style={{
                      padding: "7px 9px",
                      borderRight: "1px solid var(--pr-border-hair)",
                      borderBottom: "1px solid var(--pr-border-hair)",
                      textAlign: cellIndex === 0 ? "left" : "right",
                      whiteSpace: "nowrap",
                      fontWeight: rowIndex < 2 ? 700 : 400,
                    }}
                  >
                    {cell || "—"}
                  </Cell>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** 図表埋め込みブロック。横長図は潰さず、表は構造化データとして描画する。 */
export function FigureEmbedBlock({
  figure,
  anchor,
  onJumpToAnchor,
}: {
  figure: FigureContentOut;
  anchor?: AnchorRef | null;
  onJumpToAnchor?: (anchor: AnchorRef) => void;
}) {
  const [wideImage, setWideImage] = useState(false);
  const [imageError, setImageError] = useState(false);
  const isTable = figure.kind === "table";

  return (
    <div
      style={{
        border: "1px solid var(--pr-border-card)",
        borderRadius: 10,
        background: "var(--pr-bg-card)",
        overflow: "hidden",
      }}
    >
      {isTable && figure.table_rows?.length ? (
        <SourceTable rows={figure.table_rows} />
      ) : figure.image_url && !imageError ? (
        <div style={{ margin: "14px 16px 0", overflowX: "auto", borderRadius: 6 }}>
          <img
            src={figure.image_url}
            alt={figure.caption_ja}
            onLoad={(event) => {
              const image = event.currentTarget;
              setWideImage(image.naturalWidth / Math.max(image.naturalHeight, 1) > 3.2);
            }}
            onError={() => setImageError(true)}
            style={{
              display: "block",
              width: wideImage ? "auto" : "100%",
              minWidth: wideImage ? 960 : undefined,
              maxWidth: wideImage ? "none" : "100%",
              height: "auto",
              borderRadius: 6,
              border: "1px solid var(--pr-border-thumb)",
              objectFit: "contain",
            }}
          />
        </div>
      ) : (
        <div
          style={{
            margin: "14px 16px 0",
            minHeight: 110,
            borderRadius: 6,
            border: "1px dashed var(--pr-border-thumb)",
            background: "var(--pr-bg-thumb)",
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            justifyContent: "center",
            gap: 8,
            color: "var(--pr-text-thumb)",
            fontSize: 11,
          }}
        >
          <span>{isTable ? "表データを表示できません" : "原論文の図画像を表示できません"}</span>
          {anchor && onJumpToAnchor ? (
            <button
              type="button"
              onClick={() => onJumpToAnchor(anchor)}
              style={{
                border: "none",
                background: "transparent",
                color: "var(--pr-a)",
                fontFamily: "inherit",
                fontSize: 11,
                fontWeight: 700,
                cursor: "pointer",
              }}
            >
              原文で確認する →
            </button>
          ) : null}
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
              minHeight: 15,
              padding: "1px 6px",
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

/** 転載不可時の代替リンクカード。 */
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
