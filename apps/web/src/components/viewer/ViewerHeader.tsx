"use client";

import { useRef, useState, type CSSProperties } from "react";
import type { ReadingStatus } from "@yakudoku/tokens";
import { QualityBadge } from "@/components/ui/QualityBadge";
import { StatusPill } from "@/components/ui/StatusPill";
import { SegmentedControl } from "@/components/ui/SegmentedControl";
import { SearchBox } from "@/components/ui/SearchBox";
import { Popover } from "@/components/ui/Popover";
import { useViewerStore, type TranslationStyle } from "@/stores/viewer-store";
import type { ViewerMode } from "@/components/viewer/ViewerShell";

/**
 * M1 の表示モードは 訳文 / 対訳 / 原文 / PDF の 4 つ(plans/13 §1.5・M1-20)。
 * 「記事」は M2-07 まで未実装のため非表示(グレーアウトしない。plans/13 の決定)。
 */
export const M1_MODE_OPTIONS = [
  { value: "translation", label: "訳文" },
  { value: "parallel", label: "対訳" },
  { value: "source", label: "原文" },
  { value: "pdf", label: "PDF" },
] as const satisfies ReadonlyArray<{ value: ViewerMode; label: string }>;

const STYLE_LABELS: Record<TranslationStyle, string> = {
  natural: "自然訳",
  literal: "直訳",
};

export interface ViewerHeaderProps {
  title: string;
  qualityLevel: "A" | "B";
  status: ReadingStatus;
  mode: ViewerMode;
  onModeChange: (mode: ViewerMode) => void;
  onStatusChange: (status: ReadingStatus) => void;
  onBack: () => void;
  /**
   * PDF アセット無し論文(2a §5.3)。true の間「PDF」セグメントを disabled にし、
   * tooltip「この論文には PDF がありません」を出す(非表示にはしない)。
   */
  pdfDisabled?: boolean;
}

/** ビューアヘッダ(viewer-shell §4)。M1 は 訳文/対訳/原文/PDF の 4 モード表示。 */
export function ViewerHeader({
  title,
  qualityLevel,
  status,
  mode,
  onModeChange,
  pdfDisabled = false,
  onStatusChange,
  onBack,
}: ViewerHeaderProps) {
  const style = useViewerStore((s) => s.style);
  const setStyle = useViewerStore((s) => s.setStyle);
  const panelOpen = useViewerStore((s) => s.panelOpen);
  const setPanel = useViewerStore((s) => s.setPanel);
  const searchQuery = useViewerStore((s) => s.searchQuery);
  const setSearchQuery = useViewerStore((s) => s.setSearchQuery);
  const openSearch = useViewerStore((s) => s.openSearch);
  const closeSearch = useViewerStore((s) => s.closeSearch);

  const styleAnchor = useRef<HTMLButtonElement>(null);
  const overflowAnchor = useRef<HTMLButtonElement>(null);
  const [styleOpen, setStyleOpen] = useState(false);
  const [overflowOpen, setOverflowOpen] = useState(false);

  const controlBtn: CSSProperties = {
    display: "inline-flex",
    alignItems: "center",
    gap: 5,
    height: 26,
    padding: "0 10px",
    border: "1px solid var(--pr-border-control)",
    borderRadius: 6,
    fontSize: 11.5,
    color: "var(--pr-text-mid)",
    background: "transparent",
    cursor: "pointer",
    fontFamily: "inherit",
  };

  return (
    <header
      style={{
        height: 52,
        flex: "none",
        background: "var(--pr-bg-card)",
        borderBottom: "1px solid var(--pr-border-header)",
        display: "flex",
        alignItems: "center",
        gap: 10,
        padding: "0 16px",
        fontFamily: "var(--pr-font-ui)",
      }}
    >
      <button
        type="button"
        aria-label="戻る"
        onClick={onBack}
        style={{
          width: 20,
          textAlign: "center",
          fontSize: 16,
          color: "var(--pr-text-icon)",
          border: "none",
          background: "transparent",
          cursor: "pointer",
        }}
      >
        ‹
      </button>

      <span
        title={title}
        style={{
          fontSize: 13,
          fontWeight: 600,
          maxWidth: 330,
          whiteSpace: "nowrap",
          overflow: "hidden",
          textOverflow: "ellipsis",
          color: "var(--pr-text)",
        }}
      >
        {title}
      </span>

      <QualityBadge level={qualityLevel} size={18} />

      <StatusPill status={status} size="md" interactive onChange={onStatusChange} />

      <div style={{ flex: 1 }} />

      <SegmentedControl
        options={M1_MODE_OPTIONS.map((opt) =>
          opt.value === "pdf" && pdfDisabled
            ? { ...opt, disabled: true, title: "この論文には PDF がありません" }
            : opt,
        )}
        value={mode}
        onChange={(v) => onModeChange(v as ViewerMode)}
        size="md"
        ariaLabel="表示モード"
      />

      <button
        ref={styleAnchor}
        type="button"
        aria-haspopup="menu"
        aria-expanded={styleOpen}
        style={controlBtn}
        onClick={() => setStyleOpen((v) => !v)}
      >
        スタイル: {STYLE_LABELS[style]}
        <span style={{ color: "var(--pr-text-muted)", fontSize: 9 }}>▾</span>
      </button>
      <Popover
        open={styleOpen}
        onClose={() => setStyleOpen(false)}
        anchorRef={styleAnchor}
        width={180}
        placement="bottom-end"
        caret={false}
      >
        {(["natural", "literal"] as TranslationStyle[]).map((s) => (
          <button
            key={s}
            type="button"
            role="menuitem"
            onClick={() => {
              setStyle(s);
              setStyleOpen(false);
            }}
            style={{
              display: "block",
              width: "100%",
              textAlign: "left",
              border: "none",
              background: "transparent",
              cursor: "pointer",
              fontFamily: "inherit",
              fontSize: 12,
              padding: "8px 12px",
              color: s === style ? "var(--pr-acc)" : "var(--pr-text-mid)",
              fontWeight: s === style ? 600 : 400,
            }}
          >
            {STYLE_LABELS[s]}
          </button>
        ))}
      </Popover>

      <SearchBox
        variant="in-paper"
        value={searchQuery}
        onChange={setSearchQuery}
        onFocusChange={(f) => (f ? openSearch() : closeSearch())}
        placeholder="この論文内を検索"
        shortcutLabel="/"
      />

      <button
        ref={overflowAnchor}
        type="button"
        aria-label="その他"
        aria-haspopup="menu"
        aria-expanded={overflowOpen}
        onClick={() => setOverflowOpen((v) => !v)}
        style={{
          fontSize: 15,
          color: "var(--pr-text-sub)",
          letterSpacing: 1,
          border: "none",
          background: "transparent",
          cursor: "pointer",
        }}
      >
        ⋯
      </button>
      <Popover
        open={overflowOpen}
        onClose={() => setOverflowOpen(false)}
        anchorRef={overflowAnchor}
        width={200}
        placement="bottom-end"
        caret={false}
      >
        <button
          type="button"
          role="menuitem"
          onClick={() => {
            setPanel(!panelOpen);
            setOverflowOpen(false);
          }}
          style={{
            display: "block",
            width: "100%",
            textAlign: "left",
            border: "none",
            background: "transparent",
            cursor: "pointer",
            fontFamily: "inherit",
            fontSize: 11.5,
            padding: "0 12px",
            height: 30,
            color: "var(--pr-text-mid)",
          }}
        >
          {panelOpen ? "サイドパネルを隠す" : "サイドパネルを表示"}
        </button>
      </Popover>
    </header>
  );
}
