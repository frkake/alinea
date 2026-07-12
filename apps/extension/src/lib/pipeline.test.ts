import { describe, expect, test } from "vitest";
import { footerRightText, isProcessingStage, pipelineRows } from "./pipeline";

describe("long-paper section-selection pipeline state", () => {
  test("shows a durable user-input wait instead of falling back to bibliography fetching", () => {
    expect(pipelineRows("selecting_sections", 52)).toEqual([
      { label: "✓ 書誌", tone: "done" },
      { label: "✓ 構造化", tone: "done" },
      { label: "セクション選択待ち", tone: "muted" },
    ]);
    expect(footerRightText({ stage: "selecting_sections", progress_pct: 52 }, null)).toBe(
      "セクション選択待ち",
    );
    expect(isProcessingStage("selecting_sections")).toBe(true);
  });
});
