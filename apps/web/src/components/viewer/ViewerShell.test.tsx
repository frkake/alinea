import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, fireEvent } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, test, vi } from "vitest";
import type { TocNode } from "@yakudoku/api-client";
import { ViewerHeader } from "@/components/viewer/ViewerHeader";
import { SidePanel } from "@/components/viewer/SidePanel";
import { TocTree } from "@/components/viewer/TocTree";
import { useViewerStore } from "@/stores/viewer-store";

function resetStore() {
  useViewerStore.setState({
    panelOpen: true,
    activeTab: "chat",
    style: "natural",
    tocOpen: true,
    itemId: "li_test",
    revisionId: "rev_test",
  });
}

function renderWithClient(ui: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

// VT-VIEW-01: シェル — 表示モード切替(M1 は 4 モード: 訳文/対訳/原文/PDF)・サイドパネル 3 タブ
describe("ViewerHeader modes (VT-VIEW-01)", () => {
  beforeEach(resetStore);

  const baseProps = {
    title: "Flow Straight and Fast",
    qualityLevel: "A" as const,
    status: "reading" as const,
    mode: "translation" as const,
    onModeChange: vi.fn(),
    onStatusChange: vi.fn(),
    onBack: vi.fn(),
  };

  test("M1 shows 訳文/対訳/原文/PDF, hides 記事(M2-07 まで未実装)", () => {
    renderWithClient(<ViewerHeader {...baseProps} />);
    expect(screen.getByText("訳文")).toBeInTheDocument();
    expect(screen.getByText("対訳")).toBeInTheDocument();
    expect(screen.getByText("原文")).toBeInTheDocument();
    expect(screen.getByText("PDF")).toBeInTheDocument();
    expect(screen.queryByText("記事")).toBeNull();
  });

  test("clicking a mode segment calls onModeChange", () => {
    const onModeChange = vi.fn();
    renderWithClient(<ViewerHeader {...baseProps} onModeChange={onModeChange} />);
    fireEvent.click(screen.getByText("対訳"));
    expect(onModeChange).toHaveBeenCalledWith("parallel");
  });

  test("clicking PDF calls onModeChange('pdf')", () => {
    const onModeChange = vi.fn();
    renderWithClient(<ViewerHeader {...baseProps} onModeChange={onModeChange} />);
    fireEvent.click(screen.getByText("PDF"));
    expect(onModeChange).toHaveBeenCalledWith("pdf");
  });

  test("pdfDisabled=true disables the PDF segment with a tooltip and ignores clicks", () => {
    const onModeChange = vi.fn();
    renderWithClient(<ViewerHeader {...baseProps} onModeChange={onModeChange} pdfDisabled />);
    const pdfSegment = screen.getByText("PDF");
    expect(pdfSegment).toHaveAttribute("title", "この論文には PDF がありません");
    fireEvent.click(pdfSegment);
    expect(onModeChange).not.toHaveBeenCalled();
  });

  // M1-13/配線: ヘッダの検索ボックスは InPaperSearch(実機能)そのもの。
  test("mounts the real InPaperSearch box (この論文内を検索 placeholder)", () => {
    renderWithClient(<ViewerHeader {...baseProps} />);
    expect(screen.getByPlaceholderText("この論文内を検索")).toBeInTheDocument();
  });
});

// mobile.md §4.2: モバイル縮退ヘッダは 戻る/目次/タイトル/ステータスピル/訳文バッジ の 5 要素のみ。
describe("ViewerHeader mobile reduction (mobile.md §4.2)", () => {
  beforeEach(resetStore);

  const baseProps = {
    title: "Flow Straight and Fast",
    qualityLevel: "A" as const,
    status: "reading" as const,
    mode: "translation" as const,
    onModeChange: vi.fn(),
    onStatusChange: vi.fn(),
    onBack: vi.fn(),
  };

  test("hides mode tabs, in-paper search, and overflow menu; shows the 訳文 badge and a toc button", () => {
    const onOpenToc = vi.fn();
    renderWithClient(<ViewerHeader {...baseProps} isMobile onOpenToc={onOpenToc} />);

    expect(screen.getByText("訳文")).toBeInTheDocument();
    expect(screen.queryByText("対訳")).toBeNull();
    expect(screen.queryByText("原文")).toBeNull();
    expect(screen.queryByText("PDF")).toBeNull();
    expect(screen.queryByPlaceholderText("この論文内を検索")).toBeNull();
    expect(screen.queryByLabelText("その他")).toBeNull();

    fireEvent.click(screen.getByLabelText("目次を開く"));
    expect(onOpenToc).toHaveBeenCalledTimes(1);
  });

  test("still exposes the interactive status pill", () => {
    renderWithClient(<ViewerHeader {...baseProps} isMobile />);
    const pill = screen.getByRole("button", { name: /読んでいる/ });
    expect(pill).toHaveAttribute("aria-haspopup", "menu");
  });
});

describe("SidePanel tabs (VT-VIEW-01)", () => {
  beforeEach(resetStore);

  test("M0 shows only 3 tabs (chat/figures/info), hides notes/annotations/resources", () => {
    render(<SidePanel milestone="M0" />);
    expect(screen.getByRole("tab", { name: "チャット" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "図表" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "情報" })).toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "メモ" })).toBeNull();
    expect(screen.queryByRole("tab", { name: "注釈" })).toBeNull();
    expect(screen.queryByRole("tab", { name: "リソース" })).toBeNull();
  });
});

// VT-VIEW-04: 目次 — 翻訳進捗%・節✓・未翻訳付録(オンデマンド)・折畳レール
describe("TocTree (VT-VIEW-04)", () => {
  beforeEach(resetStore);

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

  const baseProps = {
    toc,
    progressPct: 96,
    todayReadingMinutes: 42,
    open: true,
    onToggle: vi.fn(),
    activeSectionId: "sec-1",
    onSectionClick: vi.fn(),
    onTranslateAppendix: vi.fn(),
    onFocusSearch: vi.fn(),
  };

  test("pane shows translation progress %, translated ✓, and on-demand appendix box", () => {
    render(<TocTree {...baseProps} />);
    expect(screen.getByText("翻訳 96%")).toBeInTheDocument();
    expect(screen.getByText("✓")).toBeInTheDocument();
    expect(screen.getByText("開くと翻訳します(オンデマンド)")).toBeInTheDocument();
    expect(screen.getByText("今日の読書 42分")).toBeInTheDocument();
  });

  test("clicking on-demand appendix triggers on-demand translation", () => {
    const onTranslateAppendix = vi.fn();
    render(<TocTree {...baseProps} onTranslateAppendix={onTranslateAppendix} />);
    fireEvent.click(screen.getByText("開くと翻訳します(オンデマンド)"));
    expect(onTranslateAppendix).toHaveBeenCalledWith("sec-app");
  });

  test("collapsed rail exposes an open-toc control", () => {
    render(<TocTree {...baseProps} open={false} />);
    expect(screen.getByLabelText("目次を開く")).toBeInTheDocument();
    expect(screen.queryByText("翻訳 96%")).toBeNull();
  });
});
