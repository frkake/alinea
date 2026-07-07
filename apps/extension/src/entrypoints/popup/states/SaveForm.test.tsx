import { render, screen } from "@testing-library/react";
import { fireEvent } from "@testing-library/react";
import { expect, test, vi } from "vitest";

import { SaveForm } from "./SaveForm";

// 計画 Task 32 Step 1(保存前フォームの品質見込み表示)。
test("shows quality A estimate when latex available", () => {
  render(<SaveForm preview={{ title: "Rectified Flow", latexAvailable: true }} />);
  expect(screen.getByText(/品質レベル A 見込み/)).toBeInTheDocument();
  expect(screen.queryByText("コレクション")).toBeNull(); // M2 まで非表示
});

test("shows quality B estimate when latex missing and hides row when null", () => {
  const { rerender } = render(
    <SaveForm preview={{ title: "X", latexAvailable: false }} />,
  );
  expect(screen.getByText(/品質レベル B 見込み/)).toBeInTheDocument();

  rerender(<SaveForm preview={{ title: "X", latexAvailable: null }} />);
  expect(screen.queryByText(/品質レベル/)).toBeNull();
});

test("default status is planned (読む予定) and 3 choices are shown", () => {
  render(<SaveForm preview={{ title: "X", latexAvailable: true }} />);
  expect(screen.getByRole("radio", { name: /読む予定/ })).toHaveAttribute("aria-checked", "true");
  expect(screen.getByRole("radio", { name: "すぐ読む" })).toBeInTheDocument();
  expect(screen.getByRole("radio", { name: "読んでいる" })).toBeInTheDocument();
});

test("save button emits the selected status, tags and note", () => {
  const onSave = vi.fn();
  render(
    <SaveForm
      preview={{ title: "X", latexAvailable: true, suggestedTags: ["distillation"] }}
      onSave={onSave}
    />,
  );

  fireEvent.click(screen.getByRole("radio", { name: "すぐ読む" }));
  fireEvent.click(screen.getByRole("button", { name: /提案: distillation/ }));
  fireEvent.change(screen.getByLabelText("ひとことメモ"), { target: { value: "後で読む" } });
  fireEvent.click(screen.getByRole("button", { name: /保存/ }));

  expect(onSave).toHaveBeenCalledWith({
    status: "up_next",
    tags: ["distillation"],
    quickNote: "後で読む",
  });
});
