import { render, screen, fireEvent, within } from "@testing-library/react";
import { describe, expect, test, vi } from "vitest";
import { SelectionMenu } from "@/components/viewer/SelectionMenu";

// M1-02: 選択メニュー完全化(4 色ハイライト・コメント)。語彙に追加は M2 まで非表示。
describe("SelectionMenu milestone=M1", () => {
  test("shows 4 color dots, コメント, AIに質問, コピー — but not 語彙に追加", () => {
    render(<SelectionMenu milestone="M1" />);
    const menu = screen.getByRole("menu", { name: "選択メニュー" });
    expect(within(menu).getByLabelText("重要でハイライト")).toBeInTheDocument();
    expect(within(menu).getByLabelText("疑問でハイライト")).toBeInTheDocument();
    expect(within(menu).getByLabelText("アイデアでハイライト")).toBeInTheDocument();
    expect(within(menu).getByLabelText("用語でハイライト")).toBeInTheDocument();
    expect(within(menu).getByText("コメント")).toBeInTheDocument();
    expect(within(menu).getByText("AIに質問")).toBeInTheDocument();
    expect(within(menu).getByText("コピー")).toBeInTheDocument();
    expect(screen.queryByText("語彙に追加")).toBeNull();
  });

  test("clicking a color dot calls onHighlight with the color", () => {
    const onHighlight = vi.fn();
    render(<SelectionMenu milestone="M1" onHighlight={onHighlight} />);
    fireEvent.click(screen.getByLabelText("疑問でハイライト"));
    expect(onHighlight).toHaveBeenCalledWith("question");
  });

  test("コメント opens an inline popup; saving calls onComment with color+text", () => {
    const onComment = vi.fn();
    render(<SelectionMenu milestone="M1" onComment={onComment} />);
    fireEvent.click(screen.getByText("コメント"));
    const dialog = screen.getByRole("dialog", { name: "コメントを入力" });
    fireEvent.change(within(dialog).getByLabelText("コメント本文"), {
      target: { value: "ここが本質" },
    });
    fireEvent.click(within(dialog).getByLabelText("疑問を選択"));
    fireEvent.click(within(dialog).getByText("保存"));
    expect(onComment).toHaveBeenCalledWith("question", "ここが本質");
    expect(screen.queryByRole("dialog", { name: "コメントを入力" })).toBeNull();
  });

  test("comment popup defaults to important and allows empty comment (color-only highlight)", () => {
    const onComment = vi.fn();
    render(<SelectionMenu milestone="M1" onComment={onComment} />);
    fireEvent.click(screen.getByText("コメント"));
    fireEvent.click(screen.getByText("保存"));
    expect(onComment).toHaveBeenCalledWith("important", "");
  });

  test("Escape inside the comment textarea closes only the popup, not the whole menu", () => {
    render(<SelectionMenu milestone="M1" />);
    fireEvent.click(screen.getByText("コメント"));
    const dialog = screen.getByRole("dialog", { name: "コメントを入力" });
    fireEvent.keyDown(within(dialog).getByLabelText("コメント本文"), { key: "Escape" });
    expect(screen.queryByRole("dialog", { name: "コメントを入力" })).toBeNull();
    // メニュー自体は残る。
    expect(screen.getByRole("menu", { name: "選択メニュー" })).toBeInTheDocument();
  });
});

// 後方互換: M0 は変更なし(既存 VT-VIEW-05 契約を維持)。
describe("SelectionMenu milestone=M0 (backward compatibility)", () => {
  test("still shows only ask-AI and copy, no colors/comment", () => {
    render(<SelectionMenu milestone="M0" />);
    const menu = screen.getByRole("menu", { name: "選択メニュー" });
    expect(within(menu).getAllByRole("menuitem")).toHaveLength(2);
    expect(screen.queryByLabelText("重要でハイライト")).toBeNull();
    expect(screen.queryByText("コメント")).toBeNull();
  });
});

// M2-12: 「語彙に追加」(plans/09-screens/1b §5.5)。呼び出し側の実 API 連携は本タスクの
// 所有外(SelectionController 相当。TranslationPane 等)だが、UI の活性/非活性・クリック
// 発火は SelectionMenu 自身が担う。
describe("SelectionMenu milestone=M2 (語彙に追加)", () => {
  test("shows 語彙に追加 alongside the M1 items", () => {
    render(<SelectionMenu milestone="M2" side="source" />);
    const menu = screen.getByRole("menu", { name: "選択メニュー" });
    expect(within(menu).getByText("語彙に追加")).toBeInTheDocument();
    expect(within(menu).getByText("コメント")).toBeInTheDocument();
    expect(within(menu).getByText("AIに質問")).toBeInTheDocument();
    expect(within(menu).getByText("コピー")).toBeInTheDocument();
  });

  test("clicking 語彙に追加 calls onAddVocab when side='source'", () => {
    const onAddVocab = vi.fn();
    render(<SelectionMenu milestone="M2" side="source" onAddVocab={onAddVocab} />);
    fireEvent.click(screen.getByText("語彙に追加"));
    expect(onAddVocab).toHaveBeenCalledTimes(1);
  });

  test("語彙に追加 is disabled with a hint title when side='translation'", () => {
    const onAddVocab = vi.fn();
    render(<SelectionMenu milestone="M2" side="translation" onAddVocab={onAddVocab} />);
    const button = screen.getByText("語彙に追加").closest("button");
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute("title", "原文(英語)の選択でのみ使えます");
    fireEvent.click(screen.getByText("語彙に追加"));
    expect(onAddVocab).not.toHaveBeenCalled();
  });

  test("milestone=M1 still hides 語彙に追加 (unwired panes stay on M1 until updated)", () => {
    render(<SelectionMenu milestone="M1" side="source" />);
    expect(screen.queryByText("語彙に追加")).toBeNull();
  });
});
