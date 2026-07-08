import { render, screen } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import { FigureTableBlock } from "@/components/viewer/FigureTableBlock";
import type { DocBlock } from "@/components/viewer/document-types";
import type { TranslationUnitItem } from "@yakudoku/api-client";

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

  test("cleans common LaTeX commands inside table cells", () => {
    const block: DocBlock = {
      id: "blk-table",
      type: "table",
      number: "2",
      raw: "\\begin{tabular}{ll} Metric & Value \\\\ Acc & 1.2 \\pm 0.1 \\\\ State & x_i \\\\ \\end{tabular}",
      caption: [{ t: "text", v: "Scores." }],
    };
    const { container } = render(<FigureTableBlock block={block} />);
    expect(screen.getByText("1.2 ± 0.1")).toBeInTheDocument();
    expect(container.textContent).not.toContain("\\pm");
    expect(container.querySelector(".katex")).not.toBeNull();
  });

  test("renders inline SVG figure raw content when no asset URL exists", () => {
    const block: DocBlock = {
      id: "blk-svg",
      type: "figure",
      number: "3",
      raw: '<div class="ltx_flex_figure"><svg width="40" height="20"><title>chart</title></svg></div>',
      caption: [{ t: "text", v: "Inline chart." }],
    };
    render(<FigureTableBlock block={block} />);
    expect(screen.getByRole("img", { name: "図3" })).toContainHTML("<svg");
    expect(screen.getByText("Inline chart.")).toBeInTheDocument();
  });

  test("uses the translated caption as the primary caption when a translated unit exists", () => {
    const block: DocBlock = {
      id: "blk-fig",
      type: "figure",
      number: "4",
      caption: [{ t: "text", v: "Original caption." }],
    };
    const unit: TranslationUnitItem = {
      unit_id: "unit_fig",
      block_id: "blk-fig",
      text_ja: "翻訳済みキャプション。",
      content_ja: [{ t: "text", v: "翻訳済みキャプション。" }],
      state: "machine",
      quality_flags: [],
      proposal: null,
    };
    render(<FigureTableBlock block={block} unit={unit} />);
    expect(screen.getByText("翻訳済みキャプション。")).toBeInTheDocument();
    expect(screen.getByText("Original: Original caption.")).toBeInTheDocument();
  });
});
