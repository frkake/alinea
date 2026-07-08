import { render, screen } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import { FigureTableBlock } from "@/components/viewer/FigureTableBlock";
import type { DocBlock } from "@/components/viewer/document-types";

describe("FigureTableBlock", () => {
  test("renders a figure asset with its caption", () => {
    const block: DocBlock = {
      id: "blk-fig",
      type: "figure",
      number: "1",
      asset_url: "/api/assets/abc",
      caption: [{ t: "text", v: "Overview." }],
    };
    render(<FigureTableBlock block={block} />);
    expect(screen.getByRole("img", { name: "図1" })).toHaveAttribute("src", "/api/assets/abc");
    expect(screen.getByText("Overview.")).toBeInTheDocument();
  });

  test("renders LaTeX tabular raw content as a table", () => {
    const block: DocBlock = {
      id: "blk-table",
      type: "table",
      number: "2",
      raw: "\\begin{tabular}{ll} Method & Score \\\\ Ours & $x^2$ \\\\ \\end{tabular}",
      caption: [{ t: "text", v: "Scores." }],
    };
    render(<FigureTableBlock block={block} />);
    expect(screen.getByRole("table")).toBeInTheDocument();
    expect(screen.getByText("Method")).toBeInTheDocument();
    expect(screen.getByText("Ours")).toBeInTheDocument();
    expect(screen.getByText("Scores.")).toBeInTheDocument();
  });
});
