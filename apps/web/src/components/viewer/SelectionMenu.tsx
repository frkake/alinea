"use client";

import { useState, type CSSProperties } from "react";
import { AiMark } from "@/components/ui/AIBadge";

export type CopyFormat = "citation" | "plain";

export interface SelectionMenuProps {
  /** M0 は ✦AIに質問 / コピー の 2 項目のみ(plans/13 §1.5・未実装 UI は非表示)。 */
  milestone?: "M0";
  /** 選択元。M1+ の「語彙に追加」活性判定に使う(M0 では非表示)。 */
  side?: "source" | "translation";
  /** 選択矩形からの配置(ビューポート座標)。未指定時は相対配置(テスト・Storybook 用)。 */
  position?: { top: number; left: number };
  onAskAI?: () => void;
  onCopy?: (format: CopyFormat) => void;
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
  position,
  onAskAI,
  onCopy,
}: SelectionMenuProps) {
  const [copyOpen, setCopyOpen] = useState(false);
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
      <button type="button" role="menuitem" style={actionStyle} onClick={onAskAI}>
        <AiMark />
        AIに質問
      </button>
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
