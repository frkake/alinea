import { render, screen, fireEvent } from "@testing-library/react";
import { describe, expect, test, vi } from "vitest";
import { VocabFilterRow } from "@/components/vocab/VocabFilterRow";

const COUNTS = { all: 46, word: 28, collocation: 12, idiom: 6, due: 12 };

// VT-VOC-01(フィルタ部分): 種別チップ 3 分類+復習期チップの絞り込み。
describe("VocabFilterRow (VT-VOC-01 / VT-VOC-04)", () => {
  test("renders all chips with their counts (label + count. 4d §4.2.4)", () => {
    render(
      <VocabFilterRow counts={COUNTS} kind={null} dueOnly={false} onKindChange={vi.fn()} onDueToggle={vi.fn()} />,
    );
    expect(screen.getByText("すべて")).toBeInTheDocument();
    expect(screen.getByText("46")).toBeInTheDocument();
    expect(screen.getByText("単語")).toBeInTheDocument();
    expect(screen.getByText("28")).toBeInTheDocument();
    expect(screen.getByText("コロケーション")).toBeInTheDocument();
    expect(screen.getByText("12")).toBeInTheDocument();
    expect(screen.getByText("イディオム")).toBeInTheDocument();
    expect(screen.getByText("6")).toBeInTheDocument();
    expect(screen.getByText("復習期 12")).toBeInTheDocument();
    expect(
      screen.getByText("本文で選択 → 「語彙に追加」で文脈ごと保存されます"),
    ).toBeInTheDocument();
  });

  test("clicking a kind chip calls onKindChange with that kind (排他単一選択)", () => {
    const onKindChange = vi.fn();
    render(
      <VocabFilterRow counts={COUNTS} kind={null} dueOnly={false} onKindChange={onKindChange} onDueToggle={vi.fn()} />,
    );
    fireEvent.click(screen.getByText("イディオム"));
    expect(onKindChange).toHaveBeenCalledWith("idiom");
  });

  test("clicking すべて while a kind is selected clears the filter (kind=null)", () => {
    const onKindChange = vi.fn();
    render(
      <VocabFilterRow counts={COUNTS} kind="word" dueOnly={false} onKindChange={onKindChange} onDueToggle={vi.fn()} />,
    );
    fireEvent.click(screen.getByText("すべて"));
    expect(onKindChange).toHaveBeenCalledWith(null);
  });

  test("復習期 chip toggles independently of kind selection and reflects active styling (VT-VOC-04)", () => {
    const onDueToggle = vi.fn();
    const { rerender } = render(
      <VocabFilterRow counts={COUNTS} kind="idiom" dueOnly={false} onKindChange={vi.fn()} onDueToggle={onDueToggle} />,
    );
    const dueChip = screen.getByRole("button", { name: "復習期 12" });
    expect(dueChip).toHaveAttribute("aria-pressed", "false");
    fireEvent.click(dueChip);
    expect(onDueToggle).toHaveBeenCalledTimes(1);

    rerender(
      <VocabFilterRow counts={COUNTS} kind="idiom" dueOnly onKindChange={vi.fn()} onDueToggle={onDueToggle} />,
    );
    expect(screen.getByRole("button", { name: "復習期 12" })).toHaveAttribute("aria-pressed", "true");
  });
});
