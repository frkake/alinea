import { render, screen } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import { SourceBadge } from "@/components/search/SourceBadge";

describe("SourceBadge (plans/08 §5.22)", () => {
  test("renders the given label", () => {
    render(<SourceBadge tone="body" label="本文 · 原文" />);
    expect(screen.getByText("本文 · 原文")).toBeInTheDocument();
  });

  test("md size uses a larger font than sm", () => {
    const { rerender, container } = render(<SourceBadge tone="chat" label="チャット" size="sm" />);
    const sm = container.querySelector("span");
    expect(sm).toHaveStyle({ fontSize: "9px" });
    rerender(<SourceBadge tone="chat" label="チャット" size="md" />);
    const md = container.querySelector("span");
    expect(md).toHaveStyle({ fontSize: "9.5px" });
  });
});
