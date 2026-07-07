"use client";

import { useEffect, useRef } from "react";
import type { TocNode } from "@yakudoku/api-client";
import { SegmentedControl } from "@/components/ui/SegmentedControl";
import { TocRowGroup } from "@/components/viewer/TocTree";
import { usePdfViewStore } from "@/stores/pdf-view-store";
import { PdfThumbnail } from "./PdfThumbnail";
import { usePdfDocumentContext } from "./use-pdf-document";

const SIDEBAR_TAB_OPTIONS = [
  { value: "toc", label: "目次" },
  { value: "pages", label: "ページ" },
] as const;

export interface PdfSidebarProps {
  toc: TocNode[];
  activeSectionId: string | null;
  onSectionClick: (sectionId: string) => void;
  onTranslateAppendix: (sectionId: string) => void;
  /** pdf.js 解決前のプレースホルダ(`viewer.revision.page_count`。2a §2.1 決定)。 */
  pageCountFallback: number | null;
  pdfDownloadHref: string;
}

/**
 * PDF モードの左サイドバー(2a §4.2.2, w=232px)。目次/ページサムネイル切替。
 * `PdfPane` と同じ `PdfDocumentProvider`/`usePdfViewStore` を共有する兄弟コンポーネント
 * (ページ番号・pdf.js ドキュメントは props で受け渡さず、共有ストア/Context から直接読む)。
 */
export function PdfSidebar({
  toc,
  activeSectionId,
  onSectionClick,
  onTranslateAppendix,
  pageCountFallback,
  pdfDownloadHref,
}: PdfSidebarProps) {
  const tab = usePdfViewStore((s) => s.sidebarTab);
  const setTab = usePdfViewStore((s) => s.setSidebarTab);
  const currentPage = usePdfViewStore((s) => s.page);
  const setPage = usePdfViewStore((s) => s.setPage);
  const pdf = usePdfDocumentContext();
  const listRef = useRef<HTMLDivElement>(null);

  const pageCount = pdf.numPages ?? pageCountFallback;

  useEffect(() => {
    const container = listRef.current;
    if (!container) return;
    const el = container.querySelector<HTMLElement>(`[data-thumb-page="${currentPage}"]`);
    if (el) el.scrollIntoView({ block: "nearest" });
  }, [currentPage]);

  const regular = toc.filter((n) => !n.on_demand);
  const onDemand = toc.filter((n) => n.on_demand);

  return (
    <nav
      aria-label="PDF サイドバー"
      style={{
        width: 232,
        flex: "none",
        background: "var(--pr-bg-pane)",
        borderRight: "1px solid var(--pr-border-pane)",
        display: "flex",
        flexDirection: "column",
        padding: "10px 8px 8px",
        minHeight: 0,
      }}
    >
      <div style={{ margin: "0 6px 10px" }}>
        <SegmentedControl
          options={SIDEBAR_TAB_OPTIONS}
          value={tab}
          onChange={setTab}
          size="sm"
          ariaLabel="目次/ページ"
        />
      </div>

      {tab === "toc" ? (
        <div
          style={{
            marginTop: 10,
            display: "flex",
            flexDirection: "column",
            gap: 1,
            fontSize: 12.3,
            color: "var(--pr-text-nav)",
            flex: 1,
            overflowY: "auto",
          }}
        >
          {regular.map((node) => (
            <TocRowGroup
              key={node.section_id}
              node={node}
              activeSectionId={activeSectionId}
              onSectionClick={onSectionClick}
            />
          ))}
          {onDemand.map((node) => (
            <div
              key={node.section_id}
              role="button"
              tabIndex={0}
              onClick={() => {
                onSectionClick(node.section_id);
                onTranslateAppendix(node.section_id);
              }}
              style={{
                margin: "8px 6px 0",
                border: "1px dashed var(--pr-border-dashed)",
                borderRadius: 6,
                padding: "8px 9px",
                display: "flex",
                flexDirection: "column",
                gap: 5,
                cursor: "pointer",
              }}
            >
              <span style={{ fontSize: 11.5, color: "var(--pr-text-sub)" }}>
                {node.number ? `${node.number} ` : ""}
                {node.title_ja ?? node.title_en} <span style={{ color: "var(--pr-text-muted)" }}>— 未翻訳</span>
              </span>
              <span style={{ fontSize: 10.5, color: "var(--pr-text-muted)", lineHeight: 1.5 }}>
                開くと翻訳します(オンデマンド)
              </span>
            </div>
          ))}
        </div>
      ) : (
        <div
          ref={listRef}
          style={{
            marginTop: 10,
            flex: 1,
            overflowY: "auto",
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            gap: 12,
            paddingTop: 2,
          }}
        >
          {pageCount == null
            ? null
            : Array.from({ length: pageCount }, (_, i) => i + 1).map((p) => (
                <div key={p} data-thumb-page={p}>
                  <PdfThumbnail
                    pageNumber={p}
                    selected={p === currentPage}
                    onClick={() => setPage(p)}
                    getPage={pdf.getPage}
                    scrollRootRef={listRef}
                  />
                </div>
              ))}
        </div>
      )}

      <div
        style={{
          padding: "8px 8px 2px",
          borderTop: "1px solid var(--pr-border-pane)",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          fontSize: 10.5,
          color: "var(--pr-text-muted)",
        }}
      >
        <span>
          {pageCount ?? "…"} ページ · {pdf.fileSizeMb != null ? `${pdf.fileSizeMb} MB` : "…"}
        </span>
        <a href={pdfDownloadHref} download style={{ color: "var(--pr-text-muted)", textDecoration: "none" }}>
          ⤓ 原文PDF
        </a>
      </div>
    </nav>
  );
}
