import { render, screen } from "@testing-library/react";
import { describe, expect, test, vi } from "vitest";
import { ParagraphBlock } from "@/components/viewer/article/ParagraphBlock";

describe("ParagraphBlock", () => {
  test("wraps long evidence labels without forcing a fixed-height row", () => {
    render(
      <ParagraphBlock
        markdown="CALM の構成を説明する。"
        includeMath={false}
        evidence={[
          {
            ref: 1,
            display: "1. Causal Backbone Transformer with Noise Injection ¶1",
            anchor: {
              revision_id: "rev_1",
              block_id: "blk_1",
              display: "1. Causal Backbone Transformer with Noise Injection ¶1",
            },
          },
          {
            ref: 2,
            display: "2. Short-Context Transformer ¶1",
            anchor: {
              revision_id: "rev_1",
              block_id: "blk_2",
              display: "2. Short-Context Transformer ¶1",
            },
          },
        ]}
        onJumpToAnchor={vi.fn()}
      />,
    );

    expect(screen.getByTestId("article-evidence-chips")).toHaveStyle({ flexWrap: "wrap" });
    expect(screen.getByRole("button", { name: /Causal Backbone/ })).toHaveStyle({
      maxWidth: "100%",
      minHeight: "18px",
      whiteSpace: "normal",
      overflowWrap: "anywhere",
    });
  });
});
