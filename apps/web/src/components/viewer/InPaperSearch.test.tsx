import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, test, vi } from "vitest";
import { searchInPaper, type InPaperSearchItem } from "@yakudoku/api-client";
import { InPaperSearch } from "@/components/viewer/InPaperSearch";
import { useViewerStore } from "@/stores/viewer-store";

vi.mock("@yakudoku/api-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@yakudoku/api-client")>();
  return { ...actual, searchInPaper: vi.fn() };
});

function hit(overrides: Partial<InPaperSearchItem> = {}): InPaperSearchItem {
  return {
    block_id: "blk-1",
    section_id: "sec-1",
    display: "§2.2 ¶3",
    matched_in: ["source"],
    snippet: "整流フローは直線経路で結ぶ ODE モデルである。",
    ...overrides,
  };
}

function renderWithClient(ui: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

describe("InPaperSearch (viewer-shell §7 / 1b §5.8)", () => {
  beforeEach(() => {
    useViewerStore.setState({ revisionId: "rev_1", requestScroll: vi.fn() });
    vi.clearAllMocks();
  });

  test("does not query below the 2-character minimum", async () => {
    renderWithClient(<InPaperSearch />);
    const input = screen.getByPlaceholderText("この論文内を検索");
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: "整" } });
    await act(async () => {
      await new Promise((r) => setTimeout(r, 350));
    });
    expect(searchInPaper).not.toHaveBeenCalled();
  });

  test("debounced query returns results with display + snippet, `/` focuses, Enter jumps", async () => {
    vi.mocked(searchInPaper).mockResolvedValue({ data: { items: [hit()] } } as never);
    const requestScroll = vi.fn();
    useViewerStore.setState({ requestScroll });
    renderWithClient(<InPaperSearch />);

    // `/` フォーカス(入力要素外にいる状態から)。
    fireEvent.keyDown(window, { key: "/" });
    const input = screen.getByPlaceholderText("この論文内を検索");
    expect(input).toHaveFocus();

    fireEvent.change(input, { target: { value: "整流" } });
    await waitFor(() => expect(searchInPaper).toHaveBeenCalled(), { timeout: 1000 });
    expect(await screen.findByText("§2.2 ¶3", {}, { timeout: 1000 })).toBeInTheDocument();

    fireEvent.keyDown(input, { key: "Enter" });
    expect(requestScroll).toHaveBeenCalledWith({ kind: "block", blockId: "blk-1" });
  });

  test("no hits shows 一致なし", async () => {
    vi.mocked(searchInPaper).mockResolvedValue({ data: { items: [] } } as never);
    renderWithClient(<InPaperSearch />);
    const input = screen.getByPlaceholderText("この論文内を検索");
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: "存在しない語" } });
    await waitFor(() => expect(searchInPaper).toHaveBeenCalled(), { timeout: 1000 });
    expect(await screen.findByText("一致なし", {}, { timeout: 1000 })).toBeInTheDocument();
  });

  test("Escape closes the dropdown without bubbling to the window listener", async () => {
    vi.mocked(searchInPaper).mockResolvedValue({ data: { items: [hit()] } } as never);
    renderWithClient(<InPaperSearch />);
    const input = screen.getByPlaceholderText("この論文内を検索");
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: "整流" } });
    await screen.findByText("§2.2 ¶3", {}, { timeout: 1000 });
    fireEvent.keyDown(input, { key: "Escape" });
    await waitFor(() => expect(screen.queryByText("§2.2 ¶3")).toBeNull());
  });
});
