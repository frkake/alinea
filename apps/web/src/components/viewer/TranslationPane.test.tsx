import { render, screen, within } from "@testing-library/react";
import { describe, expect, test, vi } from "vitest";
import { SummaryCard } from "@/components/viewer/SummaryCard";
import { EquationBlock } from "@/components/viewer/EquationBlock";
import { SelectionMenu } from "@/components/viewer/SelectionMenu";

// VT-VIEW-02: 訳文モード — ✦3行要約カード・KaTeX ブロック数式
describe("SummaryCard (VT-VIEW-02)", () => {
  test("renders 3 summary lines, AI badge, and 詳細要約 link", () => {
    const lines = [
      "整流フローを提案。",
      "reflow で経路を直線化。",
      "少ステップで高品質生成。",
    ];
    render(<SummaryCard lines={lines} onDetailedSummary={vi.fn()} />);
    expect(screen.getByText("3行要約")).toBeInTheDocument();
    expect(screen.getByText("AI生成")).toBeInTheDocument();
    expect(screen.getByText("詳細要約 →")).toBeInTheDocument();
    for (const line of lines) {
      expect(screen.getByText(line)).toBeInTheDocument();
    }
  });

  test("shows generating placeholder when lines are null", () => {
    render(<SummaryCard lines={null} />);
    expect(screen.getByText("✦ 要約を生成しています…")).toBeInTheDocument();
  });
});

describe("EquationBlock KaTeX (VT-VIEW-02)", () => {
  test("renders KaTeX output and hover actions", () => {
    const { container } = render(<EquationBlock latex="E = mc^2" number="(1)" />);
    // KaTeX が数式 HTML を生成している(.katex クラス)。
    expect(container.querySelector(".katex")).not.toBeNull();
    expect(screen.getByText("この式を説明")).toBeInTheDocument();
    expect(screen.getByText("LaTeXをコピー")).toBeInTheDocument();
    expect(screen.getByText("(1)")).toBeInTheDocument();
  });
});

// VT-VIEW-05: 選択メニュー — M0 は ✦AIに質問 / コピー の 2 項目のみ
describe("SelectionMenu (VT-VIEW-05)", () => {
  test("M0 selection menu shows only ask-AI and copy", () => {
    render(<SelectionMenu milestone="M0" />);
    const menu = screen.getByRole("menu", { name: "選択メニュー" });
    // ✦AIに質問(✦ は AiMark span)/ コピー の 2 項目。ボタン直下テキストで照合。
    expect(within(menu).getByText("AIに質問")).toBeInTheDocument();
    expect(within(menu).getByText("コピー")).toBeInTheDocument();
    expect(screen.queryByText("語彙に追加")).toBeNull();
    expect(screen.queryByText("コメント")).toBeNull();
    // トップレベルの操作は 2 項目(✦AIに質問 / コピー)のみ。
    expect(within(menu).getAllByRole("menuitem")).toHaveLength(2);
  });
});
