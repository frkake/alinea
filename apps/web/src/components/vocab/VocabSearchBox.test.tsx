import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { VocabSearchBox } from "@/components/vocab/VocabSearchBox";

// VT-VOC-02: VocabSearchBox — 語彙帳内検索がグローバル検索(⌘K)と独立(store 分離)。
describe("VocabSearchBox (VT-VOC-02)", () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  test("renders the dedicated placeholder distinct from the global search box", () => {
    render(<VocabSearchBox value="" onChange={vi.fn()} />);
    expect(screen.getByPlaceholderText("語彙を検索")).toBeInTheDocument();
  });

  test("debounces onChange by 200ms after typing stops", async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    const onChange = vi.fn();
    render(<VocabSearchBox value="" onChange={onChange} />);
    const input = screen.getByPlaceholderText("語彙を検索");

    await user.type(input, "boil");
    expect(onChange).not.toHaveBeenCalled();

    await act(async () => {
      vi.advanceTimersByTime(200);
    });
    expect(onChange).toHaveBeenCalledWith("boil");
  });

  test("shows a spinner only while fetching is true", () => {
    const { rerender } = render(<VocabSearchBox value="" onChange={vi.fn()} fetching={false} />);
    expect(screen.queryByTestId("vocab-search-spinner")).not.toBeInTheDocument();
    rerender(<VocabSearchBox value="" onChange={vi.fn()} fetching />);
    expect(screen.getByTestId("vocab-search-spinner")).toBeInTheDocument();
  });

  test("two independent instances do not share input state (no global store)", async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    render(
      <>
        <VocabSearchBox value="" onChange={vi.fn()} />
        <VocabSearchBox value="" onChange={vi.fn()} />
      </>,
    );
    const [first, second] = screen.getAllByPlaceholderText("語彙を検索");
    if (!first || !second) throw new Error("expected two search boxes");
    await user.type(first, "hinge on");
    expect(first).toHaveValue("hinge on");
    expect(second).toHaveValue("");
  });
});
