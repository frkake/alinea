import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, fireEvent, act, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, test, vi } from "vitest";
import { annotationsList, annotationsCreate, viewerGetDocument } from "@alinea/api-client";
import { SourcePane } from "@/components/viewer/SourcePane";
import { useViewerStore } from "@/stores/viewer-store";

vi.mock("@alinea/api-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@alinea/api-client")>();
  return {
    ...actual,
    viewerGetDocument: vi.fn(),
    annotationsList: vi.fn(),
    annotationsCreate: vi.fn(),
  };
});

// useAnnotationSelection → useRouter (next/navigation App Router コンテキストはユニットテスト対象外)。
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn() }),
}));

// jsdom does not implement Range.getBoundingClientRect; stub it so resolveSelectionAnchor works.
Range.prototype.getBoundingClientRect = () =>
  ({ top: 100, left: 50, bottom: 120, right: 200, width: 150, height: 20 }) as DOMRect;

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

  test("renders every structured source block instead of dropping non-paragraph content", async () => {
    vi.mocked(viewerGetDocument).mockResolvedValue({
      data: {
        revision_id: "rev_1",
        quality_level: "A",
        sections: [
          {
            id: "sec-1",
            heading: { number: "1", title: "Body" },
            blocks: [
              {
                id: "blk-list",
                type: "list",
                ordered: false,
                items: [[{ t: "text", v: "Visible list item" }]],
              },
              {
                id: "blk-quote",
                type: "quote",
                inlines: [{ t: "text", v: "Visible quotation" }],
              },
              {
                id: "blk-theorem",
                type: "theorem",
                title: "Theorem 1",
                inlines: [{ t: "text", v: "Visible theorem body" }],
              },
              {
                id: "blk-algorithm",
                type: "algorithm",
                caption: [{ t: "text", v: "Visible algorithm" }],
                inlines: [{ t: "text", v: "Visible algorithm body" }],
              },
              {
                id: "blk-footnote",
                type: "footnote",
                label: "footnote3",
                inlines: [{ t: "text", v: "Visible footnote" }],
              },
              {
                id: "blk-reference",
                type: "reference_entry",
                raw: String.raw`Author. \emph{Visible reference}. \url{https://example.com}.`,
              },
            ],
          },
        ],
      },
    } as never);

    renderWithClient(<SourcePane itemId="li_1" revisionId="rev_1" toc={[]} lastPosition={null} />);

    expect(await screen.findByText("Visible list item")).toBeInTheDocument();
    expect(screen.getByText("Visible quotation")).toBeInTheDocument();
    expect(screen.getByText(/Theorem 1/)).toBeInTheDocument();
    expect(screen.getByText(/Visible algorithm body/)).toBeInTheDocument();
    expect(screen.getByText("Visible footnote")).toBeInTheDocument();
    expect(screen.getByText(/Visible reference/)).toHaveTextContent(
      "Author. Visible reference. https://example.com.",
    );
  });
});

describe("SourcePane annotation creation", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(annotationsList).mockResolvedValue({
      data: { items: [], counts: { all: 0, important: 0, question: 0, idea: 0, term: 0, with_comment: 0, unplaced: 0 } },
    } as never);
    vi.mocked(viewerGetDocument).mockResolvedValue({
      data: {
        revision_id: "revision-1",
        quality_level: "A",
        sections: [
          {
            id: "section-1",
            heading: { number: "1", title: "Intro" },
            blocks: [{ id: "blk-s1", type: "paragraph", inlines: [{ t: "text", v: "A source sentence." }] }],
          },
        ],
      },
    } as never);
    vi.mocked(annotationsCreate).mockResolvedValue({ data: {} } as never);
  });

  test("highlighting creates a source-side annotation and 語彙に追加 is enabled", async () => {
    renderWithClient(
      <SourcePane itemId="item-1" revisionId="revision-1" toc={[]} lastPosition={null} />,
    );
    const text = await screen.findByText("A source sentence.");
    const range = document.createRange();
    range.selectNodeContents(text);
    const sel = window.getSelection()!;
    sel.removeAllRanges();
    sel.addRange(range);
    fireEvent.pointerUp(text);
    // 語彙に追加 is enabled for source selections.
    expect(await screen.findByRole("menuitem", { name: "語彙に追加" })).not.toBeDisabled();
    fireEvent.click(screen.getByLabelText("重要でハイライト"));
    await waitFor(() => expect(annotationsCreate).toHaveBeenCalled());
    // eslint-disable-next-line @typescript-eslint/no-non-null-assertion
    expect(vi.mocked(annotationsCreate).mock.calls[0]![0]).toMatchObject({
      body: { kind: "highlight", anchor: { side: "source", block_id: "blk-s1" } },
    });
  });
});
