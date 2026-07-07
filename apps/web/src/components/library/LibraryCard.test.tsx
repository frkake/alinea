import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, test, vi } from "vitest";
import { libraryItemsUpdate, type LibraryItemSummary } from "@yakudoku/api-client";
import { LibraryCard } from "@/components/library/LibraryCard";
import { useFinishReadingStore } from "@/components/library/finishReadingStore";

vi.mock("@yakudoku/api-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@yakudoku/api-client")>();
  return { ...actual, libraryItemsUpdate: vi.fn() };
});

function makeItem(overrides: Partial<LibraryItemSummary> = {}): LibraryItemSummary {
  return {
    id: "li_1",
    paper: {
      id: "pap_1",
      title: "Flow Straight and Fast",
      authors: ["Xingchang Liu"],
      authors_short: "Liu",
      venue: "ICLR 2023",
      year: 2023,
      arxiv_id: "2209.03003",
      license: "cc-by",
      visibility: "public",
      abstract: "",
    },
    status: "reading",
    priority: null,
    deadline: null,
    tags: [],
    suggested_tags: [],
    quality_level: "A",
    source: "arxiv",
    progress_pct: 40,
    comprehension: null,
    reading_seconds_total: 100,
    added_at: "2026-07-02T00:00:00Z",
    updated_at: "2026-07-02T00:00:00Z",
    ...overrides,
  };
}

function renderCard(item: LibraryItemSummary, onOpen = vi.fn()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return {
    onOpen,
    ...render(
      <QueryClientProvider client={client}>
        <LibraryCard item={item} onOpen={onOpen} />
      </QueryClientProvider>,
    ),
  };
}

// M1-06: LibraryCard の StatusPill 経由の起動配線(1g §2.3)。
describe("LibraryCard status change wiring (M1-06)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useFinishReadingStore.setState({ item: null });
  });

  test("changing status via the pill does not trigger the card's onOpen navigation", async () => {
    const user = userEvent.setup();
    vi.mocked(libraryItemsUpdate).mockResolvedValue({
      data: makeItem({ status: "on_hold" }),
    } as never);
    const { onOpen } = renderCard(makeItem({ status: "reading" }));

    await user.click(screen.getByRole("button", { name: /読んでいる/ }));
    await user.click(screen.getByRole("menuitemradio", { name: "保留" }));

    await waitFor(() => expect(libraryItemsUpdate).toHaveBeenCalledTimes(1));
    expect(libraryItemsUpdate).toHaveBeenCalledWith({
      path: { item_id: "li_1" },
      body: { status: "on_hold" },
      throwOnError: true,
    });
    expect(onOpen).not.toHaveBeenCalled();
  });

  test("changing status to done opens the finish-reading dialog with the PATCH response", async () => {
    const user = userEvent.setup();
    const doneItem = makeItem({ status: "done", finished_at: "2026-07-08T00:00:00Z" });
    vi.mocked(libraryItemsUpdate).mockResolvedValue({ data: doneItem } as never);
    renderCard(makeItem({ status: "reading" }));

    await user.click(screen.getByRole("button", { name: /読んでいる/ }));
    await user.click(screen.getByRole("menuitemradio", { name: "読んだ" }));

    await waitFor(() => expect(useFinishReadingStore.getState().item).toEqual(doneItem));
  });

  test("changing status away from done again does not reopen the dialog", async () => {
    const user = userEvent.setup();
    vi.mocked(libraryItemsUpdate).mockResolvedValue({
      data: makeItem({ status: "reread" }),
    } as never);
    renderCard(makeItem({ status: "done" }));

    await user.click(screen.getByRole("button", { name: /読んだ/ }));
    await user.click(screen.getByRole("menuitemradio", { name: "あとで再読" }));

    await waitFor(() => expect(libraryItemsUpdate).toHaveBeenCalledTimes(1));
    expect(useFinishReadingStore.getState().item).toBeNull();
  });
});
