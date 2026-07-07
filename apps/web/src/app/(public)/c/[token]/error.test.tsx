import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, test, vi } from "vitest";
import ShareError from "./error";

describe("ShareError(§5.4)", () => {
  test("見出し・説明を表示し、再読み込みボタンで reset() を呼ぶ", () => {
    const reset = vi.fn();
    render(<ShareError error={new Error("boom")} reset={reset} />);
    expect(screen.getByText("ページを表示できません")).toBeInTheDocument();
    expect(screen.getByText(/一時的な問題が発生しました/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "再読み込み" }));
    expect(reset).toHaveBeenCalledTimes(1);
  });
});
