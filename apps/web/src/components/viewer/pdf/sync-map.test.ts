import { describe, expect, test } from "vitest";
import type { DocumentResponse } from "@/components/viewer/document-types";
import { buildPdfSyncMap, sectionShortDisplay } from "./sync-map";

const doc: DocumentResponse = {
  revision_id: "rev-1",
  quality_level: "B",
  sections: [
    {
      id: "sec-abstract",
      heading: { number: "", title: "Abstract" },
      blocks: [
        { id: "blk-abs-1", type: "paragraph", page: 1, bbox: [50, 500, 550, 560] },
      ],
    },
    {
      id: "sec-2",
      heading: { number: "2", title: "Method" },
      blocks: [
        { id: "blk-2-h", type: "heading", number: "2", title: "Method", page: 4, bbox: [50, 60, 300, 90] },
        { id: "blk-2-p1", type: "paragraph", page: 4, bbox: [50, 100, 550, 300] },
      ],
      sections: [
        {
          id: "sec-2-2",
          heading: { number: "2.2", title: "Reflow: Straightening the Flow" },
          blocks: [
            { id: "blk-2-2-h", type: "heading", number: "2.2", title: "Reflow: Straightening the Flow", page: 5, bbox: [50, 60, 300, 90] },
            { id: "blk-2-2-p1", type: "paragraph", page: 5, bbox: [50, 100, 550, 300] },
            { id: "blk-2-2-p2", type: "paragraph", page: 5, bbox: [50, 320, 550, 600] },
            { id: "blk-2-2-fig", type: "figure", page: 5, bbox: [560, 100, 1000, 400] },
            { id: "blk-2-2-p3-nolayout", type: "paragraph" },
          ],
        },
      ],
    },
  ],
};

describe("sectionShortDisplay", () => {
  test("truncates at the first colon and adds the § prefix when number present", () => {
    expect(sectionShortDisplay("2.2", "Reflow: Straightening the Flow")).toBe("§2.2 Reflow");
  });

  test("omits § when number is absent (e.g. abstract)", () => {
    expect(sectionShortDisplay(null, "Abstract")).toBe("Abstract");
  });

  test("truncates long titles without a colon to 19 chars + ellipsis", () => {
    const long = "A Very Long Section Title Without Colon Here";
    const result = sectionShortDisplay("3", long);
    expect(result).toBe(`§3 ${long.slice(0, 19)}…`);
  });
});

describe("buildPdfSyncMap.pageToSection", () => {
  const map = buildPdfSyncMap(doc);

  test("picks the section with the largest bbox area on the page", () => {
    const hit = map.pageToSection(5);
    expect(hit?.sectionId).toBe("sec-2-2");
    expect(hit?.display).toBe("§2.2 Reflow");
  });

  test("returns null for a page with no synced blocks", () => {
    expect(map.pageToSection(99)).toBeNull();
  });

  test("omits § for sections without a number (abstract)", () => {
    const hit = map.pageToSection(1);
    expect(hit?.display).toBe("Abstract");
  });
});

describe("buildPdfSyncMap.blockAtPoint", () => {
  const map = buildPdfSyncMap(doc);

  test("hits the smallest-area block containing the point and numbers paragraphs within their section", () => {
    const hit = map.blockAtPoint(5, 100, 150);
    expect(hit?.blockId).toBe("blk-2-2-p1");
    expect(hit?.display).toBe("§2.2 Reflow ¶1");
  });

  test("second paragraph in the same section gets ¶2", () => {
    const hit = map.blockAtPoint(5, 100, 400);
    expect(hit?.blockId).toBe("blk-2-2-p2");
    expect(hit?.display).toBe("§2.2 Reflow ¶2");
  });

  test("non-paragraph hits (figure) omit the ¶ suffix", () => {
    const hit = map.blockAtPoint(5, 700, 200);
    expect(hit?.blockId).toBe("blk-2-2-fig");
    expect(hit?.display).toBe("§2.2 Reflow");
  });

  test("returns null when no block contains the point", () => {
    expect(map.blockAtPoint(5, 5000, 5000)).toBeNull();
  });
});

describe("buildPdfSyncMap.blocksOnPage / firstBlockOnPage", () => {
  const map = buildPdfSyncMap(doc);

  test("blocksOnPage lists all bbox-bearing blocks for the page", () => {
    const ids = map.blocksOnPage(5).map((b) => b.blockId);
    expect(ids).toEqual(["blk-2-2-h", "blk-2-2-p1", "blk-2-2-p2", "blk-2-2-fig"]);
  });

  test("firstBlockOnPage returns the block nearest the top (min y0 in top-origin pt)", () => {
    expect(map.firstBlockOnPage(5)).toBe("blk-2-2-h");
  });

  test("firstBlockOnPage returns null for an unsynced page", () => {
    expect(map.firstBlockOnPage(42)).toBeNull();
  });
});

describe("buildPdfSyncMap.displayForBlock / pageForBlock / firstPageOfSection", () => {
  const map = buildPdfSyncMap(doc);

  test("displayForBlock derives the same display as blockAtPoint without a coordinate hit-test", () => {
    expect(map.displayForBlock("blk-2-2-p2")).toBe("§2.2 Reflow ¶2");
    expect(map.displayForBlock("blk-2-2-fig")).toBe("§2.2 Reflow");
  });

  test("displayForBlock returns null for blocks without page/bbox", () => {
    expect(map.displayForBlock("blk-2-2-p3-nolayout")).toBeNull();
  });

  test("pageForBlock reverse-looks-up the page of a known block", () => {
    expect(map.pageForBlock("blk-2-2-p1")).toBe(5);
    expect(map.pageForBlock("nonexistent")).toBeNull();
  });

  test("firstPageOfSection returns the lowest page among a section's synced blocks", () => {
    expect(map.firstPageOfSection("sec-2-2")).toBe(5);
    expect(map.firstPageOfSection("no-such-section")).toBeNull();
  });
});
