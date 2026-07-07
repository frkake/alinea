import { render, screen, fireEvent } from "@testing-library/react";
import { describe, expect, test, vi } from "vitest";
import type { FigureItem } from "@yakudoku/api-client";
import { FigureRefPopover } from "@/components/viewer/FigureRefPopover";

function figure(overrides: Partial<FigureItem> = {}): FigureItem {
  return {
    block_id: "blk-fig2",
    kind: "figure",
    label: "Figure 2",
    display: "図2",
    caption_en: "Figure 2: Trajectories of rectified flows.",
    caption_ja: "図2: 整流フローの軌道。",
    image_url: null,
    position: { section_display: "§2.2", page: 5 },
    ...overrides,
  };
}

// VT-VIEW-06: 図表参照ポップオーバー — 両言語キャプション+「図の位置へ移動 →」
describe("FigureRefPopover (VT-VIEW-06)", () => {
  test("shows bilingual caption and the jump action", () => {
    render(<FigureRefPopover figure={figure()} />);
    expect(screen.getByText("図2: 整流フローの軌道。")).toBeInTheDocument();
    expect(screen.getByText("Figure 2: Trajectories of rectified flows.")).toBeInTheDocument();
    expect(screen.getByText("図の位置へ移動 →")).toBeInTheDocument();
    expect(screen.getByText("拡大")).toBeInTheDocument();
    expect(screen.getByText("この図を説明")).toBeInTheDocument();
  });

  test("action buttons call their handlers", () => {
    const onJump = vi.fn();
    const onZoom = vi.fn();
    const onExplain = vi.fn();
    const fig = figure();
    render(<FigureRefPopover figure={fig} onJumpToFigure={onJump} onZoom={onZoom} onExplain={onExplain} />);
    fireEvent.click(screen.getByText("図の位置へ移動 →"));
    expect(onJump).toHaveBeenCalledWith("blk-fig2");
    fireEvent.click(screen.getByText("拡大"));
    expect(onZoom).toHaveBeenCalledWith(fig);
    fireEvent.click(screen.getByText("この図を説明"));
    expect(onExplain).toHaveBeenCalledWith(fig);
  });

  test("renders placeholder text when the figure has no image", () => {
    render(<FigureRefPopover figure={figure({ image_url: null })} />);
    expect(screen.getByText("図2(原論文の画像)")).toBeInTheDocument();
  });

  test("shows loading placeholder while figures are not yet loaded", () => {
    render(<FigureRefPopover figure={undefined} loading />);
    expect(screen.getByText("読み込み中…")).toBeInTheDocument();
    expect(screen.queryByText("図の位置へ移動 →")).toBeNull();
  });

  test("omits the ja caption row when caption_ja is null", () => {
    render(<FigureRefPopover figure={figure({ caption_ja: null })} />);
    expect(screen.getByText("Figure 2: Trajectories of rectified flows.")).toBeInTheDocument();
    expect(screen.queryByText("図2: 整流フローの軌道。")).toBeNull();
  });
});
