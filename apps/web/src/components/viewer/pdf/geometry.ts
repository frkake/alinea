/**
 * PDF 座標変換の純関数群(2a §4.2.4・§5.4・§5.5)。
 *
 * 注意(座標系の不一致。本モジュールで吸収する): `document.blocks[].bbox` は PyMuPDF
 * (`packages/py-core/.../parsing/pdf_parser.py`)が生成する既定座標系で、原点はページ
 * 左上・y は下方向に増加する(いわゆる「上原点」)。一方 pdf.js の `PageViewport` が
 * 期待する pt 座標(`convertToViewportPoint` / `convertToPdfPoint` の引数・戻り値)は
 * PDF 本来の座標系で、原点はページ左下・y は上方向に増加する(「下原点」)。
 * 本ファイルの `topDownPtToViewport` / `viewportToTopDownPt` がこの 2 系を変換する。
 * ページ回転(`page.rotate !== 0`)には対応しない(既知の簡略化。arXiv 論文で概ね rotate=0)。
 */

export type Bbox = readonly [number, number, number, number]; // [x0,y0,x1,y1] 上原点 pt

/** pdf.js `PageViewport` が持つ最小インターフェース(テスト用に差し替え可能にする)。 */
export interface PdfViewportLike {
  viewBox: readonly number[]; // [x0,y0,x1,y1] 下原点 pt(page.view 相当)
  width: number; // css px(scale 適用後)
  height: number;
  convertToViewportPoint(x: number, y: number): number[];
  convertToPdfPoint(x: number, y: number): number[];
}

/** viewBox[3](ページ上端。下原点 pt)。不正な viewBox は 0 として扱う。 */
function viewBoxTop(viewport: PdfViewportLike): number {
  return viewport.viewBox[3] ?? 0;
}

/** 上原点 pt(bbox 由来) → 下原点 pt(pdf.js 期待値)。 */
export function topDownToNativeY(viewport: PdfViewportLike, yTopDown: number): number {
  return viewBoxTop(viewport) - yTopDown;
}

/** 下原点 pt(pdf.js 戻り値) → 上原点 pt(bbox と同じ座標系)。 */
export function nativeToTopDownY(viewport: PdfViewportLike, yNative: number): number {
  return viewBoxTop(viewport) - yNative;
}

/** 上原点 pt の点 → viewport css px。 */
export function topDownPtToViewport(
  viewport: PdfViewportLike,
  x: number,
  yTopDown: number,
): [number, number] {
  const [cx, cy] = viewport.convertToViewportPoint(x, topDownToNativeY(viewport, yTopDown));
  return [cx ?? 0, cy ?? 0];
}

/** viewport css px の点 → 上原点 pt(bbox 探索・クリック判定に使う)。 */
export function viewportToTopDownPt(
  viewport: PdfViewportLike,
  cssX: number,
  cssY: number,
): [number, number] {
  const [x, yNative] = viewport.convertToPdfPoint(cssX, cssY);
  return [x ?? 0, nativeToTopDownY(viewport, yNative ?? 0)];
}

export interface ViewportRect {
  left: number;
  top: number;
  right: number;
  bottom: number;
}

/** bbox(上原点 pt) → viewport css px の矩形(2a §4.2.4 bbox ハイライト用)。 */
export function bboxToViewportRect(viewport: PdfViewportLike, bbox: Bbox): ViewportRect {
  const [x0, y0, x1, y1] = bbox;
  const [cx0, cy0] = topDownPtToViewport(viewport, x0, y0);
  const [cx1, cy1] = topDownPtToViewport(viewport, x1, y1);
  return {
    left: Math.min(cx0, cx1),
    right: Math.max(cx0, cx1),
    top: Math.min(cy0, cy1),
    bottom: Math.max(cy0, cy1),
  };
}

/** bbox(上原点 pt)が点(同座標系)を含むか。 */
export function bboxContainsPoint(bbox: Bbox, x: number, y: number): boolean {
  const [x0, y0, x1, y1] = bbox;
  const minX = Math.min(x0, x1);
  const maxX = Math.max(x0, x1);
  const minY = Math.min(y0, y1);
  const maxY = Math.max(y0, y1);
  return x >= minX && x <= maxX && y >= minY && y <= maxY;
}

export function bboxArea(bbox: Bbox): number {
  const [x0, y0, x1, y1] = bbox;
  return Math.abs(x1 - x0) * Math.abs(y1 - y0);
}

export const ZOOM_MIN = 0.25;
export const ZOOM_MAX = 4.0;
export const ZOOM_STEP = 0.1;

/** ズーム範囲クランプ(2a §5.5。浮動小数誤差を避けるため小数 2 桁に丸める)。 */
export function clampZoom(zoom: number): number {
  const clamped = Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, zoom));
  return Math.round(clamped * 100) / 100;
}

export interface FitScaleOptions {
  /** スクロール領域の左右余白や安全マージン。 */
  horizontalPaddingPx?: number;
  /** スクロール領域の上下余白や安全マージン。 */
  verticalPaddingPx?: number;
  /** 見開きのページ間 gap など、scale で変化しない横幅。 */
  fixedWidthPx?: number;
  /** scale で変化しない縦幅。 */
  fixedHeightPx?: number;
}

/**
 * フィット倍率(2a §5.5)。`fit-width` はページ内容を利用可能幅へ合わせる。
 * 見開き gap など CSS px 固定寸法は `fixedWidthPx` で控除してから倍率を出す。
 */
export function computeFitScale(
  mode: "fit-width" | "fit-page" | "actual",
  containerWidth: number,
  containerHeight: number,
  pageWidthPt: number,
  pageHeightPt: number,
  options: FitScaleOptions = {},
): number {
  if (mode === "actual") return 1;
  const horizontalPaddingPx = options.horizontalPaddingPx ?? 32;
  const verticalPaddingPx = options.verticalPaddingPx ?? 40;
  const fixedWidthPx = options.fixedWidthPx ?? 0;
  const fixedHeightPx = options.fixedHeightPx ?? 0;
  const availableWidth = containerWidth - horizontalPaddingPx - fixedWidthPx;
  const availableHeight = containerHeight - verticalPaddingPx - fixedHeightPx;
  const widthScale = Math.max(0.01, availableWidth / pageWidthPt);
  if (mode === "fit-width") return clampZoom(widthScale);
  const heightScale = Math.max(0.01, availableHeight / pageHeightPt);
  return clampZoom(Math.min(widthScale, heightScale));
}
