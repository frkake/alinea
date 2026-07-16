"use client";

import { VocabSearchBox } from "@/components/vocab/VocabSearchBox";

export interface VocabHeaderProps {
  total: number;
  dueCount: number;
  searchValue: string;
  searchFetching: boolean;
  onSearchChange: (v: string) => void;
  onStartReview: () => void;
  reviewLoading: boolean;
  onExportMarkdown: () => void;
  /** Anki TSV エクスポートトリガ(S9)。 */
  onAnkiExport: () => void;
}

/** 見出し行(4d §4.2.3)。「語彙帳」「{n} 語 — 読んだ論文の文脈から」+ 検索 + Markdown/Anki エクスポート + 復習をはじめる。 */
export function VocabHeader({
  total,
  dueCount,
  searchValue,
  searchFetching,
  onSearchChange,
  onStartReview,
  reviewLoading,
  onExportMarkdown,
  onAnkiExport,
}: VocabHeaderProps) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
      <span style={{ fontSize: 16, fontWeight: 700 }}>語彙帳</span>
      <span style={{ fontSize: 11.5, color: "var(--pr-text-muted)" }}>
        {total} 語 — 読んだ論文の文脈から
      </span>
      <span style={{ flex: 1 }} />
      <VocabSearchBox value={searchValue} onChange={onSearchChange} fetching={searchFetching} />
      <button
        type="button"
        onClick={onExportMarkdown}
        aria-label="エクスポート (.md)"
        style={{
          display: "inline-flex",
          alignItems: "center",
          height: 28,
          padding: "0 13px",
          borderRadius: 6,
          border: "1px solid var(--pr-border-soft)",
          background: "transparent",
          color: "var(--pr-text-mid)",
          fontSize: 11.5,
          fontWeight: 500,
          fontFamily: "inherit",
          cursor: "pointer",
        }}
      >
        エクスポート (.md)
      </button>
      <button
        type="button"
        onClick={onAnkiExport}
        title="現在のフィルタ結果を Anki へ書き出す"
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: 7,
          height: 28,
          padding: "0 13px",
          borderRadius: 6,
          border: "1px solid var(--pr-border)",
          background: "var(--pr-bg-panel)",
          color: "var(--pr-text-mid)",
          fontSize: 11.5,
          fontWeight: 600,
          fontFamily: "inherit",
          cursor: "pointer",
        }}
      >
        Ankiへ書き出す
      </button>
      <button
        type="button"
        onClick={onStartReview}
        disabled={dueCount === 0 || reviewLoading}
        title={dueCount === 0 ? "復習期の語彙はありません" : undefined}
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: 7,
          height: 28,
          padding: "0 13px",
          borderRadius: 6,
          border: "none",
          background: "var(--pr-acc)",
          color: "#FFFFFF",
          fontSize: 11.5,
          fontWeight: 600,
          fontFamily: "inherit",
          cursor: dueCount === 0 ? "default" : "pointer",
          opacity: dueCount === 0 || reviewLoading ? 0.7 : 1,
        }}
      >
        復習をはじめる
        {dueCount > 0 ? (
          <span
            style={{
              fontSize: 9.5,
              fontWeight: 500,
              opacity: 0.8,
              border: "1px solid rgba(255,255,255,0.4)",
              borderRadius: 3,
              padding: "0 5px",
            }}
          >
            {dueCount}
          </span>
        ) : null}
      </button>
    </div>
  );
}
