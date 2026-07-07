import { render, screen, fireEvent } from "@testing-library/react";
import { describe, expect, test, vi } from "vitest";
import { PdfToolbar, type PdfToolbarProps } from "./PdfToolbar";

function baseProps(overrides: Partial<PdfToolbarProps> = {}): PdfToolbarProps {
  return {
    page: 5,
    pageCount: 24,
    zoomPct: 128,
    fitMode: "fit-width",
    spread: false,
    syncDisplay: "§2.2 Reflow",
    loading: false,
    onPageChange: vi.fn(),
    onZoomIn: vi.fn(),
    onZoomOut: vi.fn(),
    onFitModeChange: vi.fn(),
    onToggleSpread: vi.fn(),
    onOpenInTranslation: vi.fn(),
    ...overrides,
  };
}

// PY→VT bridging (2a §4.2.3): ツールバー逐語・ページ入力・ズーム・同期インジケータ。
describe("PdfToolbar (2a §4.2.3)", () => {
  test("renders page nav, zoom, sync indicator, and cross-link button verbatim", () => {
    render(<PdfToolbar {...baseProps()} />);
    expect(screen.getByDisplayValue("5")).toBeInTheDocument();
    expect(screen.getByText("/ 24")).toBeInTheDocument();
    expect(screen.getByText("128%")).toBeInTheDocument();
    expect(screen.getByText("幅に合わせる")).toBeInTheDocument();
    expect(screen.getByText("見開き")).toBeInTheDocument();
    expect(screen.getByText("§2.2 Reflow")).toBeInTheDocument();
    expect(screen.getByText("この位置を訳文で開く →")).toBeInTheDocument();
  });

  test("shows — % while loading and 同期: — when syncDisplay is null", () => {
    render(<PdfToolbar {...baseProps({ zoomPct: null, syncDisplay: null, loading: true })} />);
    expect(screen.getByText("—%")).toBeInTheDocument();
    expect(screen.getByText(/同期:/)).toBeInTheDocument();
    expect(screen.queryByText("§2.2 Reflow")).toBeNull();
  });

  test("page input commits on Enter within range and reverts out-of-range values", () => {
    const onPageChange = vi.fn();
    render(<PdfToolbar {...baseProps({ onPageChange })} />);
    const input = screen.getByLabelText("ページ番号");
    fireEvent.change(input, { target: { value: "12" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onPageChange).toHaveBeenCalledWith(12);

    fireEvent.change(input, { target: { value: "999" } });
    fireEvent.blur(input);
    expect(onPageChange).not.toHaveBeenCalledWith(999);
    expect(input).toHaveValue("5");
  });

  test("‹/› buttons step by 1 and clamp at the edges", () => {
    const onPageChange = vi.fn();
    render(<PdfToolbar {...baseProps({ page: 24, pageCount: 24, onPageChange })} />);
    expect(screen.getByLabelText("次のページ")).toBeDisabled();
    fireEvent.click(screen.getByLabelText("前のページ"));
    expect(onPageChange).toHaveBeenCalledWith(23);
  });

  test("zoom and spread controls fire their callbacks", () => {
    const onZoomIn = vi.fn();
    const onZoomOut = vi.fn();
    const onToggleSpread = vi.fn();
    render(<PdfToolbar {...baseProps({ onZoomIn, onZoomOut, onToggleSpread })} />);
    fireEvent.click(screen.getByLabelText("拡大"));
    fireEvent.click(screen.getByLabelText("縮小"));
    fireEvent.click(screen.getByText("見開き"));
    expect(onZoomIn).toHaveBeenCalledTimes(1);
    expect(onZoomOut).toHaveBeenCalledTimes(1);
    expect(onToggleSpread).toHaveBeenCalledTimes(1);
  });

  test("fit selector opens a popover with the 3 decided options and applies a choice", () => {
    const onFitModeChange = vi.fn();
    render(<PdfToolbar {...baseProps({ onFitModeChange })} />);
    fireEvent.click(screen.getByText("幅に合わせる"));
    expect(screen.getByText("ページ全体")).toBeInTheDocument();
    expect(screen.getByText("実寸(100%)")).toBeInTheDocument();
    fireEvent.click(screen.getByText("実寸(100%)"));
    expect(onFitModeChange).toHaveBeenCalledWith("actual");
  });

  test("cross-link button is disabled while loading", () => {
    render(<PdfToolbar {...baseProps({ loading: true })} />);
    expect(screen.getByText("この位置を訳文で開く →")).toBeDisabled();
  });
});
