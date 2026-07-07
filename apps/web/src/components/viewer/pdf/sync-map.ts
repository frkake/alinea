/**
 * page+bbox 同期マッピング(2a §5.4)。`GET /api/revisions/{id}/document` の全量から
 * クライアント側で 1 回構築する(`useMemo`)。品質 A/B どちらもブロックが `page`/`bbox`
 * を持つ場合に同期対象になる(持たないブロックは同期不能=null)。
 */

import type { DocBlock, DocSection, DocumentResponse } from "@/components/viewer/document-types";
import { bboxArea, bboxContainsPoint, type Bbox } from "./geometry";

export interface SyncSectionHit {
  sectionId: string;
  display: string; // "§2.2 Reflow"
}

export interface SyncBlockHit {
  blockId: string;
  bbox: Bbox;
  display: string; // "§2.2 ¶2"
}

export interface PdfSyncMap {
  pageToSection: (page: number) => SyncSectionHit | null;
  blockAtPoint: (page: number, xPt: number, yPt: number) => SyncBlockHit | null;
  blocksOnPage: (page: number) => { blockId: string; bbox: Bbox }[];
  firstBlockOnPage: (page: number) => string | null;
  /** 既知の blockId から直接 display("§2.2 ¶2")を導出する(座標の再ヒット判定はしない)。 */
  displayForBlock: (blockId: string) => string | null;
  /** blockId が属するページ(逆引き。深リンク・モード間位置引き継ぎの初期ページ解決用)。 */
  pageForBlock: (blockId: string) => number | null;
  /** セクション内で最初に bbox を持つブロックのページ(§ 深リンク用)。 */
  firstPageOfSection: (sectionId: string) => number | null;
  /** blockId が属するセクション id(useViewerStore.setCurrentBlock 用)。 */
  sectionForBlock: (blockId: string) => string | null;
}

interface FlatEntry {
  section: DocSection;
  block: DocBlock;
  /** そのセクション内の paragraph 出現順(1 起点)。paragraph 以外は 0。 */
  paraIndex: number;
}

function flatten(sections: DocSection[]): FlatEntry[] {
  const out: FlatEntry[] = [];
  const walk = (secs: DocSection[]) => {
    for (const section of secs) {
      let paraIndex = 0;
      for (const block of section.blocks ?? []) {
        if (block.type === "paragraph") paraIndex += 1;
        out.push({ section, block, paraIndex: block.type === "paragraph" ? paraIndex : 0 });
      }
      if (section.sections?.length) walk(section.sections);
    }
  };
  walk(sections);
  return out;
}

/** 「2.2 Reflow: Straightening the Flow」→「§2.2 Reflow」(§5.4)。number 無しは番号を付けない。 */
export function sectionShortDisplay(number: string | null | undefined, title: string): string {
  const cut = title.split(":")[0]?.trim() ?? title.trim();
  const short = cut.length > 20 ? `${cut.slice(0, 19)}…` : cut;
  const num = (number ?? "").trim();
  return num ? `§${num} ${short}`.trim() : short;
}

/** entry から display("§2.2 ¶2" / 段落以外は ¶ 無し)を導出する(§5.4)。 */
function entryDisplay(entry: FlatEntry): string {
  const heading = entry.section.heading;
  const sectionDisplay = sectionShortDisplay(heading?.number ?? null, heading?.title ?? "");
  return entry.block.type === "paragraph" && entry.paraIndex > 0
    ? `${sectionDisplay} ¶${entry.paraIndex}`
    : sectionDisplay;
}

/**
 * 決定(仕様からの差分): §5.4 は `buildPdfSyncMap(document, toc)` の 2 引数だが、
 * `toc[].number`/`title_en` は `document.sections[].heading` と同一ソース(いずれも
 * バックエンドが `Section.heading` から構築。`routers/viewer.py::_build_toc`)なので、
 * 本実装は `document` のみから導出する(toc は受け取らない・冗長な依存を避ける)。
 */
export function buildPdfSyncMap(document: DocumentResponse | undefined): PdfSyncMap {
  const entries = document ? flatten(document.sections ?? []) : [];
  const withPage = entries.filter((e) => typeof e.block.page === "number");
  const withBbox = withPage.filter(
    (e) => Array.isArray(e.block.bbox) && e.block.bbox.length === 4,
  );

  function pageToSection(page: number): SyncSectionHit | null {
    const onPage = withBbox.filter((e) => e.block.page === page);
    if (onPage.length === 0) return null;
    const bySection = new Map<string, { section: DocSection; area: number }>();
    for (const e of onPage) {
      const area = bboxArea(e.block.bbox as Bbox);
      const prev = bySection.get(e.section.id);
      if (prev) prev.area += area;
      else bySection.set(e.section.id, { section: e.section, area });
    }
    let best: { section: DocSection; area: number } | null = null;
    for (const entry of bySection.values()) {
      if (!best || entry.area > best.area) best = entry;
    }
    if (!best) return null;
    const heading = best.section.heading;
    return {
      sectionId: best.section.id,
      display: sectionShortDisplay(heading?.number ?? null, heading?.title ?? ""),
    };
  }

  function blockAtPoint(page: number, xPt: number, yPt: number): SyncBlockHit | null {
    const onPage = withBbox.filter((e) => e.block.page === page);
    let best: FlatEntry | null = null;
    let bestArea = Infinity;
    for (const e of onPage) {
      const bbox = e.block.bbox as Bbox;
      if (!bboxContainsPoint(bbox, xPt, yPt)) continue;
      const area = bboxArea(bbox);
      if (area < bestArea) {
        bestArea = area;
        best = e;
      }
    }
    if (!best) return null;
    return { blockId: best.block.id, bbox: best.block.bbox as Bbox, display: entryDisplay(best) };
  }

  function displayForBlock(blockId: string): string | null {
    const entry = withBbox.find((e) => e.block.id === blockId);
    return entry ? entryDisplay(entry) : null;
  }

  function pageForBlock(blockId: string): number | null {
    const entry = withBbox.find((e) => e.block.id === blockId);
    return entry ? (entry.block.page as number) : null;
  }

  function sectionForBlock(blockId: string): string | null {
    const entry = entries.find((e) => e.block.id === blockId);
    return entry ? entry.section.id : null;
  }

  function firstPageOfSection(sectionId: string): number | null {
    let best: number | null = null;
    for (const e of withBbox) {
      if (e.section.id !== sectionId) continue;
      const page = e.block.page as number;
      if (best == null || page < best) best = page;
    }
    return best;
  }

  function blocksOnPage(page: number): { blockId: string; bbox: Bbox }[] {
    return withBbox
      .filter((e) => e.block.page === page)
      .map((e) => ({ blockId: e.block.id, bbox: e.block.bbox as Bbox }));
  }

  function firstBlockOnPage(page: number): string | null {
    const onPage = withBbox.filter((e) => e.block.page === page);
    if (onPage.length === 0) return null;
    // PDF は上原点(y 小=ページ上部)。上端(y1 最小)が最も上のブロック。
    let best: FlatEntry | null = null;
    let bestTop = Infinity;
    for (const e of onPage) {
      const [, y0] = e.block.bbox as Bbox;
      if (y0 < bestTop) {
        bestTop = y0;
        best = e;
      }
    }
    return best?.block.id ?? null;
  }

  return {
    pageToSection,
    blockAtPoint,
    blocksOnPage,
    firstBlockOnPage,
    displayForBlock,
    pageForBlock,
    firstPageOfSection,
    sectionForBlock,
  };
}
