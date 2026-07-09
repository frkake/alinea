import { describe, expect, test } from "vitest";
import { isLatexSetupNoiseBlock } from "@/components/viewer/latex-noise";
import type { DocBlock } from "@/components/viewer/document-types";

const paragraph = (text: string): DocBlock => ({
  id: "blk",
  type: "paragraph",
  inlines: [{ t: "text", v: text }],
});

describe("isLatexSetupNoiseBlock", () => {
  test("detects common LaTeX setup fragments persisted by older parses", () => {
    expect(isLatexSetupNoiseBlock(paragraph("iation{ Microsoft Redmond USA }"))).toBe(true);
    expect(
      isLatexSetupNoiseBlock(
        paragraph("ForestGreen{RGB}{34,139,34} RoyalBlue{RGB}{85,118,209}"),
      ),
    ).toBe(true);
    expect(isLatexSetupNoiseBlock(paragraph("[2]{ #1 {▶#2◀} }"))).toBe(true);
    expect(isLatexSetupNoiseBlock(paragraph("blue{MICHELE{#1}}"))).toBe(true);
    expect(isLatexSetupNoiseBlock(paragraph("{Michele Tufano, et al.}"))).toBe(true);
  });

  test("keeps normal body text", () => {
    expect(
      isLatexSetupNoiseBlock(
        paragraph("We evaluate AutoDev on realistic software engineering tasks."),
      ),
    ).toBe(false);
  });
});
