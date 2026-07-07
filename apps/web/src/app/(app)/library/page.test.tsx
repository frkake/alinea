import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";
import type { LibraryItemSummary } from "@yakudoku/api-client";
import LibraryPage from "./page";
import { mockMatchMedia } from "@/test-utils/mockMatchMedia";

const push = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push }),
}));

const libraryItemsFacets = vi.fn();
const libraryItemsList = vi.fn();
vi.mock("@yakudoku/api-client", () => ({
  libraryItemsFacets: (...args: unknown[]) => libraryItemsFacets(...args),
  libraryItemsList: (...args: unknown[]) => libraryItemsList(...args),
}));

function makeItem(id: string): LibraryItemSummary {
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
    status: "reading",
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
  };
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <LibraryPage />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

// mobile.md §5.1: モバイルはカードビューのみ・1 カラム。ビュー切替(操作系)は非描画。
describe("LibraryPage mobile reduction (mobile.md §5.1)", () => {
  test("desktop: defaults to the table view and shows the view switch", async () => {
    mockMatchMedia(false);
    libraryItemsFacets.mockResolvedValue({ data: { quick: { all: 1 } } });
    libraryItemsList.mockResolvedValue({ data: { items: [makeItem("1")] } });
    renderPage();

    await waitFor(() => expect(screen.getByText("Paper 1")).toBeInTheDocument());
    expect(screen.getByRole("radiogroup", { name: "表示形式" })).toBeInTheDocument();
    // テーブルビュー既定: LibraryTable のチェックボックス列が存在する。
    expect(screen.getByRole("checkbox", { name: "すべて選択" })).toBeInTheDocument();
  });

  test("mobile: forces the card view (no table checkboxes) and hides the view switch", async () => {
    mockMatchMedia(true);
    libraryItemsFacets.mockResolvedValue({ data: { quick: { all: 1 } } });
    libraryItemsList.mockResolvedValue({ data: { items: [makeItem("1")] } });
    renderPage();

    await waitFor(() => expect(screen.getByText("Paper 1")).toBeInTheDocument());
    expect(screen.queryByRole("radiogroup", { name: "表示形式" })).toBeNull();
    expect(screen.queryByRole("checkbox", { name: "すべて選択" })).toBeNull();
  });
});
