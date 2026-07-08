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
  open?: boolean;
  onToggle?: (open: boolean) => void;
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
  open = true,
  onToggle,
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

  if (!open) {
    return (
      <PdfSidebarRail
        tab={tab}
        currentPage={currentPage}
        onOpen={(nextTab) => {
          setTab(nextTab);
          onToggle?.(true);
        }}
      />
    );
  }

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
        <div style={{ display: "flex", alignItems: "center", gap: 6, minWidth: 0 }}>
          <div style={{ minWidth: 0, flex: 1 }}>
            <SegmentedControl
              options={SIDEBAR_TAB_OPTIONS}
              value={tab}
              onChange={setTab}
              size="sm"
              ariaLabel="目次/ページ"
            />
          </div>
          <button
            type="button"
            aria-label="PDFサイドバーを折りたたむ"
            title="PDFサイドバーを折りたたむ"
            onClick={() => onToggle?.(false)}
            style={{
              width: 24,
              height: 24,
              flex: "none",
              border: "none",
              background: "transparent",
              color: "var(--pr-text-faint)",
              cursor: onToggle ? "pointer" : "default",
              opacity: onToggle ? 1 : 0.35,
              fontFamily: "inherit",
              fontSize: 12,
            }}
          >
            ⟨
          </button>
        </div>
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
          gap: 8,
          justifyContent: "space-between",
          fontSize: 10.5,
          color: "var(--pr-text-muted)",
          minWidth: 0,
        }}
      >
        <span style={{ minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {pageCount ?? "…"} ページ · {pdf.fileSizeMb != null ? `${pdf.fileSizeMb} MB` : "…"}
        </span>
        <a
          href={pdfDownloadHref}
          download
          style={{ color: "var(--pr-text-muted)", textDecoration: "none", flex: "none", whiteSpace: "nowrap" }}
        >
          ⤓ 原文PDF
        </a>
      </div>
    </nav>
  );
}

function PdfSidebarRail({
  tab,
  currentPage,
  onOpen,
}: {
  tab: "toc" | "pages";
  currentPage: number;
  onOpen: (tab: "toc" | "pages") => void;
}) {
  return (
    <nav
      aria-label="PDF サイドバー"
      style={{
        width: 44,
        flex: "none",
        background: "var(--pr-bg-pane)",
        borderRight: "1px solid var(--pr-border-pane)",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        padding: "10px 0",
        gap: 8,
      }}
    >
      <button
        type="button"
        aria-label="PDFサイドバーを開く"
        title="PDFサイドバーを開く"
        onClick={() => onOpen(tab)}
        style={{
          width: 28,
          height: 28,
          borderRadius: 6,
          border: "1px solid var(--pr-border-control)",
          background: "var(--pr-bg-inset)",
          color: "var(--pr-text-sub)",
          cursor: "pointer",
          fontFamily: "inherit",
          fontSize: 13,
        }}
      >
        ⟩
      </button>
      <button
        type="button"
        aria-label="ページサムネイルを開く"
        title="ページサムネイル"
        onClick={() => onOpen("pages")}
        style={{
          width: 30,
          minHeight: 28,
          borderRadius: 6,
          border: "none",
          background: tab === "pages" ? "var(--pr-acc-s)" : "transparent",
          color: tab === "pages" ? "var(--pr-acc)" : "var(--pr-text-sub2)",
          boxShadow: tab === "pages" ? "inset 2px 0 var(--pr-acc)" : undefined,
          cursor: "pointer",
          fontFamily: "inherit",
          fontSize: 10.5,
          fontWeight: 700,
          padding: "4px 0",
          lineHeight: 1.1,
        }}
      >
        {currentPage}
      </button>
      <button
        type="button"
        aria-label="PDF目次を開く"
        title="PDF目次"
        onClick={() => onOpen("toc")}
        style={{
          width: 30,
          height: 28,
          borderRadius: 6,
          border: "none",
          background: tab === "toc" ? "var(--pr-acc-s)" : "transparent",
          color: tab === "toc" ? "var(--pr-acc)" : "var(--pr-text-sub2)",
          boxShadow: tab === "toc" ? "inset 2px 0 var(--pr-acc)" : undefined,
          cursor: "pointer",
          fontFamily: "inherit",
          fontSize: 11,
          fontWeight: 700,
        }}
      >
        目
      </button>
    </nav>
  );
}
