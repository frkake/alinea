import { render, screen, fireEvent } from "@testing-library/react";
import { describe, expect, test, vi } from "vitest";
import { HighlightMark } from "@/components/ui/HighlightMark";

// M1-02: 本文ハイライト描画(4 色 + 注釈番号チップ)
describe("HighlightMark", () => {
  test("wraps children in a <mark> with the color-specific background token", () => {
    render(<HighlightMark color="important">重要な一文</HighlightMark>);
    const mark = screen.getByText("重要な一文");
    expect(mark.tagName).toBe("MARK");
    expect(mark).toHaveStyle({ background: "var(--pr-ann-important-bg)" });
  });

  test.each([
    ["important", "var(--pr-ann-important-bg)"],
    ["question", "var(--pr-ann-question-bg)"],
    ["idea", "var(--pr-ann-idea-bg)"],
    ["term", "var(--pr-ann-term-bg)"],
  ] as const)("color=%s uses %s", (color, bg) => {
    render(<HighlightMark color={color}>text</HighlightMark>);
    expect(screen.getByText("text")).toHaveStyle({ background: bg });
  });

  test("renders no annotation number chip when annotationNumber is omitted", () => {
    render(<HighlightMark color="term">plain</HighlightMark>);
    expect(screen.queryByRole("button")).toBeNull();
  });

  test("renders a round annotation number chip and forwards clicks", () => {
    const onClick = vi.fn();
    render(
      <HighlightMark color="important" annotationNumber={2} onClickAnnotation={onClick}>
        text
      </HighlightMark>,
    );
    const chip = screen.getByRole("button", { name: "注釈 2 を表示" });
    expect(chip).toHaveTextContent("2");
    fireEvent.click(chip);
    expect(onClick).toHaveBeenCalledTimes(1);
  });
});
