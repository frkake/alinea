import { describe, expect, test } from "vitest";
import type { DocumentResponse } from "@/components/viewer/document-types";
import { buildReferenceTargetMap, resolveReferenceTarget } from "@/components/viewer/reference-targets";

const doc: DocumentResponse = {
  revision_id: "rev-1",
  quality_level: "A",
  sections: [
    {
      id: "sec-method",
      heading: { number: "2.1", title: "Method" },
      blocks: [
        { id: "blk-heading", type: "heading", number: "2.1", title: "Method" },
        {
          id: "blk-fig",
          type: "figure",
          number: "1",
          label: "S1.F1",
          caption: [{ t: "text", v: "Overview." }],
        },
        {
          id: "blk-table",
          type: "table",
          number: "2",
          label: "tbl:results",
          caption: [{ t: "text", v: "Scores." }],
        },
        {
          id: "blk-eq",
          type: "equation",
          number: "3",
          label: "eq:loss",
          latex: "L(x)",
        },
      ],
    },
  ],
};

describe("reference-targets", () => {
  test("resolves figure, table, equation, and section aliases", () => {
    const map = buildReferenceTargetMap(doc.sections);
    expect(resolveReferenceTarget(map, "S1.F1")).toBe("blk-fig");
    expect(resolveReferenceTarget(map, "figure:1")).toBe("blk-fig");
    expect(resolveReferenceTarget(map, "fig 1")).toBe("blk-fig");
    expect(resolveReferenceTarget(map, "tbl.results")).toBe("blk-table");
    expect(resolveReferenceTarget(map, "table-2")).toBe("blk-table");
    expect(resolveReferenceTarget(map, "eq-3")).toBe("blk-eq");
    expect(resolveReferenceTarget(map, "section:2.1")).toBe("blk-heading");
  });
});
