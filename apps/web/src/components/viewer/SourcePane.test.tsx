import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, fireEvent, act, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, test, vi } from "vitest";
import { annotationsList, viewerGetDocument } from "@alinea/api-client";
import { SourcePane } from "@/components/viewer/SourcePane";
import { useViewerStore } from "@/stores/viewer-store";

vi.mock("@alinea/api-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@alinea/api-client")>();
  return {
    ...actual,
    viewerGetDocument: vi.fn(),
    annotationsList: vi.fn(),
  };
});

// jsdom は IntersectionObserver / scrollIntoView を実装しない。
class FakeIntersectionObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}
vi.stubGlobal("IntersectionObserver", FakeIntersectionObserver);
Element.prototype.scrollIntoView = vi.fn();

function renderWithClient(ui: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

function emptyAnnotations() {
  return {
    items: [],
    counts: { all: 0, important: 0, question: 0, idea: 0, term: 0, with_comment: 0, unplaced: 0 },
  };
}

// M1 統合ポリッシュ: SourcePane にも hl パリティ(注釈ハイライト・?hl= 一時マーク)を適用する
// (TranslationPane と同じ部品 text-offset/HighlightMark を InlineRenderer 経由で再利用)。
describe("SourcePane hl parity (M1 統合ポリッシュ)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useViewerStore.setState({
      pendingScrollTarget: null,
      pendingHighlightQuery: null,
      panelOpen: true,
      activeTab: "chat",
      pendingAnnotationId: null,
    });
    vi.mocked(viewerGetDocument).mockResolvedValue({
      data: {
        revision_id: "rev_1",
        quality_level: "A",
        sections: [
          {
            id: "sec-1",
            heading: { number: "1", title: "Introduction" },
            blocks: [
              {
                id: "blk-1",
                type: "paragraph",
                inlines: [{ t: "text", v: "The rectified flow is an ODE." }],
              },
            ],
          },
        ],
      },
    } as never);
    vi.mocked(annotationsList).mockResolvedValue({ data: emptyAnnotations() } as never);
  });

  test("renders a HighlightMark for a placed source-side annotation and jumps the panel on chip click", async () => {
    vi.mocked(annotationsList).mockResolvedValue({
      data: {
        items: [
          {
            id: "ann_1",
            kind: "highlight",
            color: "important",
            anchor: {
              revision_id: "rev_1",
              block_id: "blk-1",
              start: 4,
              end: 18,
              quote: "rectified flow",
              side: "source",
              display: "§1",
            },
            comment: null,
            placed: true,
            created_at: "2026-07-06T21:12:00",
            updated_at: "2026-07-06T21:12:00",
          },
        ],
        counts: { all: 1, important: 1, question: 0, idea: 0, term: 0, with_comment: 0, unplaced: 0 },
      },
    } as never);

    renderWithClient(<SourcePane itemId="li_1" revisionId="rev_1" toc={[]} lastPosition={null} />);

    const mark = await screen.findByText("rectified flow");
    expect(mark.tagName).toBe("MARK");
    const chip = screen.getByRole("button", { name: "注釈 1 を表示" });
    fireEvent.click(chip);

    expect(useViewerStore.getState().activeTab).toBe("annotations");
    expect(useViewerStore.getState().pendingAnnotationId).toBe("ann_1");
  });

  test("does not place a translation-side annotation inline (原文モードは source 側のみ)", async () => {
    vi.mocked(annotationsList).mockResolvedValue({
      data: {
        items: [
          {
            id: "ann_2",
            kind: "highlight",
            color: "term",
            anchor: {
              revision_id: "rev_1",
              block_id: "blk-1",
              start: 0,
              end: 3,
              quote: "The",
              side: "translation",
              display: "§1",
            },
            comment: null,
            placed: true,
            created_at: "2026-07-06T21:12:00",
            updated_at: "2026-07-06T21:12:00",
          },
        ],
        counts: { all: 1, important: 0, question: 0, idea: 0, term: 1, with_comment: 0, unplaced: 0 },
      },
    } as never);

    const { container } = renderWithClient(
      <SourcePane itemId="li_1" revisionId="rev_1" toc={[]} lastPosition={null} />,
    );
    await screen.findByText(/ODE/);
    expect(container.querySelector("mark")).toBeNull();
  });

  test("wraps the ?hl= query match in a alinea-search-hit mark for the jump-target block", async () => {
    useViewerStore.setState({ pendingHighlightQuery: "rectified" });
    renderWithClient(<SourcePane itemId="li_1" revisionId="rev_1" toc={[]} lastPosition={null} />);
    await screen.findByText(/ODE/);

    act(() => {
      useViewerStore.getState().requestScroll({ kind: "block", blockId: "blk-1" });
    });

    await waitFor(() => {
      const mark = document.querySelector("mark.alinea-search-hit");
      expect(mark).not.toBeNull();
      expect(mark).toHaveTextContent("rectified");
    });
  });
});
