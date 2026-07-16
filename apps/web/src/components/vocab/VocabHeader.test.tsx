import { render, screen, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, test, vi } from "vitest";
import { VocabHeader } from "@/components/vocab/VocabHeader";

const BASE_PROPS = {
  total: 42,
  dueCount: 5,
  searchValue: "",
  searchFetching: false,
  onSearchChange: vi.fn(),
  onStartReview: vi.fn(),
  reviewLoading: false,
  onExportMarkdown: vi.fn(),
  onAnkiExport: vi.fn(),
};

// VT-S5-01 / VT-S5-02: エクスポートボタン
describe("VocabHeader — export button (S5)", () => {
  test("VT-S5-01: export button is rendered", () => {
    render(<VocabHeader {...BASE_PROPS} />);
    expect(
      screen.getByRole("button", { name: "エクスポート (.md)" }),
    ).toBeInTheDocument();
  });

  test("VT-S5-02: clicking the button calls onExportMarkdown once", () => {
    const onExportMarkdown = vi.fn();
    render(<VocabHeader {...BASE_PROPS} onExportMarkdown={onExportMarkdown} />);
    fireEvent.click(screen.getByRole("button", { name: "エクスポート (.md)" }));
    expect(onExportMarkdown).toHaveBeenCalledTimes(1);
  });
});

describe("VocabHeader — Anki export button (TS-VOCAB-ANKI)", () => {
  it("renders the Anki export button", () => {
    render(<VocabHeader {...BASE_PROPS} />);
    expect(screen.getByRole("button", { name: /Anki/i })).toBeInTheDocument();
  });

  it("calls onAnkiExport when Anki button is clicked", async () => {
    const onAnkiExport = vi.fn();
    render(<VocabHeader {...BASE_PROPS} onAnkiExport={onAnkiExport} />);
    await userEvent.click(screen.getByRole("button", { name: /Anki/i }));
    expect(onAnkiExport).toHaveBeenCalledOnce();
  });
});
