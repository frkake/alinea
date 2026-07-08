"use client";

import { useEffect, useRef, useState, type CSSProperties, type KeyboardEvent } from "react";
import { Popover } from "@/components/ui/Popover";
import type { PdfDocumentMode, PdfFitMode, PdfSpreadFirstPageSide } from "@/stores/pdf-view-store";

const FIT_LABELS: Record<PdfFitMode, string> = {
  "fit-width": "幅に合わせる",
  "fit-page": "ページ全体",
  actual: "実寸(100%)",
};

const FIT_OPTIONS: PdfFitMode[] = ["fit-width", "fit-page", "actual"];

export interface PdfToolbarProps {
  page: number;
  pageCount: number | null;
  /** null = ロード中(§5.2「—%」)。 */
  zoomPct: number | null;
  fitMode: PdfFitMode | null;
  documentMode: PdfDocumentMode;
  translatedAvailable: boolean | null;
  bilingualAvailable: boolean | null;
  spread: boolean;
  spreadFirstPageSide: PdfSpreadFirstPageSide;
  /** null = 同期不能(「同期: —」)。 */
  syncDisplay: string | null;
  /** document/pdf 未解決(§5.2)。ページ入力・相互リンクを disabled にする。 */
  loading: boolean;
  onPageChange: (page: number) => void;
  onZoomIn: () => void;
  onZoomOut: () => void;
  onFitModeChange: (mode: PdfFitMode) => void;
  onDocumentModeChange: (mode: PdfDocumentMode) => void;
  onToggleSpread: () => void;
  onSpreadFirstPageSideChange: (side: PdfSpreadFirstPageSide) => void;
  onOpenInTranslation: () => void;
}

const iconBtn: CSSProperties = {
  border: "none",
  background: "transparent",
  cursor: "pointer",
  color: "var(--pr-text-muted)",
  fontFamily: "inherit",
  fontSize: 11.5,
  padding: 0,
};

const outlineBtn: CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  justifyContent: "center",
  height: 24,
  minWidth: 0,
  padding: "0 9px",
  border: "1px solid var(--pr-border-control)",
  borderRadius: 6,
  fontSize: 11,
  background: "transparent",
  cursor: "pointer",
  fontFamily: "inherit",
  whiteSpace: "nowrap",
  overflow: "hidden",
  textOverflow: "ellipsis",
};

/** PDF ツールバー(2a §4.2.3, h=38px)。 */
export function PdfToolbar({
  page,
  pageCount,
  zoomPct,
  fitMode,
  documentMode,
  translatedAvailable,
  bilingualAvailable,
  spread,
  spreadFirstPageSide,
  syncDisplay,
  loading,
  onPageChange,
  onZoomIn,
  onZoomOut,
  onFitModeChange,
  onDocumentModeChange,
  onToggleSpread,
  onSpreadFirstPageSideChange,
  onOpenInTranslation,
}: PdfToolbarProps) {
  const [pageInput, setPageInput] = useState(String(page));
  const fitAnchor = useRef<HTMLButtonElement>(null);
  const [fitOpen, setFitOpen] = useState(false);

  // page が外部から変わったら入力欄も追従(サムネイル選択・ページ移動ボタン等)。
  useEffect(() => {
    setPageInput(String(page));
  }, [page]);

  const commitPage = (raw: string) => {
    const n = Number.parseInt(raw, 10);
    if (Number.isNaN(n) || n < 1 || (pageCount != null && n > pageCount)) {
      setPageInput(String(page));
      return;
    }
    onPageChange(n);
  };

  const onInputKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") {
      commitPage(pageInput);
    }
  };

  return (
    <div
      style={{
        minHeight: 38,
        flex: "none",
        background: "var(--pr-bg-card)",
        borderBottom: "1px solid var(--pr-border-header)",
        display: "flex",
        alignItems: "center",
        flexWrap: "wrap",
        gap: "6px 10px",
        padding: "6px 10px",
        fontFamily: "var(--pr-font-ui)",
        overflow: "hidden",
      }}
    >
      <div
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: 7,
          fontSize: 11.5,
          color: "var(--pr-text-mid)",
          flex: "none",
        }}
      >
        <button
          type="button"
          aria-label="先頭ページ"
          style={iconBtn}
          disabled={loading || page <= 1}
          onClick={() => onPageChange(1)}
        >
          «
        </button>
        <button
          type="button"
          aria-label="前のページ"
          style={iconBtn}
          disabled={loading || page <= 1}
          onClick={() => onPageChange(Math.max(1, page - (spread ? 2 : 1)))}
        >
          ‹
        </button>
        <input
          type="text"
          inputMode="numeric"
          aria-label="ページ番号"
          value={pageInput}
          disabled={loading}
          onChange={(e) => setPageInput(e.target.value)}
          onKeyDown={onInputKeyDown}
          onBlur={(e) => commitPage(e.target.value)}
          onFocus={(e) => e.target.select()}
          style={{
            width: 34,
            border: "1px solid var(--pr-border-control)",
            borderRadius: 4,
            padding: "1px 8px",
            background: "var(--pr-bg-card)",
            fontWeight: 600,
            fontSize: 11.5,
            textAlign: "center",
            fontFamily: "inherit",
            color: "var(--pr-text)",
          }}
        />
        <span style={{ whiteSpace: "nowrap" }}>/ {pageCount ?? "…"}</span>
        <button
          type="button"
          aria-label="次のページ"
          style={iconBtn}
          disabled={loading || (pageCount != null && page >= pageCount)}
          onClick={() => onPageChange(Math.min(pageCount ?? page, page + (spread ? 2 : 1)))}
        >
          ›
        </button>
        <button
          type="button"
          aria-label="最終ページ"
          style={iconBtn}
          disabled={loading || pageCount == null || page >= pageCount}
          onClick={() => pageCount != null && onPageChange(pageCount)}
        >
          »
        </button>
      </div>

      <span style={{ width: 1, height: 16, background: "var(--pr-border-card)", flex: "none" }} />

      <div
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: 9,
          fontSize: 11.5,
          color: "var(--pr-text-mid)",
          flex: "none",
        }}
      >
        <button type="button" aria-label="縮小" style={iconBtn} onClick={onZoomOut}>
          −
        </button>
        <span>{zoomPct != null ? `${zoomPct}%` : "—%"}</span>
        <button type="button" aria-label="拡大" style={iconBtn} onClick={onZoomIn}>
          +
        </button>
      </div>

      <button
        ref={fitAnchor}
        type="button"
        aria-haspopup="menu"
        aria-expanded={fitOpen}
        style={{ ...outlineBtn, gap: 5, color: "var(--pr-text-mid)", maxWidth: 142 }}
        onClick={() => setFitOpen((v) => !v)}
      >
        {fitMode ? FIT_LABELS[fitMode] : "幅に合わせる"}
        <span style={{ color: "var(--pr-text-muted)", fontSize: 8.5 }}>▾</span>
      </button>
      <Popover
        open={fitOpen}
        onClose={() => setFitOpen(false)}
        anchorRef={fitAnchor}
        width={180}
        placement="bottom-start"
        caret={false}
      >
        {FIT_OPTIONS.map((m) => (
          <button
            key={m}
            type="button"
            role="menuitem"
            onClick={() => {
              onFitModeChange(m);
              setFitOpen(false);
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
              color: m === fitMode ? "var(--pr-acc)" : "var(--pr-text-mid)",
              fontWeight: m === fitMode ? 600 : 400,
            }}
          >
            {FIT_LABELS[m]}
          </button>
        ))}
      </Popover>

      <div
        role="group"
        aria-label="PDF種別"
        style={{ display: "inline-flex", flex: "none", minWidth: 0 }}
      >
        {(["source", "translated", "bilingual"] as const).map((mode) => {
          const active = documentMode === mode;
          const label = mode === "source" ? "原文" : mode === "translated" ? "日本語" : "対訳";
          const available =
            mode === "source"
              ? true
              : mode === "translated"
                ? translatedAvailable !== false
                : bilingualAvailable !== false;
          return (
            <button
              key={mode}
              type="button"
              aria-pressed={active}
              disabled={!available}
              title={
                mode === "bilingual"
                  ? available
                    ? "原文PDFと日本語PDFを左右に表示"
                    : "日本語PDFはまだ生成されていません"
                  : available
                    ? `${label}PDF`
                    : `${label}PDFはまだ生成されていません`
              }
              onClick={() => onDocumentModeChange(mode)}
              style={{
                ...outlineBtn,
                height: 24,
                padding: "0 7px",
                borderRadius:
                  mode === "source" ? "6px 0 0 6px" : mode === "bilingual" ? "0 6px 6px 0" : 0,
                borderLeftWidth: mode === "source" ? 1 : 0,
                color: active ? "var(--pr-acc)" : "var(--pr-text-sub)",
                borderColor: active ? "var(--pr-acc-m)" : "var(--pr-border-control)",
                background: active ? "var(--pr-acc-s)" : "transparent",
                fontWeight: active ? 700 : 500,
                opacity: available ? 1 : 0.45,
              }}
            >
              {label}
            </button>
          );
        })}
      </div>

      <button
        type="button"
        aria-pressed={spread}
        onClick={onToggleSpread}
        style={{
          ...outlineBtn,
          color: spread ? "var(--pr-acc)" : "var(--pr-text-sub)",
          borderColor: spread ? "var(--pr-acc-m)" : "var(--pr-border-control)",
          background: spread ? "var(--pr-acc-s)" : "transparent",
          fontWeight: spread ? 600 : 400,
        }}
      >
        見開き
      </button>

      {spread ? (
        <div
          role="group"
          aria-label="見開きの1ページ目の位置"
          title="見開きで1ページ目を置く位置"
          style={{
            display: "inline-flex",
            alignItems: "center",
            minWidth: 0,
            flex: "0 1 auto",
          }}
        >
          {(["left", "right"] as const).map((side) => {
            const active = spreadFirstPageSide === side;
            return (
              <button
                key={side}
                type="button"
                aria-pressed={active}
                onClick={() => onSpreadFirstPageSideChange(side)}
                style={{
                  ...outlineBtn,
                  height: 24,
                  padding: "0 7px",
                  borderRadius: side === "left" ? "6px 0 0 6px" : "0 6px 6px 0",
                  borderLeftWidth: side === "right" ? 0 : 1,
                  color: active ? "var(--pr-acc)" : "var(--pr-text-sub)",
                  borderColor: active ? "var(--pr-acc-m)" : "var(--pr-border-control)",
                  background: active ? "var(--pr-acc-s)" : "transparent",
                  fontWeight: active ? 700 : 500,
                }}
              >
                1P {side === "left" ? "左" : "右"}
              </button>
            );
          })}
        </div>
      ) : null}

      <div style={{ flex: "1 1 24px", minWidth: 0 }} />

      <span
        title={syncDisplay ? `同期: ${syncDisplay}` : "同期: —"}
        style={{
          fontSize: 11,
          color: "var(--pr-text-muted)",
          minWidth: 0,
          maxWidth: 170,
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
        }}
      >
        同期:{" "}
        {syncDisplay ? (
          <b style={{ color: "var(--pr-text-mid)", fontWeight: 600 }}>{syncDisplay}</b>
        ) : (
          "—"
        )}
      </span>

      <button
        type="button"
        disabled={loading}
        onClick={onOpenInTranslation}
        style={{
          ...outlineBtn,
          borderColor: "var(--pr-acc-m)",
          color: "var(--pr-acc)",
          background: "var(--pr-acc-s)",
          fontWeight: 600,
          opacity: loading ? 0.5 : 1,
          maxWidth: 180,
          flex: "0 1 auto",
        }}
      >
        この位置を訳文で開く →
      </button>
    </div>
  );
}
