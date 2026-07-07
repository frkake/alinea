import { describe, expect, test } from "vitest";
import {
  bboxArea,
  bboxContainsPoint,
  bboxToViewportRect,
  clampZoom,
  computeFitScale,
  topDownPtToViewport,
  viewportToTopDownPt,
  type PdfViewportLike,
} from "./geometry";

/**
 * pdf.js `PageViewport`(rotation=0)の実装を忠実に再現したフェイク
 * (`node_modules/pdfjs-dist/build/pdf.mjs` の `PageViewport` コンストラクタと同一の変換行列)。
 * viewBox=[0,0,W,H] を前提に、convertToViewportPoint(x,y)=[s*x, s*(H-y)] となる
 * (下原点 pt → 上原点 css px への Y 反転)。
 */
function fakeViewport(width: number, height: number, scale: number): PdfViewportLike {
  return {
    viewBox: [0, 0, width, height],
    width: width * scale,
    height: height * scale,
    convertToViewportPoint(x: number, y: number): number[] {
      return [scale * x, scale * (height - y)];
    },
    convertToPdfPoint(cx: number, cy: number): number[] {
      return [cx / scale, height - cy / scale];
    },
  };
}

describe("topDownPtToViewport / viewportToTopDownPt", () => {
  test("round-trips an arbitrary point", () => {
    const vp = fakeViewport(612, 792, 1.5);
    const [cx, cy] = topDownPtToViewport(vp, 100, 50);
    const [x, y] = viewportToTopDownPt(vp, cx, cy);
    expect(x).toBeCloseTo(100, 5);
    expect(y).toBeCloseTo(50, 5);
  });

  test("top-down y=0 (page top, fitz convention) maps near canvas top (css y≈0)", () => {
    const vp = fakeViewport(612, 792, 1);
    const [, cy] = topDownPtToViewport(vp, 0, 0);
    expect(cy).toBeCloseTo(0, 5);
  });

  test("top-down y=pageHeight (page bottom) maps near canvas bottom (css y≈height)", () => {
    const vp = fakeViewport(612, 792, 1);
    const [, cy] = topDownPtToViewport(vp, 0, 792);
    expect(cy).toBeCloseTo(792, 5);
  });

  test("scale is applied to x", () => {
    const vp = fakeViewport(612, 792, 2);
    const [cx] = topDownPtToViewport(vp, 100, 0);
    expect(cx).toBeCloseTo(200, 5);
  });
});

describe("bboxToViewportRect", () => {
  test("orders left<right and top<bottom regardless of bbox corner order (Y-flip preserved)", () => {
    const vp = fakeViewport(612, 792, 1);
    // bbox: top-down [x0=50,y0=100 (near top), x1=150,y1=140 (further down)]
    const rect = bboxToViewportRect(vp, [50, 100, 150, 140]);
    expect(rect.left).toBeCloseTo(50, 5);
    expect(rect.right).toBeCloseTo(150, 5);
    // y0=100 is nearer the page top than y1=140, so it must remain the smaller (top) css y.
    expect(rect.top).toBeCloseTo(100, 5);
    expect(rect.bottom).toBeCloseTo(140, 5);
    expect(rect.top).toBeLessThan(rect.bottom);
    expect(rect.left).toBeLessThan(rect.right);
  });
});

describe("bboxContainsPoint / bboxArea", () => {
  test("contains point inside and rejects point outside", () => {
    const bbox = [10, 20, 110, 70] as const;
    expect(bboxContainsPoint(bbox, 50, 50)).toBe(true);
    expect(bboxContainsPoint(bbox, 5, 50)).toBe(false);
    expect(bboxContainsPoint(bbox, 50, 500)).toBe(false);
  });

  test("area is width*height regardless of corner ordering", () => {
    expect(bboxArea([0, 0, 10, 20])).toBe(200);
    expect(bboxArea([10, 20, 0, 0])).toBe(200);
  });
});

describe("clampZoom", () => {
  test("clamps to [0.25, 4.0] and rounds to 2 decimals", () => {
    expect(clampZoom(0.1)).toBe(0.25);
    expect(clampZoom(5)).toBe(4.0);
    expect(clampZoom(1.2345)).toBeCloseTo(1.23, 5);
  });
});

describe("computeFitScale", () => {
  test("actual mode is always 1.0", () => {
    expect(computeFitScale("actual", 1000, 800, 612, 792)).toBe(1);
  });

  test("fit-width uses (container-166)/pageWidth", () => {
    const scale = computeFitScale("fit-width", 866, 800, 700, 906);
    expect(scale).toBeCloseTo(1.0, 2); // (866-166)/700 = 1.0
  });

  test("fit-page takes the smaller of width/height fit", () => {
    const scale = computeFitScale("fit-page", 2000, 500, 700, 906);
    // width fit would be huge; height fit = (500-40)/906 ≈ 0.5077
    expect(scale).toBeCloseTo((500 - 40) / 906, 2);
  });
});
