import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, test, vi } from "vitest";
import type { LibraryItemSummary } from "@yakudoku/api-client";
import { UpNextQueue, useDashboardUiStore } from "@/components/library/UpNextQueue";

function makeItem(id: string, overrides: Partial<LibraryItemSummary> = {}): LibraryItemSummary {
  return {
    id,
    paper: {
      id: `pap_${id}`,
      title: `Paper ${id}`,
      authors: ["Alice Liu"],
      authors_short: "Liu",
      venue: "ICLR 2023",
      year: 2023,
      arxiv_id: "2209.03003",
      license: "cc-by",
      visibility: "public",
      abstract: "",
    },
    status: "up_next",
    priority: null,
    deadline: null,
    tags: [],
    suggested_tags: [],
    quality_level: "A",
    source: "arxiv",
    progress_pct: 0,
    comprehension: null,
    reading_seconds_total: 0,
    added_at: "2026-07-02T00:00:00Z",
    updated_at: "2026-07-02T00:00:00Z",
    ...overrides,
  };
}

function makeItems(count: number): LibraryItemSummary[] {
  return Array.from({ length: count }, (_, i) => makeItem(String(i + 1)));
}

// VT-LIB-03: UpNextQueue — 6件で「積みすぎかも?」バナー表示・閉じられる
describe("UpNextQueue", () => {
  beforeEach(() => {
    window.localStorage.clear();
    useDashboardUiStore.setState({ queueWarnDismissedCount: null });
  });

  test("hides the warning banner with 5 or fewer items", () => {
    render(
      <UpNextQueue items={makeItems(5)} onOpen={() => {}} onReorder={() => {}} onOrganize={() => {}} />,
    );
    expect(screen.queryByText(/積みすぎかも/)).not.toBeInTheDocument();
  });

  test("shows the warning banner with 6 or more items", () => {
    render(
      <UpNextQueue items={makeItems(6)} onOpen={() => {}} onReorder={() => {}} onOrganize={() => {}} />,
    );
    expect(screen.getByText("キューが 6 本になっています — 積みすぎかも?")).toBeInTheDocument();
  });

  test("dismissing the banner hides it and persists across remounts", async () => {
    const user = userEvent.setup();
    const { unmount } = render(
      <UpNextQueue items={makeItems(6)} onOpen={() => {}} onReorder={() => {}} onOrganize={() => {}} />,
    );
    await user.click(screen.getByRole("button", { name: "警告を閉じる" }));
    expect(screen.queryByText(/積みすぎかも/)).not.toBeInTheDocument();
    unmount();

    render(
      <UpNextQueue items={makeItems(6)} onOpen={() => {}} onReorder={() => {}} onOrganize={() => {}} />,
    );
    expect(screen.queryByText(/積みすぎかも/)).not.toBeInTheDocument();
  });

  test("re-shows the banner when the queue count changes after dismissal", async () => {
    const user = userEvent.setup();
    const { rerender } = render(
      <UpNextQueue items={makeItems(6)} onOpen={() => {}} onReorder={() => {}} onOrganize={() => {}} />,
    );
    await user.click(screen.getByRole("button", { name: "警告を閉じる" }));
    expect(screen.queryByText(/積みすぎかも/)).not.toBeInTheDocument();

    rerender(
      <UpNextQueue items={makeItems(7)} onOpen={() => {}} onReorder={() => {}} onOrganize={() => {}} />,
    );
    expect(screen.getByText("キューが 7 本になっています — 積みすぎかも?")).toBeInTheDocument();
  });

  test("clicking 整理する invokes onOrganize", async () => {
    const user = userEvent.setup();
    const onOrganize = vi.fn();
    render(
      <UpNextQueue items={makeItems(6)} onOpen={() => {}} onReorder={() => {}} onOrganize={onOrganize} />,
    );
    await user.click(screen.getByText("整理する"));
    expect(onOrganize).toHaveBeenCalledTimes(1);
  });

  test("moving a row up reorders and calls onReorder with the full new order", async () => {
    const user = userEvent.setup();
    const onReorder = vi.fn();
    render(
      <UpNextQueue items={makeItems(3)} onOpen={() => {}} onReorder={onReorder} onOrganize={() => {}} />,
    );
    const upButtons = screen.getAllByRole("button", { name: "上へ移動" });
    // 2 行目(index 1)を上へ
    await user.click(upButtons[1] as HTMLElement);
    expect(onReorder).toHaveBeenCalledTimes(1);
    const newOrder = onReorder.mock.calls[0]?.[0] as LibraryItemSummary[];
    expect(newOrder.map((i) => i.id)).toEqual(["2", "1", "3"]);
  });

  test("renders empty state when the queue has no items", () => {
    render(<UpNextQueue items={[]} onOpen={() => {}} onReorder={() => {}} onOrganize={() => {}} />);
    expect(screen.getByText("すぐ読むキューは空です")).toBeInTheDocument();
    expect(screen.queryByText(/積みすぎかも/)).not.toBeInTheDocument();
  });

  test("opening a row invokes onOpen with the item id", async () => {
    const user = userEvent.setup();
    const onOpen = vi.fn();
    render(
      <UpNextQueue items={makeItems(2)} onOpen={onOpen} onReorder={() => {}} onOrganize={() => {}} />,
    );
    await user.click(screen.getByText("Paper 1"));
    expect(onOpen).toHaveBeenCalledWith("1");
  });
});
