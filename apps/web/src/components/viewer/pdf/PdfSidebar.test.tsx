import { render, screen, fireEvent } from "@testing-library/react";
import { beforeEach, describe, expect, test, vi } from "vitest";
import type { TocNode } from "@yakudoku/api-client";
import { usePdfViewStore } from "@/stores/pdf-view-store";
import { PdfSidebar } from "./PdfSidebar";
import type { UsePdfDocumentResult } from "./use-pdf-document";

// jsdom は IntersectionObserver を実装しない。PdfThumbnail の遅延レンダリング用
// observer 生成が失敗しないよう最小限のスタブを与える(可視判定はしない=常にスケルトン)。
class FakeIntersectionObserver {
  observe(): void {}
  disconnect(): void {}
  unobserve(): void {}
}
vi.stubGlobal("IntersectionObserver", FakeIntersectionObserver);
// jsdom は scrollIntoView も未実装。
Element.prototype.scrollIntoView = vi.fn();

const pdfContext: UsePdfDocumentResult = {
  loading: false,
  error: false,
  notFound: false,
  numPages: 24,
  fileSizeMb: 4.1,
  getPage: () => Promise.reject(new Error("not used in this test")),
  retry: vi.fn(),
};

vi.mock("./use-pdf-document", () => ({
  usePdfDocumentContext: () => pdfContext,
}));

const toc: TocNode[] = [
  {
    section_id: "sec-1",
    number: "1",
    title_ja: "はじめに",
    title_en: "Introduction",
    translated: true,
    in_progress_denominator: true,
    on_demand: false,
    annotation_count: 0,
    bookmarked: false,
    children: [],
  },
  {
    section_id: "sec-app",
    number: "A",
    title_ja: null,
    title_en: "Appendix",
    translated: false,
    in_progress_denominator: false,
    on_demand: true,
    annotation_count: 0,
    bookmarked: false,
    children: [],
  },
];

function baseProps() {
  return {
    toc,
    activeSectionId: "sec-1",
    onSectionClick: vi.fn(),
    onTranslateAppendix: vi.fn(),
    pageCountFallback: null,
    pdfDownloadHref: "/api/papers/pap_1/pdf",
  };
}

// VT-VIEW-2a: PDF 左サイドバー — 目次/ページ切替、サムネイル選択、フッタ。
describe("PdfSidebar (2a §4.2.2)", () => {
  beforeEach(() => {
    usePdfViewStore.setState({ sidebarTab: "pages", page: 5 });
  });

  test("defaults to the 'pages' tab and shows numPages thumbnails with the current page selected", () => {
    render(<PdfSidebar {...baseProps()} />);
    expect(screen.getByLabelText("ページ 5")).toHaveAttribute("aria-current", "true");
    expect(screen.getByLabelText("ページ 1")).not.toHaveAttribute("aria-current");
    expect(screen.getAllByLabelText(/^ページ \d+$/)).toHaveLength(24);
  });

  test("clicking a thumbnail updates usePdfViewStore.page", () => {
    render(<PdfSidebar {...baseProps()} />);
    fireEvent.click(screen.getByLabelText("ページ 3"));
    expect(usePdfViewStore.getState().page).toBe(3);
  });

  test("footer shows page count and file size, with a PDF download link", () => {
    render(<PdfSidebar {...baseProps()} />);
    expect(screen.getByText("24 ページ · 4.1 MB")).toBeInTheDocument();
    const link = screen.getByText("⤓ 原文PDF");
    expect(link).toHaveAttribute("href", "/api/papers/pap_1/pdf");
    expect(link).toHaveAttribute("download");
  });

  test("switching to the 目次 tab renders the TOC rows and on-demand appendix box", () => {
    render(<PdfSidebar {...baseProps()} />);
    fireEvent.click(screen.getByText("目次"));
    // TocRow は "1 "+"はじめに" を別テキストノードで描画する(TocTree.tsx の既存挙動)。
    expect(screen.getByText((_, node) => node?.textContent === "1 はじめに")).toBeInTheDocument();
    expect(screen.getByText("開くと翻訳します(オンデマンド)")).toBeInTheDocument();
    expect(screen.queryByLabelText("ページ 5")).toBeNull();
  });

  test("clicking the on-demand appendix box triggers section click + translate-appendix", () => {
    const onSectionClick = vi.fn();
    const onTranslateAppendix = vi.fn();
    render(<PdfSidebar {...baseProps()} onSectionClick={onSectionClick} onTranslateAppendix={onTranslateAppendix} />);
    fireEvent.click(screen.getByText("目次"));
    fireEvent.click(screen.getByText("開くと翻訳します(オンデマンド)"));
    expect(onSectionClick).toHaveBeenCalledWith("sec-app");
    expect(onTranslateAppendix).toHaveBeenCalledWith("sec-app");
  });

  test("falls back to pageCountFallback while pdf.js numPages is unresolved", () => {
    pdfContext.numPages = null;
    render(<PdfSidebar {...baseProps()} pageCountFallback={12} />);
    expect(screen.getByText(/^12 ページ ·/)).toBeInTheDocument();
    pdfContext.numPages = 24;
  });

  test("collapsed rail can reopen the PDF sidebar", () => {
    const onToggle = vi.fn();
    render(<PdfSidebar {...baseProps()} open={false} onToggle={onToggle} />);
    expect(screen.getByLabelText("PDFサイドバーを開く")).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText("PDFサイドバーを開く"));
    expect(onToggle).toHaveBeenCalledWith(true);
  });

  test("collapse button closes the PDF sidebar", () => {
    const onToggle = vi.fn();
    render(<PdfSidebar {...baseProps()} onToggle={onToggle} />);
    fireEvent.click(screen.getByLabelText("PDFサイドバーを折りたたむ"));
    expect(onToggle).toHaveBeenCalledWith(false);
  });
});
