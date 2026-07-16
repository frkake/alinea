/**
 * VocabHeader テスト — Anki エクスポートボタン(TS-VOCAB-ANKI)
 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { VocabHeader } from "@/components/vocab/VocabHeader";

const defaultProps = {
  total: 5,
  dueCount: 2,
  searchValue: "",
  searchFetching: false,
  onSearchChange: vi.fn(),
  onStartReview: vi.fn(),
  reviewLoading: false,
  onAnkiExport: vi.fn(),
};

describe("VocabHeader — Anki export button (TS-VOCAB-ANKI)", () => {
  it("renders the Anki export button", () => {
    render(<VocabHeader {...defaultProps} />);
    expect(screen.getByRole("button", { name: /Anki/i })).toBeInTheDocument();
  });

  it("calls onAnkiExport when Anki button is clicked", async () => {
    const onAnkiExport = vi.fn();
    render(<VocabHeader {...defaultProps} onAnkiExport={onAnkiExport} />);
    await userEvent.click(screen.getByRole("button", { name: /Anki/i }));
    expect(onAnkiExport).toHaveBeenCalledOnce();
  });
});
