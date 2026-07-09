import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, test, vi } from "vitest";
import type { LibraryItemSummary } from "@alinea/api-client";
import { ContinueReading, formatRelativeDay } from "@/components/library/ContinueReading";

function makeItem(overrides: Partial<LibraryItemSummary> = {}): LibraryItemSummary {
  return {
    id: "li_1",
    paper: {
      id: "pap_1",
      title: "Flow Straight and Fast",
      authors: ["Xingchao Liu"],
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
    progress_pct: 42,
    comprehension: null,
    reading_seconds_total: 0,
    last_position: {
      revision_id: "rev_1",
      block_id: "blk_1",
      mode: "translation",
      section_display: "§2.1 整流フロー",
      saved_at: "2026-07-01T00:00:00Z",
    },
    added_at: "2026-07-02T00:00:00Z",
    updated_at: "2026-07-02T00:00:00Z",
    ...overrides,
  };
}

// VT-LIB-03 隣接(1d ContinueReading)
describe("ContinueReading", () => {
  test("renders up to 3 cards with progress and last position", () => {
    render(<ContinueReading items={[makeItem()]} onOpen={() => {}} />);
    expect(screen.getByText("Flow Straight and Fast")).toBeInTheDocument();
    expect(screen.getByText(/前回: §2.1 整流フロー/)).toBeInTheDocument();
    expect(screen.getByText("再開 →")).toBeInTheDocument();
  });

  test("shows empty state with no items", () => {
    render(<ContinueReading items={[]} onOpen={() => {}} />);
    expect(screen.getByText("読みかけの論文はありません")).toBeInTheDocument();
  });

  test("clicking a card opens it", async () => {
    const user = userEvent.setup();
    const onOpen = vi.fn();
    render(<ContinueReading items={[makeItem()]} onOpen={onOpen} />);
    await user.click(screen.getByText("Flow Straight and Fast"));
    expect(onOpen).toHaveBeenCalledWith("li_1");
  });

  // mobile.md §5.2: 縦積み 1 カラム(カード幅 100%)。
  test("isMobile switches the card grid to a single column", () => {
    const { container } = render(
      <ContinueReading items={[makeItem()]} onOpen={() => {}} isMobile />,
    );
    expect(container.querySelector('[style*="grid-template-columns"]')).toHaveStyle({
      gridTemplateColumns: "1fr",
    });
  });
});

describe("formatRelativeDay", () => {
  const now = new Date("2026-07-08T12:00:00");

  test.each([
    ["2026-07-08T08:00:00", "今日"],
    ["2026-07-07T08:00:00", "昨日"],
    ["2026-07-06T08:00:00", "2日前"],
    ["2026-07-02T08:00:00", "6日前"],
    ["2026-07-01T08:00:00", "先週"],
    ["2026-06-25T08:00:00", "先週"],
    ["2026-06-24T08:00:00", "6/24"],
  ])("%s -> %s", (iso, expected) => {
    expect(formatRelativeDay(iso, now)).toBe(expected);
  });
});
