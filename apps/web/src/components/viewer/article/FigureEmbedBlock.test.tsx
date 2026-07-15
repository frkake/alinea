import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import { FigureEmbedBlock } from "@/components/viewer/article/FigureEmbedBlock";
import { ExplainerFigureBlock } from "@/components/viewer/article/ExplainerFigureBlock";

describe("article figures", () => {
  test("renders source tables as horizontally scrollable HTML instead of a missing-image placeholder", () => {
    render(
      <FigureEmbedBlock
        figure={{
          figure_block_id: "blk-table",
          kind: "table",
          image_url: "",
          table_rows: [
            ["Model", "FAD"],
            ["CALM", "0.93"],
          ],
          caption_ja: "比較結果",
          credit: "出典",
          license_badge: "CC BY",
          caption_separated: false,
          share_alike: false,
        }}
      />,
    );
    expect(screen.getByRole("table")).toHaveTextContent("CALM");
    expect(screen.queryByText("図(原論文の画像)")).not.toBeInTheDocument();
  });

  test("renders LaTeX fragments in source-table cells instead of exposing commands", () => {
    const { container } = render(
      <FigureEmbedBlock
        figure={{
          figure_block_id: "blk-math-table",
          kind: "table",
          image_url: "",
          table_rows: [
            ["通信量", "$ T \\cdot N_H \\cdot D_H $"],
            ["パラメータサイズ", "$\\frac{W}{N_{TP}}$"],
          ],
          caption_ja: "計算量の比較",
          credit: "出典",
          license_badge: "CC BY",
          caption_separated: false,
          share_alike: false,
        }}
      />,
    );

    expect(container.querySelectorAll(".katex")).toHaveLength(2);
    expect(screen.getByRole("table")).not.toHaveTextContent("\\\\cdot");
    expect(screen.getByRole("table")).not.toHaveTextContent("\\\\frac");
  });

  test("keeps very wide source figures readable instead of shrinking them into a thin strip", () => {
    render(
      <FigureEmbedBlock
        figure={{
          figure_block_id: "blk-wide",
          kind: "figure",
          image_url: "/api/assets/wide",
          table_rows: null,
          caption_ja: "横長の構成図",
          credit: "出典",
          license_badge: "CC BY",
          caption_separated: false,
          share_alike: false,
        }}
      />,
    );
    const image = screen.getByAltText("横長の構成図");
    Object.defineProperty(image, "naturalWidth", { configurable: true, value: 1600 });
    Object.defineProperty(image, "naturalHeight", { configurable: true, value: 220 });
    fireEvent.load(image);
    expect(image).toHaveStyle({ minWidth: "960px", width: "auto" });
  });

  test("shows AI generation and loading states until an explainer image is ready", () => {
    const { rerender } = render(
      <ExplainerFigureBlock explainer={{ figure_id: "", image_url: "", caption: "概念図" }} />,
    );
    expect(screen.getByText("AI 解説図を生成しています…")).toBeInTheDocument();

    rerender(
      <ExplainerFigureBlock
        explainer={{ figure_id: "fig-1", image_url: "/api/assets/image", caption: "概念図" }}
      />,
    );
    expect(screen.getByText("AI 解説図を読み込んでいます…")).toBeInTheDocument();
    fireEvent.load(screen.getByAltText("概念図"));
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });
});
