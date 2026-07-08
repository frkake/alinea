import { render, screen, waitFor } from "@testing-library/react";
import { beforeAll, describe, expect, test, vi } from "vitest";
import { createRef } from "react";
import { PdfThumbnail } from "./PdfThumbnail";
import type { PdfPageLike, PdfRenderTask } from "./use-pdf-document";
import type { PdfViewportLike } from "./geometry";

beforeAll(() => {
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(
    {} as unknown as CanvasRenderingContext2D,
  );

  class ImmediateIntersectionObserver {
    private readonly callback: IntersectionObserverCallback;

    constructor(callback: IntersectionObserverCallback) {
      this.callback = callback;
    }

    observe(target: Element): void {
      this.callback([{ isIntersecting: true, target } as IntersectionObserverEntry], this as unknown as IntersectionObserver);
    }

    disconnect(): void {}
    unobserve(): void {}
    takeRecords(): IntersectionObserverEntry[] {
      return [];
    }
  }

  vi.stubGlobal("IntersectionObserver", ImmediateIntersectionObserver);
});

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

describe("PdfThumbnail", () => {
  test("keeps a canvas mounted so the visible thumbnail can render", async () => {
    const getPage = vi.fn(() => Promise.resolve(fakePage(612, 792)));
    render(
      <PdfThumbnail
        pageNumber={3}
        selected={false}
        onClick={vi.fn()}
        getPage={getPage}
        scrollRootRef={createRef<HTMLDivElement>()}
      />,
    );

    const button = screen.getByLabelText("ページ 3");
    const canvas = button.querySelector("canvas");
    expect(canvas).toBeInTheDocument();
    await waitFor(() => expect(canvas?.style.display).toBe("block"));
    expect(getPage).toHaveBeenCalledWith(3);
  });
});
