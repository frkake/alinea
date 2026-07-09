import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, fireEvent } from "@testing-library/react";
import { afterEach, beforeAll, beforeEach, describe, expect, test, vi } from "vitest";
import type { LibraryItemSummary, ViewerInit } from "@alinea/api-client";
import { ViewerShell } from "@/components/viewer/ViewerShell";
import { useViewerStore } from "@/stores/viewer-store";
import { mockMatchMedia } from "@/test-utils/mockMatchMedia";

vi.mock("@alinea/api-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@alinea/api-client")>();
  return {
    ...actual,
    libraryItemsUpdate: vi.fn(),
    translationsSectionTranslate: vi.fn(),
  };
});

const replace = vi.fn();
const currentSearch = new URLSearchParams();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace, back: vi.fn(), push: vi.fn() }),
  useSearchParams: () => currentSearch,
}));

// 周辺フック/タブ本体は別レーン所有のため軽量スタブへ(ViewerShell.finish-reading.test.tsx と同方針)。
vi.mock("@/hooks/use-pdf-availability", () => ({ usePdfAvailability: () => true }));
vi.mock("@/hooks/use-reading-position", () => ({ useReadingPosition: () => undefined }));
vi.mock("@/hooks/use-reading-session", () => ({ useReadingSession: () => undefined }));
vi.mock("@/hooks/use-viewer-keymap", () => ({ useViewerKeymap: () => undefined }));
vi.mock("@/lib/sse", () => ({ useSSE: () => ({ connected: false, fallbackActive: true, lastEventId: "" }) }));
vi.mock("@/components/chat/ChatPanel", () => ({ ChatPanel: () => <div>chat-panel</div> }));
vi.mock("@/components/viewer/FiguresPanel", () => ({ FiguresPanel: () => null }));
vi.mock("@/components/viewer/InfoPanel", () => ({ InfoPanel: () => <div>info-panel</div> }));
vi.mock("@/components/viewer/AnnotationListPanel", () => ({
  AnnotationListPanel: () => <div>annotations-panel</div>,
}));

class FakeResizeObserver {
  observe(): void {}
  disconnect(): void {}
  unobserve(): void {}
}
// mobile.md §6-3: 初回フレームのちらつきは許容(useIsMobile の初期値は false)。そのため
// マウント直後の一瞬 mode='pdf' 分岐(PdfSidebar/PdfPane)が描画されうる — 他の pdf テスト
// (PdfSidebar.test.tsx)と同じスタブを与える。
class FakeIntersectionObserver {
  observe(): void {}
  disconnect(): void {}
  unobserve(): void {}
}
vi.stubGlobal("ResizeObserver", FakeResizeObserver);
vi.stubGlobal("IntersectionObserver", FakeIntersectionObserver);
Element.prototype.scrollIntoView = vi.fn();
beforeAll(() => {
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(
    {} as unknown as CanvasRenderingContext2D,
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.stubGlobal("ResizeObserver", FakeResizeObserver);
  vi.stubGlobal("IntersectionObserver", FakeIntersectionObserver);
});

function makeLibraryItem(overrides: Partial<LibraryItemSummary> = {}): LibraryItemSummary {
  return {
    id: "li_1",
    paper: {
      id: "pap_1",
      title: "Rectified Flow",
      authors: ["Xingchao Liu"],
      authors_short: "Liu, Gong, Liu",
      venue: "ICLR 2023",
      year: 2023,
      arxiv_id: "2209.03003",
      license: "cc-by",
      visibility: "public",
      abstract: "",
    },
    status: "reading",
    priority: null,
    deadline: null,
    tags: [],
    suggested_tags: [],
    quality_level: "A",
    source: "arxiv",
    progress_pct: 0,
    comprehension: null,
    reading_seconds_total: 0,
    added_at: "2026-07-02T00:00:00Z",
    updated_at: "2026-07-02T00:00:00Z",
    ...overrides,
  };
}

function makeViewer(overrides: Partial<ViewerInit> = {}): ViewerInit {
  return {
    library_item: makeLibraryItem(),
    revision: {
      id: "rev_1",
      quality_level: "A",
      source_version: null,
      parser_version: "1.0",
      source_format: "latex",
      page_count: 8,
      figure_count: 0,
      table_count: 0,
      created_at: "2026-07-02T00:00:00Z",
    },
    newer_revision: null,
    toc: [
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
    ],
    translation: null,
    counts: { annotations: 0, resources: 0, figures: 0, notes: 0 },
    last_position: null,
    license_card: { license: "cc-by", figure_reuse: "allowed", message: "" },
    ingest_timeline: [],
    today_reading_minutes: 0,
    ...overrides,
  };
}

function renderMobile(mode: "translation" | "pdf" = "translation") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const viewer = makeViewer();
  client.setQueryData(["viewer", "li_1"], viewer);
  return render(
    <QueryClientProvider client={client}>
      <ViewerShell itemId="li_1" viewer={viewer} mode={mode} onModeChange={vi.fn()}>
        <div>本文</div>
      </ViewerShell>
    </QueryClientProvider>,
  );
}

// mobile.md §4/§4.5: モバイル縮退時は左目次ペイン/常設サイドパネルを非描画にし、
// 目次ドロワー・FAB+ボトムシートに差し替える。mode='pdf' でも訳文本文(children)を描画する。
describe("ViewerShell mobile reduction (mobile.md §4)", () => {
  beforeEach(() => {
    useViewerStore.setState({ panelOpen: false, tocOpen: false, itemId: null, revisionId: null });
    mockMatchMedia(true);
  });

  test("renders children (translation) even when URL mode is pdf, without a desktop toc pane or side panel", () => {
    renderMobile("pdf");
    expect(screen.getByText("本文")).toBeInTheDocument();
    // デスクトップの TocTree ペイン(見出し「目次」)は非描画。目次ドロワーのボタンに差し替わる。
    expect(screen.queryByRole("navigation", { name: "目次" })).toBeNull();
    expect(screen.getByLabelText("目次を開く")).toBeInTheDocument();
    // 常設サイドパネル(SidePanelTabs のタブ行)は非描画。FAB に差し替わる。
    expect(screen.queryByRole("tablist")).toBeNull();
    expect(screen.getByLabelText("論文パネルを開く")).toBeInTheDocument();
  });

  test("tapping the toc button opens the toc drawer with the section row", () => {
    renderMobile();
    fireEvent.click(screen.getByLabelText("目次を開く"));
    expect(screen.getByRole("dialog", { name: "目次" })).toBeInTheDocument();
    expect(screen.getByText(/はじめに/)).toBeInTheDocument();
  });

  test("tapping the FAB opens the bottom sheet with chat/annotations/info tabs (readOnly)", () => {
    renderMobile();
    fireEvent.click(screen.getByLabelText("論文パネルを開く"));
    expect(screen.getByRole("tab", { name: "チャット" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /注釈/ })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "情報" })).toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "図表" })).toBeNull();
    expect(screen.getByText("chat-panel")).toBeInTheDocument();
  });
});
