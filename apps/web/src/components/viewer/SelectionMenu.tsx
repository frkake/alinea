"use client";

import { useState, type CSSProperties } from "react";
import { AiMark } from "@/components/ui/AIBadge";
import type { HighlightColor } from "@/components/ui/HighlightMark";

export type CopyFormat = "citation" | "plain";

/** 4 色ドット(plans/09-screens/1b §4.5-6・docs/04 §9)。順序固定。 */
const HIGHLIGHT_COLORS: ReadonlyArray<{ color: HighlightColor; hex: string; label: string }> = [
  { color: "important", hex: "#C49432", label: "重要" },
  { color: "question", hex: "#5884AA", label: "疑問" },
  { color: "idea", hex: "#659471", label: "アイデア" },
  { color: "term", hex: "#82827E", label: "用語" },
];

export interface SelectionMenuProps {
  /**
   * M0 は ✦AIに質問 / コピー の 2 項目のみ。M1 で 4 色ハイライト・コメントを追加。M2 で
   * 「語彙に追加」を追加(plans/13 §1.5 の段階公開規則。M2-12)。既定は後方互換のため "M0"。
   * `TranslationPane.tsx` は `milestone="M2"` + `onAddVocab`(`vocab-context.ts` で文脈センテンス
   * 抽出 → `POST /api/vocab`)を渡す(M2-17 followup: M2-12 は本項目の呼び出し側配線を
   * 明示的に所有範囲外としていたため未配線だった。deviations 参照)。他パネル(記事モード等)は
   * 引き続き M0/M1 のまま。
   */
  milestone?: "M0" | "M1" | "M2";
  /**
   * 選択元。「語彙に追加」の活性判定に使う — `side='source'` のみ活性、`'translation'` は
   * 非活性(1b §5.5 決定)。
   */
  side?: "source" | "translation";
  /** 選択矩形からの配置(ビューポート座標)。未指定時は相対配置(テスト・Storybook 用)。 */
  position?: { top: number; left: number };
  onAskAI?: () => void;
  onCopy?: (format: CopyFormat) => void;
  /** 色ドットクリック(1b §5.5)。 */
  onHighlight?: (color: HighlightColor) => void;
  /** コメント入力ポップの「保存」(1b §5.5。空文字はコメント無しハイライトとして作成)。 */
  onComment?: (color: HighlightColor, comment: string) => void;
  /**
   * 「語彙に追加」クリック(milestone="M2" かつ `side==='source'` の時のみ有効。1b §5.5)。
   * 実際の `POST /api/vocab` 呼び出し・文脈センテンス抽出・409 トーストは呼び出し側の責務
   * (plans/09-screens/1b `SelectionController` の所有範囲。M2-12 の所有外)。
   */
  onAddVocab?: () => void;
}

const menuStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 2,
  background: "var(--pr-elev-bg)",
  borderRadius: 8,
  padding: "5px 7px",
  boxShadow: "var(--pr-shadow-menu)",
  zIndex: "var(--z-selection-menu)" as unknown as number,
};

const actionStyle: CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: 4,
  border: "none",
  background: "transparent",
  cursor: "pointer",
  fontFamily: "var(--pr-font-ui)",
  fontSize: 11.5,
  color: "var(--pr-elev-fg)",
  padding: "0 6px",
  height: 22,
  borderRadius: 4,
};

/**
 * テキスト選択メニュー(1b §4.5-6 / §5.5)。ダークフローティングツールバー。
 * M0 は ✦AIに質問 と コピー の 2 項目のみ(色ドット・コメント・語彙追加は M1+ で非表示)。
 */
export function SelectionMenu({
  milestone = "M0",
  side,
  position,
  onAskAI,
  onCopy,
  onHighlight,
  onComment,
  onAddVocab,
}: SelectionMenuProps) {
  const [copyOpen, setCopyOpen] = useState(false);
  const [commentOpen, setCommentOpen] = useState(false);
  const [commentColor, setCommentColor] = useState<HighlightColor>("important");
  const [commentText, setCommentText] = useState("");
  const positioned: CSSProperties = position
    ? { position: "fixed", top: position.top, left: position.left }
    : { position: "relative" };

  return (
    <div
      role="menu"
      aria-label="選択メニュー"
      data-milestone={milestone}
      style={{ ...menuStyle, ...positioned }}
    >
      {milestone === "M1" || milestone === "M2" ? (
        <>
          {HIGHLIGHT_COLORS.map(({ color, hex, label }) => (
            <button
              key={color}
              type="button"
              role="menuitem"
              aria-label={`${label}でハイライト`}
              title={label}
              onClick={() => onHighlight?.(color)}
              style={{
                width: 15,
                height: 15,
                borderRadius: "50%",
                background: hex,
                border: "none",
                margin: "0 2px",
                cursor: "pointer",
                padding: 0,
              }}
            />
          ))}
          <div
            style={{
              width: 1,
              height: 14,
              background: "var(--pr-elev-divider)",
              margin: "0 5px",
            }}
          />
          <div style={{ position: "relative" }}>
            <button
              type="button"
              role="menuitem"
              aria-haspopup="dialog"
              aria-expanded={commentOpen}
              style={actionStyle}
              onClick={() => {
                setCommentOpen((v) => !v);
              }}
            >
              コメント
            </button>
            {commentOpen ? (
              <div
                role="dialog"
                aria-label="コメントを入力"
                onKeyDown={(e) => {
                  if (e.key === "Escape") {
                    // 入力ポップのみ閉じ、選択メニューへ戻る(内容は破棄。1b §5.5)。
                    e.stopPropagation();
                    setCommentOpen(false);
                    setCommentText("");
                  }
                }}
                style={{
                  position: "absolute",
                  top: "calc(100% + 4px)",
                  left: 0,
                  width: 280,
                  background: "var(--pr-bg-card)",
                  border: "1px solid var(--pr-border-pop)",
                  borderRadius: 8,
                  boxShadow: "var(--pr-shadow-pop)",
                  padding: 10,
                  display: "flex",
                  flexDirection: "column",
                  gap: 8,
                  zIndex: 1,
                }}
              >
                <textarea
                  aria-label="コメント本文"
                  rows={3}
                  value={commentText}
                  onChange={(e) => setCommentText(e.target.value)}
                  style={{
                    width: "100%",
                    boxSizing: "border-box",
                    fontFamily: "var(--pr-font-ui)",
                    fontSize: 12,
                    border: "1px solid var(--pr-border-control)",
                    borderRadius: 6,
                    padding: 6,
                    resize: "none",
                  }}
                />
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  {HIGHLIGHT_COLORS.map(({ color, hex, label }) => (
                    <button
                      key={color}
                      type="button"
                      aria-label={`${label}を選択`}
                      aria-pressed={commentColor === color}
                      onClick={() => setCommentColor(color)}
                      style={{
                        width: 15,
                        height: 15,
                        borderRadius: "50%",
                        background: hex,
                        border:
                          commentColor === color
                            ? "2px solid var(--pr-text)"
                            : "1px solid transparent",
                        cursor: "pointer",
                        padding: 0,
                      }}
                    />
                  ))}
                  <button
                    type="button"
                    onClick={() => {
                      onComment?.(commentColor, commentText.trim());
                      setCommentOpen(false);
                      setCommentText("");
                    }}
                    style={{
                      marginLeft: "auto",
                      height: 24,
                      padding: "0 10px",
                      border: "none",
                      borderRadius: 6,
                      background: "var(--pr-acc)",
                      color: "#FFFFFF",
                      fontSize: 11.5,
                      fontWeight: 600,
                      cursor: "pointer",
                      fontFamily: "inherit",
                    }}
                  >
                    保存
                  </button>
                </div>
              </div>
            ) : null}
          </div>
        </>
      ) : null}
      <button type="button" role="menuitem" style={actionStyle} onClick={onAskAI}>
        <AiMark />
        AIに質問
      </button>
      {milestone === "M2" ? (
        <button
          type="button"
          role="menuitem"
          disabled={side !== "source"}
          title={side !== "source" ? "原文(英語)の選択でのみ使えます" : undefined}
          onClick={() => onAddVocab?.()}
          style={{
            ...actionStyle,
            opacity: side !== "source" ? 0.45 : 1,
            cursor: side !== "source" ? "default" : "pointer",
          }}
        >
          語彙に追加
        </button>
      ) : null}
      <div style={{ position: "relative" }}>
        <button
          type="button"
          role="menuitem"
          aria-haspopup="menu"
          aria-expanded={copyOpen}
          style={actionStyle}
          onClick={() => {
            setCopyOpen((v) => !v);
          }}
        >
          コピー
        </button>
        {copyOpen ? (
          <div
            role="menu"
            aria-label="コピー形式"
            style={{
              position: "absolute",
              top: "calc(100% + 4px)",
              right: 0,
              display: "flex",
              flexDirection: "column",
              background: "var(--pr-elev-bg)",
              borderRadius: 8,
              padding: "4px 0",
              boxShadow: "var(--pr-shadow-menu)",
              minWidth: 150,
            }}
          >
            <button
              type="button"
              role="menuitem"
              style={{ ...actionStyle, padding: "5px 10px", justifyContent: "flex-start" }}
              onClick={() => {
                onCopy?.("citation");
                setCopyOpen(false);
              }}
            >
              引用形式でコピー
            </button>
            <button
              type="button"
              role="menuitem"
              style={{ ...actionStyle, padding: "5px 10px", justifyContent: "flex-start" }}
              onClick={() => {
                onCopy?.("plain");
                setCopyOpen(false);
              }}
            >
              プレーンでコピー
            </button>
          </div>
        ) : null}
      </div>
    </div>
  );
}
