import { describe, expect, test } from "vitest";
import { isSectionHeadingBlock, sectionHeadingBlock } from "@/components/viewer/section-heading-block";
import type { DocSection } from "@/components/viewer/document-types";

describe("sectionHeadingBlock", () => {
  test("detects the leading block that mirrors the section heading", () => {
    const section: DocSection = {
      id: "sec-1",
      heading: { number: "1", title: "Introduction" },
      blocks: [
        { id: "blk-heading", type: "heading", number: "1", title: "Introduction" },
        { id: "blk-p1", type: "paragraph", inlines: [{ t: "text", v: "Body" }] },
      ],
      sections: [],
    };

    const bodyBlock = section.blocks?.[1];
    expect(sectionHeadingBlock(section)?.id).toBe("blk-heading");
    expect(bodyBlock).toBeDefined();
    expect(bodyBlock ? isSectionHeadingBlock(section, bodyBlock) : true).toBe(false);
  });
});
