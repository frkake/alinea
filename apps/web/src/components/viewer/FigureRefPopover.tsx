"use client";

import type { CSSProperties } from "react";
import type { FigureItem } from "@alinea/api-client";
import { AiMark } from "@/components/ui/AIBadge";

export interface FigureRefPopoverProps {
  /** undefined/null = 未着(ローディング表示)。 */
  figure: FigureItem | null | undefined;
  /** figures クエリ取得中(1c §5.3)。 */
  loading?: boolean;
  /** figures クエリ失敗(1c §5.3)。 */
  error?: boolean;
  onJumpToFigure?: (blockId: string) => void;
  onZoom?: (figure: FigureItem) => void;
  onExplain?: (figure: FigureItem) => void;
  onRetry?: () => void;
}

const btnBase: CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: 4,
  height: 23,
  padding: "0 10px",
  borderRadius: 5,
  fontSize: 10.5,
  fontFamily: "var(--pr-font-ui)",
  cursor: "pointer",
};

/**
 * 図参照ポップオーバー本体(1c §4.5)。両言語キャプション+「図の位置へ移動 →」「拡大」
 * 「✦ この図を説明」。配置(共通 Popover へのマウント)は呼び出し側の責務。
 */
export function FigureRefPopover({
  figure,
  loading = false,
  error = false,
  onJumpToFigure,
  onZoom,
  onExplain,
  onRetry,
}: FigureRefPopoverProps) {
  const targetName = figure?.display ?? "図表";
  return (
    <div
      role="dialog"
      aria-label="図表参照"
      style={{ width: 400, fontFamily: "var(--pr-font-ui)", background: "var(--pr-bg-pop)" }}
    >
      {/* 画像領域 */}
      <div style={{ padding: "12px 14px 0" }}>
        {error ? (
          <ImageBox>
            <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 8 }}>
              <span style={{ color: "var(--pr-text-thumb)", fontSize: 11.5 }}>図を読み込めませんでした</span>
              <button
                type="button"
                style={{ ...btnBase, border: "1px solid var(--pr-border-pop)", color: "var(--pr-text-mid)", background: "transparent" }}
                onClick={onRetry}
              >
                再試行
              </button>
            </div>
          </ImageBox>
        ) : loading || !figure ? (
          <ImageBox>
            <span style={{ color: "var(--pr-text-thumb)", fontSize: 11.5 }}>読み込み中…</span>
          </ImageBox>
        ) : figure.image_url ? (
          <img
            src={figure.image_url}
            alt={figure.caption_en}
            style={{ width: "100%", height: 170, objectFit: "contain", borderRadius: 6, background: "var(--pr-bg-thumb)", border: "1px solid var(--pr-border-thumb)" }}
          />
        ) : (
          <ImageBox>
            <span style={{ color: "var(--pr-text-thumb)", fontSize: 11.5, letterSpacing: "0.4px" }}>
              {figure.display}(原論文の画像)
            </span>
          </ImageBox>
        )}
      </div>

      {/* 下部: キャプション+ボタン */}
      <div style={{ padding: "10px 14px 12px", display: "flex", flexDirection: "column", gap: 6 }}>
        {loading || !figure ? (
          <>
            <CaptionSkeleton width="92%" height={10} />
            <CaptionSkeleton width="70%" height={12} />
          </>
        ) : (
          <>
            {figure.caption_ja ? (
              <div style={{ fontFamily: "var(--pr-jp)", fontSize: 12, lineHeight: 1.8, color: "var(--pr-text-body)" }}>
                {figure.caption_ja}
              </div>
            ) : null}
            <div
              style={{
                fontFamily: "var(--pr-font-en)",
                fontStyle: "italic",
                fontSize: 10.5,
                lineHeight: 1.6,
                color: "var(--pr-text-sub2)",
              }}
            >
              {figure.caption_en}
            </div>
            <div style={{ display: "flex", gap: 6, paddingTop: 2 }}>
              <button
                type="button"
                style={{ ...btnBase, background: "var(--pr-acc)", color: "var(--pr-bg-app)", fontWeight: 700 }}
                onClick={() => onJumpToFigure?.(figure.block_id)}
              >
                {targetName}の位置へ移動 →
              </button>
              <button
                type="button"
                style={{ ...btnBase, border: "1px solid var(--pr-border-pop)", color: "var(--pr-text-mid)", background: "transparent" }}
                onClick={() => onZoom?.(figure)}
              >
                拡大
              </button>
              <button
                type="button"
                style={{ ...btnBase, border: "1px solid var(--pr-border-pop)", color: "var(--pr-acc)", background: "transparent", fontWeight: 600 }}
                onClick={() => onExplain?.(figure)}
              >
                <AiMark /> この図を説明
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function ImageBox({ children }: { children: React.ReactNode }) {
  return (
    <div
      style={{
        height: 170,
        borderRadius: 6,
        background: "var(--pr-bg-thumb)",
        border: "1px solid var(--pr-border-thumb)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
      }}
    >
      {children}
    </div>
  );
}

function CaptionSkeleton({ width, height }: { width: string; height: number }) {
  return (
    <div
      style={{
        width,
        height,
        borderRadius: 3,
        background: "var(--pr-bg-thumb)",
        animation: "alinea-pulse 1.2s ease-in-out infinite",
      }}
    />
  );
}
