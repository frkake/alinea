import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, test, vi } from "vitest";
import { QuickFilterBar } from "@/components/library/QuickFilterBar";

const facets = { all: 41, unread: 12, in_progress: 4, done: 23, recheck: 2 };

// VT-LIB-01(クイックフィルタ部)
describe("QuickFilterBar", () => {
  test("renders 5 quick filters with facet counts", () => {
    render(<QuickFilterBar facets={facets} quick="all" onQuickChange={() => {}} />);
    for (const label of ["すべて", "未読", "途中", "読了", "要再確認"]) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
    expect(screen.getByText("41")).toBeInTheDocument();
    expect(screen.getByText("12")).toBeInTheDocument();
    expect(screen.getByText("2")).toBeInTheDocument();
  });

  test("omits counts while facets are loading", () => {
    render(<QuickFilterBar facets={undefined} quick="all" onQuickChange={() => {}} />);
    expect(screen.getByText("すべて")).toBeInTheDocument();
    expect(screen.queryByText("41")).not.toBeInTheDocument();
  });

  test("clicking a chip changes the quick filter", async () => {
    const user = userEvent.setup();
    const onQuickChange = vi.fn();
    render(<QuickFilterBar facets={facets} quick="all" onQuickChange={onQuickChange} />);
    await user.click(screen.getByText("途中"));
    expect(onQuickChange).toHaveBeenCalledWith("in_progress");
  });
});
