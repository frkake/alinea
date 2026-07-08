import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { beforeAll, beforeEach, describe, expect, test, vi } from "vitest";
import { viewerGetDocument } from "@yakudoku/api-client";
import { useViewerStore } from "@/stores/viewer-store";
import { usePdfViewStore } from "@/stores/pdf-view-store";
import type { DocumentResponse } from "@/components/viewer/document-types";
import { PdfPane } from "./PdfPane";
import type { UsePdfDocumentResult } from "./use-pdf-document";

// jsdom は ResizeObserver も 2D canvas context も実装しない。
class FakeResizeObserver {
  observe(): void {}
  disconnect(): void {}
  unobserve(): void {}
}
vi.stubGlobal("ResizeObserver", FakeResizeObserver);
beforeAll(() => {
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(
    {} as unknown as CanvasRenderingContext2D,
  );
});

vi.mock("@yakudoku/api-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@yakudoku/api-client")>();
  return { ...actual, viewerGetDocument: vi.fn() };
});

const replace = vi.fn();
let currentSearch = new URLSearchParams();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace, back: vi.fn(), push: vi.fn() }),
  useSearchParams: () => currentSearch,
}));

const pdfContext: UsePdfDocumentResult = {
  loading: false,
  error: false,
  notFound: false,
  numPages: 24,
  fileSizeMb: 4.1,
  getPage: () =>
    Promise.resolve({
      getViewport: ({ scale }: { scale: number }) => ({
        viewBox: [0, 0, 612, 792],
        width: 612 * scale,
        height: 792 * scale,
        convertToViewportPoint: (x: number, y: number) => [scale * x, scale * (792 - y)],
        convertToPdfPoint: (cx: number, cy: number) => [cx / scale, 792 - cy / scale],
      }),
      render: () => ({ promise: Promise.resolve(), cancel: vi.fn() }),
    }),
  retry: vi.fn(),
};

vi.mock("./use-pdf-document", () => ({
  usePdfDocumentContext: () => pdfContext,
  usePdfDocument: () => pdfContext,
}));

const doc: DocumentResponse = {
  revision_id: "rev-1",
  quality_level: "B",
  sections: [
    {
      id: "sec-2-2",
      heading: { number: "2.2", title: "Reflow: Straightening the Flow" },
      blocks: [
        {
          id: "blk-2-2-h",
          type: "heading",
          number: "2.2",
          title: "Reflow",
          page: 5,
          bbox: [50, 60, 300, 90],
        },
        { id: "blk-2-2-p1", type: "paragraph", page: 5, bbox: [50, 100, 550, 300] },
      ],
    },
  ],
};

function renderPane(overrides: Partial<Parameters<typeof PdfPane>[0]> = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const onOpenInTranslation = vi.fn();
  const utils = render(
    <QueryClientProvider client={client}>
      <PdfPane
        itemId="li_1"
        paperId="paper-1"
        revisionId="rev-1"
        initialPage={5}
        onOpenInTranslation={onOpenInTranslation}
        {...overrides}
      />
    </QueryClientProvider>,
  );
  return { ...utils, onOpenInTranslation };
}

// VT-VIEW-2a: PDF ペイン統合 — ツールバー同期・ページ移動→URL replace・bbox→訳文遷移。
describe("PdfPane (2a §5)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ status: 200, ok: true, body: { cancel: vi.fn() } }),
    );
    currentSearch = new URLSearchParams({ mode: "pdf", page: "5" });
    usePdfViewStore.setState({
      // itemId を render 時の itemId と一致させておく(resetForItem は itemId 変化時のみ
      // fitMode を 'fit-width' に戻すため。jsdom は clientWidth/Height が常に 0 になり
      // fit 計算が破綻するので、依存しない 'actual'(scale=1.0 固定)で決定的にする)。
      itemId: "li_1",
      page: 5,
      zoom: 1,
      fitMode: "actual",
      spread: false,
      spreadFirstPageSide: "right",
      documentMode: "source",
      selectedBlockId: null,
      sidebarTab: "pages",
    });
    useViewerStore.setState({ pendingScrollTarget: null });
    vi.mocked(viewerGetDocument).mockResolvedValue({ data: doc } as never);
  });

  test("renders the sync indicator once the document resolves", async () => {
    renderPane();
    expect(await screen.findByText("§2.2 Reflow")).toBeInTheDocument();
  });

  test("renders all PDF page slots in the main viewer instead of only the current neighborhood", async () => {
    const { container } = renderPane();
    await screen.findByText("§2.2 Reflow");
    expect(container.querySelectorAll("[data-pdf-page]")).toHaveLength(24);
  });

  test("bilingual mode renders source and translated PDF pages side by side without tracking duplicate page slots", async () => {
    usePdfViewStore.setState({ documentMode: "bilingual" });
    const { container } = renderPane();
    await screen.findByText("§2.2 Reflow");
    await waitFor(() => {
      const trackedPages = container.querySelectorAll("[data-pdf-page]");
      const pageLayers = container.querySelectorAll(".yk-pdf-page-layer");
      expect(trackedPages).toHaveLength(24);
      expect(pageLayers).toHaveLength(48);
    });
  });

  test("page navigation via the toolbar updates the store and replaces the URL with the new page", async () => {
    renderPane();
    await screen.findByText("§2.2 Reflow");
    fireEvent.click(screen.getByLabelText("次のページ"));
    await waitFor(() => expect(usePdfViewStore.getState().page).toBe(6));
    await waitFor(() =>
      expect(replace).toHaveBeenCalledWith(
        expect.stringContaining("page=6"),
        expect.objectContaining({ scroll: false }),
      ),
    );
  });

  test("Ctrl+wheel updates PDF zoom state and leaves fit mode", async () => {
    const { container } = renderPane();
    await screen.findByText("§2.2 Reflow");
    const root = container.querySelector<HTMLElement>(".yk-pdf-canvas-bg");
    if (!root) throw new Error("PDF canvas root missing");

    fireEvent.wheel(root, { ctrlKey: true, deltaY: -120 });

    await waitFor(() => {
      expect(usePdfViewStore.getState().zoom).toBe(1.1);
      expect(usePdfViewStore.getState().fitMode).toBeNull();
    });
    expect(screen.getByText("110%")).toBeInTheDocument();
  });

  test("spread first-page side control updates the PDF view store", async () => {
    const { container } = renderPane();
    await screen.findByText("§2.2 Reflow");
    fireEvent.click(screen.getByText("見開き"));
    fireEvent.click(await screen.findByText("1P 左"));
    expect(usePdfViewStore.getState().spread).toBe(true);
    expect(usePdfViewStore.getState().spreadFirstPageSide).toBe("left");
    await waitFor(() => {
      const page24Canvas = container.querySelector<HTMLCanvasElement>(
        '[data-pdf-page="24"] canvas',
      );
      expect(page24Canvas?.width).toBeGreaterThan(0);
    });
  });

  test("clicking a synced bbox then the chip calls onOpenInTranslation with the block id", async () => {
    const { container, onOpenInTranslation } = renderPane();
    await screen.findByText("§2.2 Reflow");
    const pageEl = await waitFor(() => {
      const el = container.querySelector('[data-pdf-page="5"]');
      // canvas の width 属性が付くまで(=viewport state 反映済みまで)待つ。
      const canvas = el?.querySelector("canvas");
      if (!el || !canvas || canvas.width === 0) throw new Error("not rendered yet");
      return el;
    });
    vi.spyOn(pageEl, "getBoundingClientRect").mockReturnValue({
      left: 0,
      top: 0,
      right: 612,
      bottom: 792,
      width: 612,
      height: 792,
      x: 0,
      y: 0,
      toJSON: () => ({}),
    });
    fireEvent.click(pageEl, { clientX: 100, clientY: 150 });
    const chip = await screen.findByText("≒ §2.2 Reflow ¶1 — 訳文で見る →");
    fireEvent.click(chip);
    expect(onOpenInTranslation).toHaveBeenCalledWith("blk-2-2-p1");
  });

  test("'この位置を訳文で開く →' targets the first block on the page when nothing is selected", async () => {
    const { onOpenInTranslation } = renderPane();
    await screen.findByText("§2.2 Reflow");
    fireEvent.click(screen.getByText("この位置を訳文で開く →"));
    expect(onOpenInTranslation).toHaveBeenCalledWith("blk-2-2-h");
  });
});
