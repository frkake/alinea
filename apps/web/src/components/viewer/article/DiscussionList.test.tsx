import { render, screen } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import type { DiscussionContentOut } from "@alinea/api-client";
import { DiscussionList } from "@/components/viewer/article/DiscussionList";

function discussion(overrides: Partial<DiscussionContentOut> = {}): DiscussionContentOut {
  return {
    items: [
      { text: "reflow を重ねると周辺分布の誤差は蓄積しないか", origin: "user_highlight" },
      { text: "Flow Matching との理論的な差分はどこか", origin: "ai" },
    ],
    ...overrides,
  };
}

// VT-VIEW-16: 「あなたの疑問ハイライトから」バッジ(origin=user_highlight のみ)
describe("DiscussionList (VT-VIEW-16)", () => {
  test("renders the heading and AI構成 badge", () => {
    render(<DiscussionList discussion={discussion()} />);
    expect(screen.getByText("議論したい点")).toBeInTheDocument();
    expect(screen.getByText("✦ AI構成")).toBeInTheDocument();
  });

  test("shows the origin badge only for user_highlight items", () => {
    render(<DiscussionList discussion={discussion()} />);
    const badges = screen.getAllByText("あなたの疑問ハイライトから");
    expect(badges).toHaveLength(1);
  });

  test("renders all items with numbered prefixes", () => {
    render(<DiscussionList discussion={discussion()} />);
    expect(screen.getByText("1.")).toBeInTheDocument();
    expect(screen.getByText("2.")).toBeInTheDocument();
  });

  test("shows no origin badge when all items are ai-authored", () => {
    render(<DiscussionList discussion={discussion({ items: [{ text: "x", origin: "ai" }] })} />);
    expect(screen.queryByText("あなたの疑問ハイライトから")).not.toBeInTheDocument();
  });
});
