import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { beforeAll, describe, expect, test, vi } from "vitest";
import { PdfCanvas, spreadPages, type PdfCanvasProps } from "./PdfCanvas";
import { buildPdfSyncMap } from "./sync-map";
import type { PdfPageLike, PdfRenderTask } from "./use-pdf-document";
import type { DocumentResponse } from "@/components/viewer/document-types";
import type { PdfViewportLike } from "./geometry";

// jsdom は 2D context を実装しない。本テストでは render() 自体をモックするため
// getContext の戻り値は使わない(null チェックを通過させるためのダミー)。
beforeAll(() => {
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(
    {} as unknown as CanvasRenderingContext2D,
  );
});

/** pdf.js PageViewport(rotation=0)を模したフェイク(scale=1, viewBox=[0,0,W,H])。 */
function fakeViewport(width: number, height: number, scale: number): PdfViewportLike {
  return {
    viewBox: [0, 0, width, height],
    width: width * scale,
    height: height * scale,
    convertToViewportPoint(x, y) {
      return [scale * x, scale * (height - y)];
    },
    convertToPdfPoint(cx, cy) {
      return [cx / scale, height - cy / scale];
    },
  };
}

function fakePage(width: number, height: number): PdfPageLike {
  return {
    getViewport({ scale }: { scale: number }) {
      return fakeViewport(width, height, scale);
    },
    render(): PdfRenderTask {
      return { promise: Promise.resolve(), cancel: vi.fn() };
    },
  };
}

function fakePageWithLink(width: number, height: number): PdfPageLike {
  return {
    ...fakePage(width, height),
    getAnnotations: () =>
      Promise.resolve([
        {
          subtype: "Link",
          url: "https://example.com/paper",
          rect: [90, 622, 130, 662],
        },
      ]),
  };
}

const doc: DocumentResponse = {
  revision_id: "rev-1",
  quality_level: "B",
  sections: [
    {
      id: "sec-2-2",
      heading: { number: "2.2", title: "Reflow: Straightening the Flow" },
      blocks: [{ id: "blk-2-2-p1", type: "paragraph", page: 5, bbox: [50, 100, 550, 300] }],
    },
  ],
};

function baseProps(overrides: Partial<PdfCanvasProps> = {}): PdfCanvasProps {
  return {
    displayPages: [5],
    scale: 1,
    getPage: () => Promise.resolve(fakePage(612, 792)),
    syncMap: buildPdfSyncMap(doc),
    selectedBlockId: null,
    onSelectBlock: vi.fn(),
    onOpenInTranslation: vi.fn(),
    loading: false,
    error: false,
    onRetry: vi.fn(),
    ...overrides,
  };
}

// VT-VIEW-2a: PDF キャンバス — bbox 選択→チップ、ローディング/エラー面。
describe("PdfCanvas (2a §4.2.4)", () => {
  test("shows the loading placeholder text while loading", () => {
    render(<PdfCanvas {...baseProps({ loading: true })} />);
    expect(screen.getByText("PDF を読み込んでいます…")).toBeInTheDocument();
  });

  test("shows an error EmptyState with a retry action", () => {
    const onRetry = vi.fn();
    render(<PdfCanvas {...baseProps({ error: true, onRetry })} />);
    expect(screen.getByText("PDF を読み込めませんでした")).toBeInTheDocument();
    fireEvent.click(screen.getByText("再試行"));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  test("Ctrl+wheel zooms instead of stepping pages", () => {
    const onWheelZoom = vi.fn();
    const onPageStep = vi.fn();
    const preventDefault = vi.spyOn(Event.prototype, "preventDefault");
    const { container } = render(
      <PdfCanvas
        {...baseProps({
          displayPages: [],
          onWheelZoom,
          onPageStep,
        })}
      />,
    );
    const root = container.querySelector<HTMLElement>(".yk-pdf-canvas-bg");
    if (!root) throw new Error("PDF canvas root missing");

    const event = new WheelEvent("wheel", {
      bubbles: true,
      cancelable: true,
      ctrlKey: true,
      deltaY: -120,
    });

    try {
      fireEvent(root, event);
      expect(preventDefault).toHaveBeenCalled();
      expect(onWheelZoom).toHaveBeenCalledWith(1);
      expect(onPageStep).not.toHaveBeenCalled();
    } finally {
      preventDefault.mockRestore();
    }
  });

  test("keeps the pointed PDF position under the cursor after Ctrl+wheel zoom", async () => {
    const onWheelZoom = vi.fn();
    const props = baseProps({ onWheelZoom });
    const { container, rerender } = render(<PdfCanvas {...props} />);
    const root = container.querySelector<HTMLElement>(".yk-pdf-canvas-bg");
    if (!root) throw new Error("PDF canvas root missing");
    const pageEl = await waitFor(() => {
      const el = container.querySelector<HTMLElement>('[data-pdf-page="5"]');
      if (!el) throw new Error("page layer not rendered yet");
      return el;
    });

    root.scrollLeft = 20;
    root.scrollTop = 30;
    let pageRect = {
      left: 40,
      top: 80,
      right: 240,
      bottom: 380,
      width: 200,
      height: 300,
      x: 40,
      y: 80,
      toJSON: () => ({}),
    };
    vi.spyOn(pageEl, "getBoundingClientRect").mockImplementation(() => pageRect);

    fireEvent.wheel(pageEl, { ctrlKey: true, deltaY: -120, clientX: 90, clientY: 155 });
    expect(onWheelZoom).toHaveBeenCalledWith(1);

    pageRect = {
      left: 30,
      top: 60,
      right: 430,
      bottom: 660,
      width: 400,
      height: 600,
      x: 30,
      y: 60,
      toJSON: () => ({}),
    };
    rerender(<PdfCanvas {...props} scale={2} />);

    await waitFor(() => {
      expect(root.scrollLeft).toBeCloseTo(60, 5);
      expect(root.scrollTop).toBeCloseTo(85, 5);
    });
  });

  test("clicking inside a synced bbox reports the hit via onSelectBlock", async () => {
    const onSelectBlock = vi.fn();
    const { container } = render(<PdfCanvas {...baseProps({ onSelectBlock })} />);
    const pageEl = await waitFor(() => {
      const el = container.querySelector('[data-pdf-page="5"]');
      if (!el) throw new Error("page layer not rendered yet");
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
    // scale=1 では css px と上原点 pt が一致する(下原点↔上原点の 2 回反転が相殺する。
    // geometry.test.ts で検証済み)。ブロック bbox [50,100,550,300] の内側の css 座標。
    fireEvent.click(pageEl, { clientX: 100, clientY: 150 });
    await waitFor(() => expect(onSelectBlock).toHaveBeenCalled());
    expect(onSelectBlock).toHaveBeenCalledWith(
      expect.objectContaining({ blockId: "blk-2-2-p1", display: "§2.2 Reflow ¶1" }),
    );
  });

  test("syncs the page wrapper, canvas css size, and pdf.js scale factor", async () => {
    const { container } = render(<PdfCanvas {...baseProps({ scale: 1.25 })} />);
    const pageEl = await waitFor(() => {
      const el = container.querySelector<HTMLElement>('[data-pdf-page="5"]');
      if (!el) throw new Error("page layer not rendered yet");
      return el;
    });
    await waitFor(() => expect(pageEl.style.width).toBe("765px"));
    const canvas = pageEl.querySelector("canvas");
    expect(pageEl.style.height).toBe("990px");
    expect(pageEl.style.getPropertyValue("--scale-factor")).toBe("1.25");
    expect(canvas?.style.width).toBe("765px");
    expect(canvas?.style.height).toBe("990px");
  });

  test("clicking outside any bbox reports null (deselect)", async () => {
    const onSelectBlock = vi.fn();
    const { container } = render(<PdfCanvas {...baseProps({ onSelectBlock })} />);
    const pageEl = await waitFor(() => {
      const el = container.querySelector('[data-pdf-page="5"]');
      if (!el) throw new Error("page layer not rendered yet");
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
    fireEvent.click(pageEl, { clientX: 5, clientY: 5 });
    await waitFor(() => expect(onSelectBlock).toHaveBeenCalledWith(null));
  });

  test("clicking a PDF link opens the href without using the transparent overlay for pointer events", async () => {
    const onSelectBlock = vi.fn();
    const open = vi.spyOn(window, "open").mockImplementation(() => null);
    const { container } = render(
      <PdfCanvas
        {...baseProps({
          getPage: () => Promise.resolve(fakePageWithLink(612, 792)),
          onSelectBlock,
        })}
      />,
    );
    const pageEl = await waitFor(() => {
      const el = container.querySelector('[data-pdf-page="5"]');
      if (!el) throw new Error("page layer not rendered yet");
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
    await waitFor(() =>
      expect(container.querySelector(".yk-pdf-link-layer a")).toBeInTheDocument(),
    );
    fireEvent.click(pageEl, { clientX: 100, clientY: 150 });
    expect(open).toHaveBeenCalledWith("https://example.com/paper", "_blank", "noopener,noreferrer");
    expect(onSelectBlock).not.toHaveBeenCalled();
    open.mockRestore();
  });

  test("dragging text selection does not trigger bbox selection on the trailing click", async () => {
    const onSelectBlock = vi.fn();
    const { container } = render(<PdfCanvas {...baseProps({ onSelectBlock })} />);
    const pageEl = await waitFor(() => {
      const el = container.querySelector('[data-pdf-page="5"]');
      if (!el) throw new Error("page layer not rendered yet");
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
    fireEvent.mouseDown(pageEl, { button: 0, clientX: 100, clientY: 150 });
    fireEvent.mouseMove(pageEl, { clientX: 145, clientY: 150 });
    fireEvent.click(pageEl, { clientX: 145, clientY: 150 });
    expect(onSelectBlock).not.toHaveBeenCalled();
  });

  test("does not auto-scroll again when the active page changed from local scrolling", async () => {
    const onVisiblePageChange = vi.fn();
    const scrollIntoView = vi.fn();
    const originalScrollIntoView = HTMLElement.prototype.scrollIntoView;
    const originalRaf = window.requestAnimationFrame;
    const originalCancelRaf = window.cancelAnimationFrame;
    const now = vi.spyOn(Date, "now").mockReturnValue(1000);
    HTMLElement.prototype.scrollIntoView = scrollIntoView;
    window.requestAnimationFrame = (cb: FrameRequestCallback) => {
      cb(0);
      return 1;
    };
    window.cancelAnimationFrame = vi.fn();

    try {
      const props = baseProps({
        displayPages: [1],
        pageGroups: [[1], [2]],
        activePage: 1,
        onVisiblePageChange,
      });
      const { container, rerender } = render(<PdfCanvas {...props} />);
      const page1 = await waitFor(() => {
        const el = container.querySelector<HTMLElement>('[data-pdf-page="1"]');
        if (!el) throw new Error("page 1 missing");
        return el;
      });
      const page2 = await waitFor(() => {
        const el = container.querySelector<HTMLElement>('[data-pdf-page="2"]');
        if (!el) throw new Error("page 2 missing");
        return el;
      });
      await waitFor(() => expect(scrollIntoView).toHaveBeenCalled());
      scrollIntoView.mockClear();
      now.mockReturnValue(2000);

      const root = container.querySelector<HTMLElement>(".yk-pdf-canvas-bg");
      if (!root) throw new Error("root missing");
      vi.spyOn(root, "getBoundingClientRect").mockReturnValue({
        left: 0,
        top: 0,
        right: 700,
        bottom: 800,
        width: 700,
        height: 800,
        x: 0,
        y: 0,
        toJSON: () => ({}),
      });
      vi.spyOn(page1, "getBoundingClientRect").mockReturnValue({
        left: 0,
        top: -700,
        right: 612,
        bottom: 92,
        width: 612,
        height: 792,
        x: 0,
        y: -700,
        toJSON: () => ({}),
      });
      vi.spyOn(page2, "getBoundingClientRect").mockReturnValue({
        left: 0,
        top: 110,
        right: 612,
        bottom: 902,
        width: 612,
        height: 792,
        x: 0,
        y: 110,
        toJSON: () => ({}),
      });

      fireEvent.scroll(root);
      await waitFor(() => expect(onVisiblePageChange).toHaveBeenCalledWith(2));
      rerender(<PdfCanvas {...props} activePage={2} />);
      await Promise.resolve();
      expect(scrollIntoView).not.toHaveBeenCalled();
    } finally {
      now.mockRestore();
      if (originalScrollIntoView) {
        HTMLElement.prototype.scrollIntoView = originalScrollIntoView;
      } else {
        delete (HTMLElement.prototype as Partial<HTMLElement>).scrollIntoView;
      }
      window.requestAnimationFrame = originalRaf;
      window.cancelAnimationFrame = originalCancelRaf;
    }
  });

  test("selecting a block shows the highlight + sync chip, and clicking the chip opens translation", async () => {
    const onOpenInTranslation = vi.fn();
    render(<PdfCanvas {...baseProps({ selectedBlockId: "blk-2-2-p1", onOpenInTranslation })} />);
    const chip = await screen.findByText("≒ §2.2 Reflow ¶1 — 訳文で見る →");
    expect(screen.getByTestId("pdf-bbox-highlight")).toBeInTheDocument();
    fireEvent.click(chip);
    expect(onOpenInTranslation).toHaveBeenCalledWith("blk-2-2-p1");
  });
});

describe("spreadPages (2a §4.2.4 見開き決定)", () => {
  test("non-spread mode always shows a single page", () => {
    expect(spreadPages(5, 24, false)).toEqual([5]);
  });

  test("page 1 is single, positioned right (empty left slot)", () => {
    expect(spreadPages(1, 24, true)).toEqual([null, 1]);
  });

  test("even/odd pairing normalizes to the left (even) page", () => {
    expect(spreadPages(5, 24, true)).toEqual([4, 5]);
    expect(spreadPages(4, 24, true)).toEqual([4, 5]);
  });

  test("a trailing single page (even total) sits alone on the left", () => {
    expect(spreadPages(24, 24, true)).toEqual([24, null]);
  });

  test("can place page 1 on the left for odd/even spread pairing", () => {
    expect(spreadPages(1, 24, true, "left")).toEqual([1, 2]);
    expect(spreadPages(2, 24, true, "left")).toEqual([1, 2]);
    expect(spreadPages(3, 24, true, "left")).toEqual([3, 4]);
  });
});
